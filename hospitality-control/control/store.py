"""Durable local hotel operations. All writes use serialized transactions."""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager, closing
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


class Fault(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def require(condition, code, message, status=400):
    if not condition:
        raise Fault(code, message, status)


def fields(data, required, optional=()):
    require(isinstance(data, dict) and set(required) <= data.keys() and
            data.keys() <= set(required) | set(optional), 'invalid_fields', 'Unexpected or missing fields.')


def string(value, maximum=200):
    require(isinstance(value, str) and 0 < len(value.strip()) <= maximum,
            'invalid_value', 'A non-empty text value is required.')
    return value.strip()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260000).hex()


SCHEMA = '''
CREATE TABLE IF NOT EXISTS properties(id TEXT PRIMARY KEY, name TEXT, currency TEXT,
 paused INTEGER NOT NULL DEFAULT 0, policy_version INTEGER NOT NULL DEFAULT 1,
 max_checkout INTEGER NOT NULL DEFAULT 16, checkout_fee INTEGER NOT NULL DEFAULT 2500);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, property_id TEXT REFERENCES properties,
 role TEXT NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, user_id TEXT REFERENCES users, expires REAL);
CREATE TABLE IF NOT EXISTS attempts(bucket TEXT, created REAL);
CREATE TABLE IF NOT EXISTS rooms(property_id TEXT REFERENCES properties, id TEXT, category TEXT,
 ready INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(property_id,id));
CREATE TABLE IF NOT EXISTS reservations(property_id TEXT REFERENCES properties, id TEXT, guest TEXT,
 room TEXT, category TEXT, status TEXT, checkout INTEGER DEFAULT 11, version INTEGER DEFAULT 1,
 PRIMARY KEY(property_id,id), FOREIGN KEY(property_id,room) REFERENCES rooms(property_id,id));
CREATE UNIQUE INDEX IF NOT EXISTS occupied_room ON reservations(property_id,room) WHERE status='in_house';
CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, property_id TEXT REFERENCES properties,
 actor TEXT REFERENCES users, kind TEXT, reservation_id TEXT, payload TEXT, base_version INTEGER,
 policy_version INTEGER, status TEXT, created REAL, expires REAL, reviewer TEXT, reason TEXT,
 idem TEXT, fingerprint TEXT, result TEXT, UNIQUE(property_id,actor,idem));
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, property_id TEXT REFERENCES properties,
 reservation_id TEXT, category TEXT, description TEXT, status TEXT, version INTEGER DEFAULT 1,
 proposal_id TEXT UNIQUE, created REAL);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, property_id TEXT REFERENCES properties,
 external_id TEXT, payload TEXT, fingerprint TEXT, status TEXT, reason TEXT, created REAL,
 UNIQUE(property_id,external_id));
CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, property_id TEXT,
 actor TEXT, action TEXT, target TEXT, detail TEXT, created REAL, previous TEXT, signature TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
'''


class Store:
    def __init__(self, directory, clock=time.time):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'control.sqlite3'
        keyfile = self.directory / 'audit.key'
        if not keyfile.exists():
            require(not self.path.exists(), 'missing_key', 'Restore audit.key with the database; refusing to replace it.', 500)
            try:
                with keyfile.open('xb') as f:
                    f.write(secrets.token_bytes(32))
                keyfile.chmod(0o600)
            except FileExistsError:
                pass
        self.key = keyfile.read_bytes()
        self.clock = clock
        with closing(self.connect()) as db:
            db.executescript(SCHEMA)
            db.execute('PRAGMA journal_mode=WAL')
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @contextmanager
    def tx(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def audit(self, db, actor, action, target='', detail=None):
        prop = actor['property_id']
        last = db.execute('SELECT signature FROM audit WHERE property_id=? ORDER BY seq DESC LIMIT 1', (prop,)).fetchone()
        entry = dict(property_id=prop, actor=actor['id'], action=action, target=target,
                     detail=canonical(detail or {}), created=self.clock(), previous=last[0] if last else '0'*64)
        entry['signature'] = hmac.new(self.key, canonical(entry).encode(), hashlib.sha256).hexdigest()
        db.execute('INSERT INTO audit(property_id,actor,action,target,detail,created,previous,signature) VALUES(:property_id,:actor,:action,:target,:detail,:created,:previous,:signature)', entry)

    def seed(self, demo=False):
        credentials = {}
        with self.tx() as db:
            mode = db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
            if mode:
                require((mode[0] == 'demo') == demo, 'mode_mismatch', 'Use a separate data directory for demo and private modes.')
                return credentials
            db.execute('INSERT INTO meta VALUES(?,?)', ('mode', 'demo' if demo else 'private'))
            for prop, name, currency in [('LIS','Austugal Lisboa','EUR'), ('MEL','Austugal Melbourne','AUD')]:
                db.execute('INSERT INTO properties(id,name,currency) VALUES(?,?,?)', (prop,name,currency))
                for room, category, ready in [('101','classic',1),('102','classic',1),('201','suite',1),('202','suite',0)]:
                    db.execute('INSERT INTO rooms(property_id,id,category,ready) VALUES(?,?,?,?)',(prop,room,category,ready))
                for rid, guest, room in [('R1001','Alex Morgan','101'),('R1002','Sam Taylor','102')]:
                    db.execute('INSERT INTO reservations(property_id,id,guest,room,category,status) VALUES(?,?,?,?,?,?)', (prop,rid,guest,room,'classic','in_house'))
                for role, suffix in [('manager','manager'),('staff','desk'),('guest_agent','concierge'),('upsell_agent','upsell')]:
                    uid = f'{prop.lower()}-{suffix}'
                    password = 'Austugal-demo-2026' if demo else secrets.token_urlsafe(18)
                    salt = secrets.token_hex(16)
                    db.execute('INSERT INTO users(id,property_id,role,salt,password) VALUES(?,?,?,?,?)', (uid,prop,role,salt,password_hash(password,salt)))
                    credentials[uid] = password
                self.audit(db, {'id':'system','property_id':prop}, 'hotel.initialized', detail={'synthetic':True})
        return credentials

    def login(self, username, password, ip='local'):
        require(isinstance(username,str) and isinstance(password,str) and len(username)<=80 and len(password)<=256,
                'invalid_credentials','Invalid credentials.',401)
        now = self.clock()
        bucket = hashlib.sha256((ip+':'+username.lower()).encode()).hexdigest()
        ip_bucket = 'ip:'+hashlib.sha256(ip.encode()).hexdigest()
        with self.tx() as db:
            db.execute('DELETE FROM attempts WHERE created<?',(now-300,))
            for b,limit in [(bucket,8),(ip_bucket,50)]:
                require(db.execute('SELECT count(*) FROM attempts WHERE bucket=?',(b,)).fetchone()[0] < limit,
                        'rate_limited','Too many login attempts. Try again in five minutes.',429)
            user = db.execute('SELECT * FROM users WHERE id=? AND active=1',(username,)).fetchone()
            valid = hmac.compare_digest(password_hash(password, user['salt'] if user else '0'*32), user['password'] if user else '0'*64)
            if not valid:
                db.executemany('INSERT INTO attempts VALUES(?,?)',[(bucket,now),(ip_bucket,now)])
            else:
                token = secrets.token_urlsafe(32)
                db.execute('DELETE FROM sessions WHERE expires<?',(now,))
                db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),username,now+3600))
                self.audit(db,user,'session.created')
        require(valid,'invalid_credentials','Invalid credentials.',401)
        return {'token':token,'expires':now+3600}

    def authenticate(self, token):
        with closing(self.connect()) as db:
            user = db.execute('SELECT u.id,u.property_id,u.role,u.active FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.hash=? AND s.expires>? AND u.active=1',
                              (hashlib.sha256(token.encode()).hexdigest(),self.clock())).fetchone()
        require(user is not None,'unauthorized','Sign in to continue.',401)
        return dict(user)

    def consume_budget(self, actor):
        """Bound authenticated remote planner use per user, persisted across restarts."""
        with self.tx() as db:
            actor=self.principal(db,actor)
            now=self.clock(); bucket='planner:'+actor['id']
            db.execute('DELETE FROM attempts WHERE created<?',(now-300,))
            count=db.execute('SELECT count(*) FROM attempts WHERE bucket=?',(bucket,)).fetchone()[0]
            require(count<20,'rate_limited','AI planner limit reached. Use local commands or wait five minutes.',429)
            db.execute('INSERT INTO attempts VALUES(?,?)',(bucket,now))

    def logout(self, token):
        with self.tx() as db:
            db.execute('DELETE FROM sessions WHERE hash=?',(hashlib.sha256(token.encode()).hexdigest(),))

    def principal(self, db, actor, roles=None):
        current = db.execute('SELECT id,property_id,role,active FROM users WHERE id=?',(actor['id'],)).fetchone()
        require(current is not None and current['active'] and current['property_id']==actor['property_id'], 'forbidden','Account is not active for this property.',403)
        require(roles is None or current['role'] in roles,'forbidden','This role cannot perform this action.',403)
        return dict(current)

    def property(self, db, actor, writable=False):
        prop = dict(db.execute('SELECT * FROM properties WHERE id=?',(actor['property_id'],)).fetchone())
        if writable:
            require(not prop['paused'],'paused','Hotel writes are paused by a manager.',423)
        return prop

    def reservation(self, db, actor, rid):
        row = db.execute('SELECT * FROM reservations WHERE property_id=? AND id=?',(actor['property_id'],rid)).fetchone()
        require(row is not None,'not_found','Reservation not found.',404)
        return dict(row)

    def state(self, actor):
        with self.tx() as db:
            actor = self.principal(db,actor)
            prop = self.property(db,actor)
            result = {'user':actor,'property':prop,'mode':db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()[0]}
            for table in ['reservations','rooms','proposals','tasks','events']:
                sql = f'SELECT * FROM {table} WHERE property_id=?'
                params = [actor['property_id']]
                if table=='proposals' and actor['role'] in ['guest_agent','upsell_agent']:
                    sql += ' AND actor=?'; params.append(actor['id'])
                if table in ['events','tasks'] and actor['role'] in ['guest_agent','upsell_agent']:
                    result[table]=[]; continue
                rows = [dict(r) for r in db.execute(sql,params)]
                for row in rows:
                    if table=='reservations' and actor['role'] in ['guest_agent','upsell_agent']:
                        row.pop('guest',None)
                    if table=='proposals':
                        row['payload']=json.loads(row['payload']); row['result']=json.loads(row['result']) if row['result'] else None
                        row['effective_status']='expired' if row['status'] in ('pending','approved') and row['expires']<=self.clock() else row['status']
                        row.pop('fingerprint'); row.pop('idem')
                result[table]=rows
            self.audit(db,actor,'state.read')
            return result

    def validate_payload(self, kind, payload):
        if kind=='late_checkout':
            fields(payload,['hour']); require(type(payload['hour']) is int and 12<=payload['hour']<=23,'invalid_hour','Checkout must be a whole hour from 12 to 23.')
        elif kind=='room_upgrade':
            fields(payload,['room']); string(payload['room'],12)
        elif kind=='service_task':
            fields(payload,['category','description'])
            require(payload['category'] in ('housekeeping','maintenance'),'invalid_category','Choose housekeeping or maintenance.')
            string(payload['description'],500)
        else:
            raise Fault('unsupported_action','This action is not supported.')

    def check(self, db, actor, prop, reservation, kind, payload):
        require(reservation['status']=='in_house','invalid_stay','The reservation must be in house.',409)
        if kind=='late_checkout':
            require(payload['hour']<=prop['max_checkout'],'policy_denied',f"Checkout policy allows up to {prop['max_checkout']}:00.",409)
            require(payload['hour']>reservation['checkout'],'no_change','Checkout must extend the current checkout time.',409)
            return {'checkout':payload['hour'],'quoted_fee_minor':(payload['hour']-11)*prop['checkout_fee'],'currency':prop['currency'],'payment_collected':False}
        if kind=='room_upgrade':
            room = db.execute('SELECT * FROM rooms WHERE property_id=? AND id=?',(actor['property_id'],payload['room'])).fetchone()
            require(room is not None and room['category']=='suite' and reservation['category']=='classic','invalid_upgrade','Choose a suite for a classic-room reservation.',409)
            require(room['ready'],'room_not_ready','This room is not ready.',409)
            occupied = db.execute("SELECT 1 FROM reservations WHERE property_id=? AND room=? AND status='in_house'",(actor['property_id'],payload['room'])).fetchone()
            require(not occupied,'room_unavailable','This suite is already occupied.',409)
            return {'room':payload['room'],'category':'suite','charge_minor':0,'complimentary':True}
        return {'category':payload['category'],'description':payload['description']}

    def propose(self, actor, data):
        fields(data,['kind','reservation_id','payload','idempotency_key'])
        kind, rid, key = string(data['kind'],40), string(data['reservation_id'],40), string(data['idempotency_key'],100)
        self.validate_payload(kind,data['payload'])
        fingerprint = hashlib.sha256(canonical({k:data[k] for k in ('kind','reservation_id','payload')}).encode()).hexdigest()
        with self.tx() as db:
            actor = self.principal(db,actor,['staff','guest_agent','upsell_agent','manager'])
            allowed = {'guest_agent':{'late_checkout','service_task'},'upsell_agent':{'room_upgrade'}}
            require(actor['role'] not in allowed or kind in allowed[actor['role']],'forbidden','This agent is not permitted to propose that action.',403)
            old = db.execute('SELECT id,fingerprint FROM proposals WHERE property_id=? AND actor=? AND idem=?',(actor['property_id'],actor['id'],key)).fetchone()
            if old:
                require(old['fingerprint']==fingerprint,'idempotency_conflict','This request key was already used for different input.',409)
                return {'id':old['id'],'replayed':True}
            prop = self.property(db,actor,True)
            reservation = self.reservation(db,actor,rid)
            preview = self.check(db,actor,prop,reservation,kind,data['payload'])
            pid = 'P-'+secrets.token_hex(6)
            db.execute('INSERT INTO proposals(id,property_id,actor,kind,reservation_id,payload,base_version,policy_version,status,created,expires,idem,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (pid,actor['property_id'],actor['id'],kind,rid,canonical(data['payload']),reservation['version'],prop['policy_version'],'pending',self.clock(),self.clock()+1800,key,fingerprint))
            self.audit(db,actor,'proposal.created',pid,{'kind':kind,'reservation':rid,'base_version':reservation['version']})
            return {'id':pid,'preview':preview,'replayed':False}

    def get_proposal(self, db, actor, pid):
        p = db.execute('SELECT * FROM proposals WHERE id=? AND property_id=?',(pid,actor['property_id'])).fetchone()
        require(p is not None,'not_found','Proposal not found.',404)
        return dict(p)

    def review(self, actor, pid, data):
        fields(data,['decision','reason'])
        require(data['decision'] in ('approve','reject'),'invalid_decision','Choose approve or reject.')
        reason = string(data['reason'],300)
        with self.tx() as db:
            actor = self.principal(db,actor,['manager'])
            self.property(db,actor,True)
            p = self.get_proposal(db,actor,pid)
            require(p['actor'] != actor['id'],'self_approval','A different manager must review this proposal.',403)
            require(p['status']=='pending','invalid_state','Only pending proposals can be reviewed.',409)
            require(p['expires']>self.clock(),'expired','This proposal expired. Create a new one.',409)
            status='approved' if data['decision']=='approve' else 'rejected'
            db.execute('UPDATE proposals SET status=?,reviewer=?,reason=? WHERE id=?',(status,actor['id'],reason,pid))
            self.audit(db,actor,'proposal.'+status,pid)
            return {'id':pid,'status':status}

    def execute(self, actor, pid):
        failure = None
        with self.tx() as db:
            actor = self.principal(db,actor,['manager'])
            p = self.get_proposal(db,actor,pid)
            if p['status']=='executed':
                return {'id':pid,'result':json.loads(p['result']),'replayed':True}
            require(p['status']=='approved','not_approved','Manager approval is required before execution.',409)
            try:
                prop=self.property(db,actor,True)
                require(p['expires']>self.clock(),'expired','This proposal expired.',409)
                proposer=db.execute('SELECT * FROM users WHERE id=?',(p['actor'],)).fetchone()
                reviewer=db.execute('SELECT * FROM users WHERE id=?',(p['reviewer'],)).fetchone()
                require(proposer and proposer['active'] and proposer['property_id']==actor['property_id'],'revoked','Proposer access was revoked.',403)
                require(reviewer and reviewer['active'] and reviewer['role']=='manager' and reviewer['property_id']==actor['property_id'],'revoked','Reviewer access was revoked.',403)
                allowed={'guest_agent':{'late_checkout','service_task'},'upsell_agent':{'room_upgrade'},'staff':{'late_checkout','service_task','room_upgrade'},'manager':{'late_checkout','service_task','room_upgrade'}}
                require(p['kind'] in allowed.get(proposer['role'],set()),'revoked','Proposer permission changed.',403)
                require(prop['policy_version']==p['policy_version'],'policy_changed','Hotel policy changed. A fresh proposal and approval are required.',409)
                reservation=self.reservation(db,actor,p['reservation_id'])
                require(reservation['version']==p['base_version'],'stale_reservation','The reservation changed. Refresh and submit a new proposal.',409)
                payload=json.loads(p['payload'])
                result=self.check(db,actor,prop,reservation,p['kind'],payload)
            except Fault as exc:
                failure=exc
                if exc.code != 'paused':
                    db.execute('UPDATE proposals SET status=?,reason=? WHERE id=?',('expired' if exc.code=='expired' else 'conflict',exc.message,pid))
                self.audit(db,actor,'execution.blocked',pid,{'code':exc.code})
            if failure is None:
                if p['kind']=='late_checkout':
                    db.execute('UPDATE reservations SET checkout=?,version=version+1 WHERE property_id=? AND id=?',(payload['hour'],actor['property_id'],p['reservation_id']))
                elif p['kind']=='room_upgrade':
                    db.execute("UPDATE reservations SET room=?,category='suite',version=version+1 WHERE property_id=? AND id=?",(payload['room'],actor['property_id'],p['reservation_id']))
                    db.execute('UPDATE rooms SET ready=0,version=version+1 WHERE property_id=? AND id=?',(actor['property_id'],reservation['room']))
                    db.execute('UPDATE rooms SET version=version+1 WHERE property_id=? AND id=?',(actor['property_id'],payload['room']))
                else:
                    tid='T-'+secrets.token_hex(6)
                    db.execute('INSERT INTO tasks(id,property_id,reservation_id,category,description,status,proposal_id,created) VALUES(?,?,?,?,?,?,?,?)',(tid,actor['property_id'],p['reservation_id'],payload['category'],payload['description'],'open',pid,self.clock()))
                    result={'task_id':tid}
                db.execute("UPDATE proposals SET status='executed',result=? WHERE id=?",(canonical(result),pid))
                self.audit(db,actor,'proposal.executed',pid,{'kind':p['kind'],'reservation':p['reservation_id']})
        if failure:
            raise failure
        return {'id':pid,'result':result,'replayed':False}

    def policy(self, actor, data):
        fields(data,['paused','max_checkout','checkout_fee','version'])
        require(type(data['paused']) is bool and type(data['max_checkout']) is int and 12<=data['max_checkout']<=18 and type(data['checkout_fee']) is int and 0<=data['checkout_fee']<=100000 and type(data['version']) is int,
                'invalid_policy','Invalid policy values.')
        with self.tx() as db:
            actor=self.principal(db,actor,['manager']); p=self.property(db,actor)
            require(data['version']==p['policy_version'],'stale_policy','Refresh the changed policy before saving.',409)
            changed=(data['max_checkout'],data['checkout_fee']) != (p['max_checkout'],p['checkout_fee'])
            db.execute('UPDATE properties SET paused=?,max_checkout=?,checkout_fee=?,policy_version=policy_version+? WHERE id=?',(data['paused'],data['max_checkout'],data['checkout_fee'],int(changed),actor['property_id']))
            self.audit(db,actor,'policy.updated',detail=data)
            return self.property(db,actor)

    def task_update(self, actor, tid, data):
        fields(data,['status','version'])
        require(data['status'] in ('in_progress','done') and type(data['version']) is int,'invalid_task','Invalid task update.')
        with self.tx() as db:
            actor=self.principal(db,actor,['manager','staff']); self.property(db,actor,True)
            row=db.execute('SELECT * FROM tasks WHERE id=? AND property_id=?',(tid,actor['property_id'])).fetchone()
            require(row is not None,'not_found','Task not found.',404)
            require(row['version']==data['version'],'stale_task','Task changed. Refresh it.',409)
            require((row['status'],data['status']) in [('open','in_progress'),('in_progress','done')],'invalid_transition','Start a task before completing it.',409)
            db.execute('UPDATE tasks SET status=?,version=version+1 WHERE id=?',(data['status'],tid))
            self.audit(db,actor,'task.'+data['status'],tid)
            return {'id':tid,'status':data['status']}

    def enqueue(self, actor, data):
        fields(data,['external_id','room','ready','version'])
        string(data['external_id'],100); string(data['room'],12)
        require(type(data['ready']) is bool and type(data['version']) is int and data['version']>0,'invalid_event','Invalid room event.')
        fingerprint=hashlib.sha256(canonical(data).encode()).hexdigest()
        with self.tx() as db:
            actor=self.principal(db,actor,['manager','staff'])
            old=db.execute('SELECT * FROM events WHERE property_id=? AND external_id=?',(actor['property_id'],data['external_id'])).fetchone()
            if old:
                require(old['fingerprint']==fingerprint,'idempotency_conflict','Event identifier already has different input.',409)
                return {'id':old['id'],'replayed':True}
            require(db.execute('SELECT 1 FROM rooms WHERE property_id=? AND id=?',(actor['property_id'],data['room'])).fetchone() is not None,'not_found','Room not found.',404)
            eid='E-'+secrets.token_hex(6)
            db.execute('INSERT INTO events(id,property_id,external_id,payload,fingerprint,status,created) VALUES(?,?,?,?,?,?,?)',(eid,actor['property_id'],data['external_id'],canonical(data),fingerprint,'queued',self.clock()))
            self.audit(db,actor,'event.received',eid)
            return {'id':eid,'replayed':False}

    def process_events(self, actor):
        with self.tx() as db:
            actor=self.principal(db,actor,['manager']); self.property(db,actor,True)
            rows=db.execute("SELECT * FROM events WHERE property_id=? AND status='queued' ORDER BY rowid LIMIT 100",(actor['property_id'],)).fetchall()
            results=[]
            for row in rows:
                event=json.loads(row['payload'])
                updated=db.execute('UPDATE rooms SET ready=?,version=version+1 WHERE property_id=? AND id=? AND version=?',(event['ready'],actor['property_id'],event['room'],event['version'])).rowcount
                status='processed' if updated else 'dead_letter'
                reason=None if updated else 'Room version changed; inspect current state and submit a new event.'
                db.execute('UPDATE events SET status=?,reason=? WHERE id=?',(status,reason,row['id']))
                self.audit(db,actor,'event.'+status,row['id'])
                results.append({'id':row['id'],'status':status})
            return {'results':results}

    def audit_export(self, actor):
        with self.tx() as db:
            actor=self.principal(db,actor,['manager'])
            self.audit(db,actor,'audit.exported')
            rows=[dict(r) for r in db.execute('SELECT * FROM audit WHERE property_id=? ORDER BY seq',(actor['property_id'],))]
        previous='0'*64
        for row in rows:
            entry={k:v for k,v in row.items() if k not in ('seq','signature')}
            expected=hmac.new(self.key,canonical(entry).encode(),hashlib.sha256).hexdigest()
            if row['previous']!=previous or not hmac.compare_digest(expected,row['signature']):
                return {'valid':False,'broken_at':row['seq'],'entries':rows}
            previous=row['signature']
        return {'valid':True,'head':previous,'entries':rows}
