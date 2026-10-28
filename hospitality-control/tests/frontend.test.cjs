// Unit tests for view generation and client request handling. No browser is launched.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const {webcrypto}=require('node:crypto');
function fixture(){
 const elements=new Map();
 const element=selector=>{if(!elements.has(selector))elements.set(selector,{textContent:'',innerHTML:'',hidden:false,className:'',value:'',addEventListener(){},focus(){},classList:{toggle(){}},setAttribute(){}});return elements.get(selector);};
 const calls=[];
 const context=vm.createContext({document:{querySelector:element,querySelectorAll:()=>[],addEventListener(){}},crypto:webcrypto,Uint8Array,Intl,Date,setTimeout:()=>1,clearTimeout(){},fetch:async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>({demo:false})};}});
 vm.runInContext(fs.readFileSync('static/app.js','utf8'),context);
 vm.runInContext(`state={user:{id:'lis-desk',role:'staff'},property:{id:'LIS',name:'Austugal Lisboa',currency:'EUR',paused:0,max_checkout:16,checkout_fee:2500,policy_version:1},reservations:[{id:'R1001',guest:'Test Guest',room:'101',category:'classic',checkout:11,version:1,status:'in_house'}],rooms:[{id:'101',category:'classic',ready:1,version:1},{id:'201',category:'suite',ready:1,version:1}],proposals:[],tasks:[],events:[]};`,context);
 return {context,element,calls,eval:code=>vm.runInContext(code,context)};
}
test('all six views render for every supported role',()=>{
 const f=fixture();
 for(const role of ['staff','manager','guest_agent','upsell_agent'])for(const view of ['overview','requests','operations','assistant','controls','audit']){
  f.eval(`state.user.role='${role}';view='${view}';render()`);
  assert.match(f.element('#main').innerHTML,/<h1>/);
 }
});
test('untrusted guest names and task text are escaped',()=>{
 const f=fixture();f.eval(`state.reservations[0].guest='<img src=x onerror=alert(1)>';state.tasks=[{id:'T-x',category:'housekeeping',reservation_id:'R1001',description:'<script>bad()</script>',status:'open',version:1}];view='operations';render()`);
 const html=f.element('#main').innerHTML;
 assert(!html.includes('<script>'));assert(!html.includes('<img'));assert(html.includes('&lt;script&gt;'));assert(html.includes('&lt;img'));
});
test('staff cannot see review or execute controls',()=>{
 const f=fixture();f.eval(`state.proposals=[{id:'P-a',kind:'late_checkout',reservation_id:'R1001',actor:'lis-concierge',payload:{hour:14},status:'approved',effective_status:'approved',created:1,expires:2,base_version:1,policy_version:1}];`);
 assert(!f.eval('requestList()').includes('data-execute'));
 f.eval("state.user.role='manager'");assert(f.eval('requestList()').includes('data-execute'));
});
test('own manager proposal has no self-review control',()=>{
 const f=fixture();f.eval(`state.user={id:'lis-manager',role:'manager'};state.proposals=[{id:'P-a',kind:'late_checkout',reservation_id:'R1001',actor:'lis-manager',payload:{hour:14},status:'pending',effective_status:'pending',created:1,expires:2,base_version:1,policy_version:1}];`);
 assert(!f.eval('requestList()').includes('data-review'));
});
test('action forms reflect agent permissions',()=>{
 const f=fixture();f.eval("state.user.role='upsell_agent'");let html=f.eval('actionForm()');assert(html.includes('room_upgrade'));assert(!html.includes('value="late_checkout"'));
 f.eval("state.user.role='guest_agent'");html=f.eval('actionForm()');assert(!html.includes('room_upgrade'));assert(html.includes('service_task'));
});
test('request keys work without secure-context randomUUID',()=>{
 const f=fixture();const values=new Set(Array.from({length:100},()=>f.eval('requestKey()')));assert.equal(values.size,100);assert.match([...values][0],/^[a-f0-9]{32}$/);
});
test('session reset removes cached audit and proposal data',()=>{
 const f=fixture();f.eval("token='old';auditResult={entries:[{property_id:'LIS'}]};draft={kind:'late_checkout'};pendingRequest={key:'x'};clearSession()");
 assert.equal(f.eval('token'),'');assert.equal(f.eval('state'),null);assert.equal(f.eval('auditResult'),null);assert.equal(f.eval('draft'),null);assert.equal(f.eval('pendingRequest'),null);
});
test('client rejects a stale response after account switch',async()=>{
 const f=fixture();let release;
 f.context.fetch=()=>new Promise(resolve=>{release=()=>resolve({ok:true,status:200,json:async()=>({property:'old'})});});
 f.eval("token='old'");const pending=f.eval("api('/state')");f.eval("token='new'");release();await assert.rejects(pending,/session changed/);
});
test('failed submission keeps the idempotency key for retry',async()=>{
 const f=fixture();f.context.fetch=async()=>{throw new Error('connection lost');};
 const body="{kind:'late_checkout',reservation_id:'R1001',payload:{hour:14}}";
 await assert.rejects(f.eval(`submitProposal(${body})`));const first=f.eval('pendingRequest.key');
 await assert.rejects(f.eval(`submitProposal(${body})`));assert.equal(f.eval('pendingRequest.key'),first);
});
test('fee display uses integer minor units and property currency',()=>{
 const f=fixture();assert.equal(f.eval('money(7500)'),'€75.00');f.eval("state.property.currency='AUD'");assert.match(f.eval('money(7500)'),/75.00/);
});
