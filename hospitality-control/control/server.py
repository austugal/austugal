"""Local HTTP application. Use a hardened TLS reverse proxy for any private pilot."""
from contextlib import closing
import argparse
import json
import os
import re
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .assistant import OpenAIPlanner, draft
from .store import Store, Fault, fields, require

STATIC=Path(__file__).resolve().parent.parent/'static'


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,store,origin=None,planner=None):
        super().__init__(address,Handler)
        self.store,self.planner=store,planner
        port=self.server_address[1]
        self.allowed_hosts={f'localhost:{port}',f'127.0.0.1:{port}'}
        self.origins={f'http://{host}' for host in self.allowed_hosts}
        if origin:
            parts=urlsplit(origin)
            require(parts.scheme in ('http','https') and parts.hostname and not parts.username and not parts.password and not parts.query and not parts.fragment and parts.path in ('','/'),'invalid_origin','Origin must be a plain HTTP(S) origin; use HTTPS for any remote deployment.')
            self.allowed_hosts.add(parts.netloc); self.origins.add(origin.rstrip('/'))
        self.slots=threading.BoundedSemaphore(24)

    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            request.close(); return
        try: super().process_request(request,client_address)
        except Exception:
            self.slots.release(); raise

    def process_request_thread(self,request,client_address):
        try: super().process_request_thread(request,client_address)
        finally: self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version='AustugalControl/1.0'
    def setup(self):
        super().setup(); self.connection.settimeout(30)
    def log_message(self,*args):
        pass  # Do not log tokens, submitted text, or guest data.
    def send(self,status,data,content_type='application/json; charset=utf-8'):
        body=json.dumps(data).encode() if isinstance(data,(dict,list)) else data
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers(); self.wfile.write(body)
    def do_GET(self): self.handle_request('GET')
    def do_POST(self): self.handle_request('POST')
    def handle_request(self,method):
        actor=None
        try:
            require(self.headers.get('Host') in self.server.allowed_hosts,'invalid_host','Host is not allowed.',403)
            origin=self.headers.get('Origin')
            require(not origin or origin in self.server.origins,'invalid_origin','Cross-origin requests are not allowed.',403)
            require(self.headers.get('Sec-Fetch-Site')!='cross-site','invalid_origin','Cross-site requests are not allowed.',403)
            path=urlsplit(self.path).path
            if method=='GET' and path in ('/','/app.js','/style.css'):
                name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[path]
                mime={'html':'text/html','js':'text/javascript','css':'text/css'}[name.rsplit('.',1)[1]]
                return self.send(200,(STATIC/name).read_bytes(),mime+'; charset=utf-8')
            if method=='GET' and path=='/api/health': return self.send(200,{'status':'ok','version':'1.0.0'})
            data={}
            if method=='POST':
                require(self.headers.get_content_type()=='application/json','content_type','Use application/json.',415)
                require(not self.headers.get('Transfer-Encoding'),'invalid_body','Chunked requests are not supported.')
                try: length=int(self.headers.get('Content-Length','0'))
                except ValueError: raise Fault('invalid_body','Invalid content length.') from None
                require(0<length<=16384,'body_size','Request body must be between 1 and 16384 bytes.',413)
                raw=self.rfile.read(length)
                require(len(raw)==length,'invalid_body','Incomplete request body.')
                try: data=json.loads(raw)
                except (ValueError,UnicodeDecodeError): raise Fault('invalid_json','Invalid JSON.') from None
                require(isinstance(data,dict),'invalid_json','Request body must be an object.')
            store=self.server.store
            if method=='GET' and path=='/api/bootstrap':
                with closing(store.connect()) as db: mode=db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()[0]
                return self.send(200,{'demo':mode=='demo','ai_configured':self.server.planner is not None})
            if method=='POST' and path=='/api/login':
                fields(data,['username','password'])
                return self.send(200,store.login(data['username'],data['password'],self.client_address[0]))
            auth=self.headers.get('Authorization','')
            require(auth.startswith('Bearer ') and len(auth)<200,'unauthorized','Sign in to continue.',401)
            token=auth[7:]; actor=store.authenticate(token)
            if method=='GET' and path=='/api/state': result=store.state(actor)
            elif method=='GET' and path=='/api/audit': result=store.audit_export(actor)
            elif method=='POST' and path=='/api/logout':
                fields(data,[]); store.logout(token); result={'ok':True}
            elif method=='POST' and path=='/api/assistant': result=draft(store,actor,data,self.server.planner)
            elif method=='POST' and path=='/api/proposals': result=store.propose(actor,data)
            elif method=='POST' and re.fullmatch(r'/api/proposals/P-[a-f0-9]{12}/(review|execute)',path):
                _,_,_,pid,action=path.split('/')
                if action=='review': result=store.review(actor,pid,data)
                else:
                    fields(data,[]); result=store.execute(actor,pid)
            elif method=='POST' and path=='/api/policy': result=store.policy(actor,data)
            elif method=='POST' and re.fullmatch(r'/api/tasks/T-[a-f0-9]{12}',path): result=store.task_update(actor,path.split('/')[-1],data)
            elif method=='POST' and path=='/api/events': result=store.enqueue(actor,data)
            elif method=='POST' and path=='/api/events/process':
                fields(data,[]); result=store.process_events(actor)
            else: raise Fault('not_found','Route not found.',404)
            self.send(200,result)
        except Fault as exc:
            if actor and exc.status!=401:
                with self.server.store.tx() as db:
                    self.server.store.audit(db,actor,'request.denied',detail={'code':exc.code})
            self.send(exc.status,{'error':{'code':exc.code,'message':exc.message}})
        except (BrokenPipeError,ConnectionResetError,TimeoutError):
            return
        except sqlite3.OperationalError:
            self.send(503,{'error':{'code':'storage_unavailable','message':'Storage is temporarily unavailable. Retry with the same request key.'}})
        except Exception:
            self.send(500,{'error':{'code':'internal_error','message':'Unexpected server error. No success is assumed; refresh the current state.'}})


def main():
    parser=argparse.ArgumentParser(description='Austugal Control — local hotel operations')
    parser.add_argument('--data',default='./data'); parser.add_argument('--port',type=int,default=8080)
    parser.add_argument('--host',default='127.0.0.1'); parser.add_argument('--origin',default=os.getenv('CONTROL_ORIGIN'))
    parser.add_argument('--demo',action='store_true',help='Synthetic hotels and public demo passwords; loopback only')
    args=parser.parse_args()
    if args.demo and args.host not in ('127.0.0.1','localhost'):
        parser.error('Demo mode can only bind to loopback. Do not expose demo credentials publicly.')
    store=Store(args.data)
    credentials=store.seed(demo=args.demo)
    key,model=os.getenv('OPENAI_API_KEY'),os.getenv('OPENAI_MODEL')
    if bool(key)!=bool(model): parser.error('Set both OPENAI_API_KEY and OPENAI_MODEL, or neither.')
    planner=OpenAIPlanner(key,model) if key else None
    server=Server((args.host,args.port),store,args.origin,planner)
    if credentials:
        print('Initial credentials (save privately; shown once):',flush=True)
        for username,password in credentials.items(): print(f'  {username}: {password}',flush=True)
    print(f'Austugal Control: http://{args.host}:{server.server_port}',flush=True)
    print('Synthetic hotel backend. No external PMS connected. AI planner: '+('configured; live access not verified' if planner else 'off; local commands available'),flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__=='__main__': main()
