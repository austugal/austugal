"""Proposal drafting only. Neither planner has execution privileges."""
import json
import os
import re
import urllib.error
import urllib.request
from .store import Fault, fields, require, string

INTENT_SCHEMA = {
    'type':'object','additionalProperties':False,
    'properties':{
        'intent':{'type':'string','enum':['late_checkout','room_upgrade','service_task','policy','unknown']},
        'reservation_id':{'type':['string','null']},
        'hour':{'type':['integer','null']},
        'room':{'type':['string','null']},
        'category':{'type':['string','null'],'enum':['housekeeping','maintenance',None]},
        'description':{'type':['string','null']}
    },
    'required':['intent','reservation_id','hour','room','category','description']
}


def local_intent(message):
    text=message.strip()
    if re.fullmatch(r'(show )?(hotel )?(policy|policies|rules)\??',text,re.I):
        return {'intent':'policy'}
    late=re.fullmatch(r'(?:late checkout|extend checkout)(?: for)? (R\d+) (?:at|to|until) (1[2-9]|2[0-3])(?::00)?',text,re.I)
    if late:
        return {'intent':'late_checkout','reservation_id':late[1].upper(),'hour':int(late[2])}
    upgrade=re.fullmatch(r'upgrade (R\d+) to(?: room| suite)? (\d+)',text,re.I)
    if upgrade:
        return {'intent':'room_upgrade','reservation_id':upgrade[1].upper(),'room':upgrade[2]}
    task=re.fullmatch(r'(housekeeping|maintenance)(?: for)? (R\d+):\s*(.{1,500})',text,re.I)
    if task:
        return {'intent':'service_task','reservation_id':task[2].upper(),'category':task[1].lower(),'description':task[3]}
    return {'intent':'unknown'}


class OpenAIPlanner:
    def __init__(self, key, model, transport=urllib.request.urlopen):
        self.key,self.model,self.transport=key,model,transport

    def plan(self, message):
        body={'model':self.model,'store':False,
              'instructions':'Extract one hotel action or a policy question. Never execute actions. Never invent reservation IDs, room IDs, hours, or task descriptions. If information is missing, instructions conflict, or multiple actions are requested, return unknown. Hotel tasks are housekeeping or maintenance. All fields must be present; use null for inapplicable values.',
              'input':message,'max_output_tokens':700,
              'text':{'format':{'type':'json_schema','name':'hotel_intent','strict':True,'schema':INTENT_SCHEMA}}}
        request=urllib.request.Request('https://api.openai.com/v1/responses',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'},method='POST')
        try:
            with self.transport(request,timeout=20) as response:
                raw=response.read(65537)
            require(len(raw)<=65536,'provider_error','Provider response exceeded its limit.',502)
            document=json.loads(raw)
            require(document.get('status')=='completed','provider_error','The model did not complete a usable response.',502)
            texts=[c['text'] for item in document.get('output',[]) if item.get('type')=='message' for c in item.get('content',[]) if c.get('type')=='output_text']
            require(len(texts)==1,'provider_error','The model returned no usable proposal.',502)
            result=json.loads(texts[0]); fields(result,INTENT_SCHEMA['required'])
            require(result['intent'] in INTENT_SCHEMA['properties']['intent']['enum'],'provider_error','Invalid model intent.',502)
            return result
        except Fault:
            raise
        except (urllib.error.URLError,TimeoutError,ValueError,KeyError,TypeError,AttributeError):
            raise Fault('provider_error','The AI provider is unavailable or returned invalid data. Use the action form or local commands.',502) from None


def draft(store,actor,data,planner=None):
    fields(data,['message','provider'])
    message=string(data['message'],1500)
    require(data['provider'] in ('local','openai'),'invalid_provider','Unknown planner.')
    if data['provider']=='openai':
        require(planner is not None,'provider_not_configured','AI provider is not configured. Local commands and action forms remain available.',503)
        store.consume_budget(actor)
        intent=planner.plan(message)
    else:
        intent=local_intent(message)
    kind=intent.get('intent')
    with store.tx() as db:
        actor=store.principal(db,actor)
        prop=store.property(db,actor)
        store.audit(db,actor,'assistant.drafted',detail={'provider':data['provider'],'intent':kind})
        if kind=='policy':
            return {'answer':f"Checkout extensions are allowed until {prop['max_checkout']}:00. The quote is {prop['checkout_fee']/100:.2f} {prop['currency']} per hour after 11:00. Suite upgrades in this sandbox are complimentary and require a ready, vacant suite. Every action needs a different manager's approval. Proposals expire after 30 minutes. Payments are not collected.",'source':f"Property policy v{prop['policy_version']}",'provider':data['provider']}
        if kind=='unknown':
            return {'answer':'Use one explicit request: “late checkout R1001 to 14”, “upgrade R1001 to room 201”, “housekeeping R1001: Bring towels”, or “show policy”. No changes were made.','provider':data['provider']}
        allowed={'guest_agent':{'late_checkout','service_task'},'upsell_agent':{'room_upgrade'}}
        require(actor['role'] not in allowed or kind in allowed[actor['role']],'forbidden','This agent cannot draft that action.',403)
        rid=string(intent.get('reservation_id'),40)
        if kind=='late_checkout': payload={'hour':intent.get('hour')}
        elif kind=='room_upgrade': payload={'room':intent.get('room')}
        elif kind=='service_task': payload={'category':intent.get('category'),'description':intent.get('description')}
        else: raise Fault('unsupported_action','Unsupported proposal.')
        store.validate_payload(kind,payload)
        reservation=store.reservation(db,actor,rid)
        preview=store.check(db,actor,prop,reservation,kind,payload)
        return {'answer':'Review the draft, then submit it for manager approval. No hotel record has changed.',
                'draft':{'kind':kind,'reservation_id':rid,'payload':payload},'preview':preview,'provider':data['provider']}
