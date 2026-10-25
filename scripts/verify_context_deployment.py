"""Local HTTP + PostgreSQL + real BGE acceptance; no language-model calls."""
import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.smoke_customer_portal import create_database_connection,customer_username
from portal_store import PostgresPortalStore,PortalNotFoundError
from conversation_memory import ConversationMemory,cached_embedder,memory_context
from conversation_context import build_conversation_history


def _run_checks():
    marker='context-acceptance-'+uuid4().hex
    store=PostgresPortalStore(create_database_connection)
    cid=str(store.create_conversation('U-1001',marker)['id'])
    ids=[str(uuid4()) for _ in range(137)]
    request_ids=[str(uuid4()) for _ in ids]
    checks=[]
    def check(name,value):
        if not value:raise AssertionError(name)
        checks.append(name)
    try:
        with create_database_connection() as db:
            for i,tid in enumerate(ids):
                message='购买商品时留了商家开的购物小票。' if i==0 else '独立练习编号'+str(i)+'，今天先交流问候。'
                db.execute("""INSERT INTO portal_turns(id,conversation_id,request_id,request_hash,message,
                    mode,kind,status,result,created_at) VALUES (%s,%s,%s,'synthetic',%s,'mock','agent',
                    'COMPLETED',%s,NOW()+(%s * INTERVAL '1 second'))""",
                    (tid,cid,request_ids[i],message,Jsonb({'answer':'这只是合成历史，不是审批。'}),i))
        credentials={}
        for line in (ROOT/'output/customer-login-access.txt').read_text(encoding='utf-8').splitlines():
            if ': ' in line:
                name,password=line.split(': ',1);credentials[name]=password
        with httpx.Client(base_url='http://127.0.0.1:8011',trust_env=False,timeout=20) as client:
            username=customer_username('U-1001',credentials)
            login=client.post('/auth/login',json={'username':username,'password':credentials[username]})
            check('owner_login',login.status_code==200)
            path='/conversations/'+cid
            seen=[];sizes=[];before=None
            for _ in range(4):
                response=client.get(path,params={'before':before} if before else {})
                check('page_http_'+str(len(sizes)),response.status_code==200)
                page=response.json();sizes.append(len(page['turns']));seen.extend(t['id'] for t in page['turns'])
                before=page['next_before']
                if before is None:break
            check('137_turns_50_50_37',sizes==[50,50,37] and len(set(seen))==137)
            check('old_request_direct_lookup',client.get(path+'/requests/'+request_ids[0]).json()['id']==ids[0])
            check('original_source_lookup',client.get(path+'/turns/'+ids[0]).json()['message'].endswith('购物小票。'))
            with httpx.Client(base_url='http://127.0.0.1:8011',trust_env=False,timeout=20) as other:
                name=customer_username('U-1002',credentials)
                other.post('/auth/login',json={'username':name,'password':credentials[name]}).raise_for_status()
                check('foreign_source_hidden',other.get(path+'/turns/'+ids[0]).status_code==404)
                check('foreign_cursor_hidden',other.get(path).status_code==404)
                other.post('/auth/logout',headers={'X-CSRF-Token':other.cookies.get('opspilot_csrf')})
            client.post('/auth/logout',headers={'X-CSRF-Token':client.cookies.get('opspilot_csrf')})
        assets=str(ROOT/'.models/memory-bge')
        os.environ['MEMORY_EMBEDDING_PATH']=assets
        service=ConversationMemory(cached_embedder(assets));indexed=0
        for _ in range(6):
            added=service.index_pending(store,'U-1001',cid);indexed+=added
            if not added:break
        check('all_synthetic_sources_indexed',indexed==137)
        recreated=PostgresPortalStore(create_database_connection)
        recalled=ConversationMemory(cached_embedder(assets)).recall(recreated,'U-1001',cid,'购买凭证该准备什么？')
        check('semantic_source_recalled_after_recreation',ids[0] in {str(r['id']) for r in recalled})
        history=build_conversation_history(memory_context(recreated,'U-1001',cid,'购买凭证该准备什么？',
            recreated.conversation('U-1001',cid)['turns']),'购买凭证该准备什么？')
        check('old_source_reaches_bounded_history',any(ids[0] in h['content'] and '小票' in h['content'] for h in history))
        try:
            service.recall(recreated,'U-1002',cid,'购买凭证')
        except PortalNotFoundError:
            check('semantic_owner_enforced',True)
        else:
            raise AssertionError('semantic_owner_enforced')
        return {'checks_passed':len(checks),'checks':checks,'llm_requests':0,
                'embedding':'real local BGE','synthetic_turns':137}
    finally:
        with create_database_connection() as db:
            deleted=db.execute("DELETE FROM portal_conversations WHERE id=%s AND owner_user_id='U-1001' AND title=%s RETURNING id",
                (cid,marker)).fetchone()
            if not deleted:raise RuntimeError('合成会话被外部修改，停止清理')


def run(output=None):
    target=Path(output) if output is not None else ROOT/'reports/context-deployment-20261024.json'
    # Reserve before creating synthetic DB rows or performing any HTTP request.
    with target.open('x',encoding='utf-8') as report_file:
        result={'passed':False,'llm_requests':0}
        try:
            result=_run_checks()
            return result
        except Exception as error:
            result['error_type']=type(error).__name__
            raise
        finally:
            json.dump(result,report_file,ensure_ascii=False,indent=2)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-local',action='store_true',required=True)
    parser.add_argument('--output',type=Path,help='新报告路径；已有文件会在创建测试数据前拒绝')
    options=parser.parse_args()
    result=run(options.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
