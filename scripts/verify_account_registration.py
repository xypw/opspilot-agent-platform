"""Local HTTP/Java acceptance, synthetic accounts only; no model requests."""
from pathlib import Path
import json
import secrets
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from portal_store import PostgresPortalStore, fingerprint
from portal_identity import issue_business_identity
from auth import _internal_service_token


def run(output=None):
    target=Path(output) if output else ROOT/'reports/accounts-deployment-20261028.json'
    # Reserve evidence before creating any test account; never overwrite history.
    with target.open('x',encoding='utf-8') as report:
        json.dump({'passed':False,'status':'started','model_calls':0},report)
    import psycopg
    from database import load_database_url
    database_url=load_database_url().replace('@localhost:', '@127.0.0.1:')
    from urllib.parse import urlsplit
    if urlsplit(database_url).hostname!='127.0.0.1':
        raise RuntimeError('验收只允许本机数据库')
    store=PostgresPortalStore(lambda:psycopg.connect(database_url,connect_timeout=5))
    prefix='qa_'+uuid4().hex[:12]
    names=[prefix+'a',prefix+'b']
    admin_token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    rows=[]
    try:
        store.create_session('ys10087',fingerprint(admin_token),fingerprint(csrf))
        with httpx.Client(base_url='http://127.0.0.1:8011',trust_env=False,timeout=20) as admin, \
             httpx.Client(base_url='http://127.0.0.1:8011',trust_env=False,timeout=20) as a, \
             httpx.Client(base_url='http://127.0.0.1:8011',trust_env=False,timeout=20) as b:
            admin.cookies.set('opspilot_session',admin_token);admin.cookies.set('opspilot_csrf',csrf)
            def check(label,condition):
                rows.append({'check':label,'passed':bool(condition)})
                if not condition:raise AssertionError(label)
            password=secrets.token_urlsafe(20)
            for client,name in ((a,names[0]),(b,names[1])):
                response=client.post('/auth/register',json={'username':name,'password':password})
                check('register_'+name[-1],response.status_code==201)
                info=response.json();check('customer_only_'+name[-1],info['roles']==['customer'])
                check('auto_login_'+name[-1],client.get('/auth/me').status_code==200)
            own=a.get('/auth/me').json();other=b.get('/auth/me').json()
            check('owned_order_java',a.get('/service/orders/'+own['demo_order_id']).status_code==200)
            check('foreign_order_java',b.get('/service/orders/'+own['demo_order_id']).status_code==404)
            check('no_admin_list',a.get('/auth/admin/users').status_code==403)
            check('no_policy_upload',a.post('/documents/upload',headers={'X-CSRF-Token':a.cookies['opspilot_csrf']},
                data={'title':'qa','document_id':'qa'},files={'file':('qa.pdf',b'not a pdf','application/pdf')}).status_code==403)
            check('admin_roles',{'admin','knowledge_admin'}<=set(admin.get('/auth/me').json()['roles']))
            listing=admin.get('/auth/admin/users')
            check('admin_list',listing.status_code==200)
            check('no_password_in_list','password_hash' not in listing.text)
            check('csrf_admin',admin.patch('/auth/admin/users/'+names[0],json={'active':False}).status_code==403)
            header={'X-CSRF-Token':csrf}
            check('self_protected',admin.patch('/auth/admin/users/ys10087',headers=header,json={'active':False}).status_code==409)
            check('disable',admin.patch('/auth/admin/users/'+names[0],headers=header,json={'active':False}).status_code==200)
            check('old_session_revoked',a.get('/auth/me').status_code==401)
            with httpx.Client(trust_env=False) as java:
                token=issue_business_identity(own['user_id'])
                headers={'Authorization':'Bearer '+token,'X-OpsPilot-Service-Token':_internal_service_token()}
                check('java_rejects_disabled',java.get('http://127.0.0.1:8081/api/orders/'+own['demo_order_id'],headers=headers).status_code==401)
                forged=issue_business_identity(other['user_id'])
                forged=forged.rsplit('.',1)[0]+'.invalid'
                headers['Authorization']='Bearer '+forged
                check('java_rejects_tamper',java.get('http://127.0.0.1:8081/api/orders/'+other['demo_order_id'],headers=headers).status_code==401)
            check('enable',admin.patch('/auth/admin/users/'+names[0],headers=header,json={'active':True}).status_code==200)
            check('enable_no_old_session',a.get('/auth/me').status_code==401)
            check('login_again',a.post('/auth/login',json={'username':names[0],'password':password}).status_code==200)
            check('no_role_injection',b.post('/auth/register',json={'username':'qa_forbidden','password':password,'roles':['admin']}).status_code==422)
            check('cross_origin',b.post('/auth/register',headers={'Origin':'https://outside.example'},json={'username':'qa_forbidden','password':password}).status_code==403)
    finally:
        # Only accounts allocated by this invocation; source/regular accounts never removed.
        with store.transaction() as cursor:
            cursor.execute('DELETE FROM portal_sessions WHERE token_hash=%s',(fingerprint(admin_token),))
            for name in names:
                cursor.execute('SELECT demo_order_id FROM portal_accounts WHERE username=%s',(name,))
                account=cursor.fetchone()
                if account:
                    cursor.execute('DELETE FROM portal_sessions WHERE username=%s',(name,))
                    cursor.execute('DELETE FROM portal_admin_events WHERE target=%s',(name,))
                    cursor.execute('DELETE FROM order_return_policies WHERE order_id=%s',(account['demo_order_id'],))
                    cursor.execute('DELETE FROM orders WHERE id=%s',(account['demo_order_id'],))
                    cursor.execute('DELETE FROM portal_accounts WHERE username=%s',(name,))
    with target.open('w',encoding='utf-8') as output:
        json.dump({'checks':rows,'model_calls':0,'test_accounts_cleaned':True},output,ensure_ascii=False,indent=2)
    print(json.dumps({'passed':len(rows),'model_calls':0,'test_accounts_cleaned':True}))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    run(parser.parse_args().output)
