"""Host-operator account administration and consistent backups. No web admin bypass."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import secrets
import sqlite3
from .store import Store, Fault, require, password_hash, string

ROLES=('manager','staff','guest_agent','upsell_agent')


def set_account(store,username,property_id=None,role=None,disable=False,reset=False):
    username=string(username,80)
    with store.tx() as db:
        old=db.execute('SELECT * FROM users WHERE id=?',(username,)).fetchone()
        require(old is not None or (property_id and role),'invalid_account','New accounts require property and role.')
        prop=property_id or old['property_id']
        require(db.execute('SELECT 1 FROM properties WHERE id=?',(prop,)).fetchone() is not None,'not_found','Property not found.')
        selected_role=role or old['role']
        require(selected_role in ROLES,'invalid_role','Unknown role.')
        require(not old or old['property_id']==prop,'property_change','Create a new account instead of moving an existing account between properties.')
        password=secrets.token_urlsafe(24) if reset or not old else None
        salt=secrets.token_hex(16) if password else old['salt']
        hashed=password_hash(password,salt) if password else old['password']
        db.execute('INSERT INTO users(id,property_id,role,salt,password,active) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET role=excluded.role,salt=excluded.salt,password=excluded.password,active=excluded.active',
                   (username,prop,selected_role,salt,hashed,int(not disable)))
        db.execute('DELETE FROM sessions WHERE user_id=?',(username,))
        store.audit(db,{'id':'host-operator','property_id':prop},'account.updated',username,{'role':selected_role,'active':not disable,'password_reset':bool(password)})
    return {'username':username,'password':password,'active':not disable}


def backup(store,destination):
    target=Path(destination)
    require(not target.exists(),'backup_exists','Choose a new backup directory; existing backups are not overwritten.')
    target.mkdir(parents=True,mode=0o700)
    try:
        with closing(store.connect()) as source, closing(sqlite3.connect(target/'control.sqlite3')) as dest:
            source.backup(dest)
            require(dest.execute('PRAGMA integrity_check').fetchone()[0]=='ok','backup_invalid','Database integrity check failed.',500)
        (target/'control.sqlite3').chmod(0o600)
        with (target/'audit.key').open('xb') as f: f.write(store.key)
        (target/'audit.key').chmod(0o600)
        (target/'manifest.json').write_text(json.dumps({'format':1,'created':store.clock(),'backend':'sqlite','contains_sensitive_data':True},indent=2)+'\n')
    except Exception:
        # Retain partial output for diagnosis. Never overwrite it on retry.
        raise
    return {'directory':str(target),'integrity':'ok'}


def main():
    parser=argparse.ArgumentParser(description='Austugal Control host administration')
    parser.add_argument('--data',default='./data')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('users')
    sub.add_parser('revoke-sessions')
    account=sub.add_parser('account'); account.add_argument('username');account.add_argument('--property',choices=['LIS','MEL']);account.add_argument('--role',choices=ROLES)
    account.add_argument('--disable',action='store_true');account.add_argument('--reset-password',action='store_true')
    save=sub.add_parser('backup');save.add_argument('destination')
    args=parser.parse_args()
    require((Path(args.data)/'control.sqlite3').exists(),'not_found','Start the application once to initialize this data directory.')
    store=Store(args.data)
    if args.command=='users':
        with closing(store.connect()) as db:
            print(json.dumps([dict(r) for r in db.execute('SELECT id,property_id,role,active FROM users ORDER BY id')],indent=2))
    elif args.command=='revoke-sessions':
        with store.tx() as db:
            db.execute('DELETE FROM sessions')
            for row in db.execute('SELECT id FROM properties').fetchall():
                store.audit(db,{'id':'host-operator','property_id':row['id']},'sessions.revoked')
        print('All sessions revoked.')
    elif args.command=='account':
        print(json.dumps(set_account(store,args.username,args.property,args.role,args.disable,args.reset_password),indent=2))
        print('Any generated password is shown once. Store it privately. Existing sessions were revoked.')
    else: print(json.dumps(backup(store,args.destination),indent=2))


if __name__=='__main__':
    try: main()
    except Fault as exc: raise SystemExit(exc.message)
