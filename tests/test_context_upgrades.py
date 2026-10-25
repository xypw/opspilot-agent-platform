import copy
import os
import json
import httpx
from datetime import datetime,timezone
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4

from conversation_context import build_conversation_history
from conversation_memory import ConversationMemory,source_hash,vector_literal,memory_context
from model_context import prepare_context,ContextBudgetExceeded
from qwen_tokenizer import render_qwen
from tests import test_customer_portal as portal_fixture


class UpgradeTests(unittest.TestCase):
    def test_tool_json_matches_native_order_and_escapes_html(self):
        tool={'type':'function','function':{'name':'read','description':'<trusted>',
            'parameters':{'type':'object','additionalProperties':False,'properties':{
                'z':{'description':'z value','type':'string','maxLength':10}},'required':['z']}}}
        text=render_qwen([{'role':'user','content':'hi'}],[tool])
        self.assertIn('"type": "object", "required": ["z"], "properties"',text)
        self.assertIn('"type": "string", "description": "z value"',text)
        self.assertIn(r'\u003ctrusted\u003e',text)
        self.assertNotIn('additionalProperties',text);self.assertNotIn('maxLength',text)

    def test_ollama_reports_negative_or_excessive_usage_as_failure(self):
        from ollama_gateway import ollama_chat,LocalModelFormatError
        for prompt,output in [(-1,2),(20,-1),(8000,500)]:
            response={'done':True,'message':{'role':'assistant','content':'OK'},
                      'prompt_eval_count':prompt,'eval_count':output}
            with patch.dict(os.environ,{'OLLAMA_BASE_URL':'http://127.0.0.1:11434','OLLAMA_MODEL':'qwen3-vl:4b',
                                       'QWEN_TOKENIZER_PATH':''}),patch('ollama_gateway.acquire_model_request'),\
                    httpx.Client(transport=httpx.MockTransport(lambda _,r=response:httpx.Response(200,json=r))) as client:
                with self.assertRaises(LocalModelFormatError):ollama_chat(client,[{'role':'user','content':'hi'}])

    def test_backend_context_rejection_is_known_sent_error(self):
        from ollama_gateway import ollama_chat
        from model_context import ContextWindowRejected
        seen=[]
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(400,json={'error':'prompt is longer than the context length'})
        with patch.dict(os.environ,{'OLLAMA_BASE_URL':'http://127.0.0.1:11434','OLLAMA_MODEL':'qwen3-vl:4b',
                                   'QWEN_TOKENIZER_PATH':''}),patch('ollama_gateway.acquire_model_request'),\
                httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ContextWindowRejected) as caught:ollama_chat(client,[{'role':'user','content':'hi'}])
        self.assertTrue(caught.exception.request_sent);self.assertEqual(len(seen),1)
        self.assertFalse(seen[0]['truncate']);self.assertFalse(seen[0]['shift'])

    def test_renderer_covers_non_thinking_tools_and_does_not_mutate(self):
        rows=[{'role':'system','content':'rules'},{'role':'user','content':'你好'}]
        self.assertEqual(render_qwen(rows),'<|im_start|>system\nrules<|im_end|>\n<|im_start|>user\n你好<|im_end|>\n<|im_start|>assistant\n')
        paired=rows+[{'role':'assistant','content':'','tool_calls':[{'function':{'name':'query_order','arguments':'{"order_id":"O-7101"}'}}]},
                     {'role':'tool','content':'not found'}]
        original=copy.deepcopy(paired)
        text=render_qwen(paired,[{'type':'function','function':{'name':'query_order'}}])
        self.assertIn('<tools>',text);self.assertIn('<tool_response>\nnot found',text)
        self.assertEqual(paired,original)

    def test_real_counter_can_accept_long_chinese_without_byte_rejection(self):
        rows=[{'role':'user','content':'中文'*2000}]
        with self.assertRaises(ContextBudgetExceeded):prepare_context(rows)
        result=prepare_context(rows,token_counter=lambda m,t:4000)
        self.assertEqual(result.messages,rows);self.assertEqual(result.estimated_input_units,4000)

    def test_semantic_match_without_keyword_overlap_is_used_as_old_reference(self):
        old={'id':'old','message':'保留购物小票。','result':{'answer':'旧答复不代表批准'},'_semantic_recalled':True}
        recent=[{'id':str(i),'message':'问候'+str(i),'result':{'answer':'您好'}} for i in range(4)]
        history=build_conversation_history([old,*recent],'购买凭证如何准备？')
        self.assertTrue(any('按需检索' in r['content'] and '小票' in r['content'] for r in history))
        self.assertFalse(any(r['role'] in {'system','tool'} for r in history))

    def test_chunked_index_and_recreated_service_keep_original_source(self):
        class Store:
            def __init__(self):self.saved={}
            def unindexed_memory(self,user,cid,model,limit):return [] if self.saved else [{'id':'old','message':'原文。'*300}]
            def save_memory(self,user,cid,tid,h,model,vectors):self.saved[tid]=(h,vectors)
            def search_memory(self,user,cid,model,vector,limit):return [{'id':'old','message':'原文。'*300,
                'memory_source_hash':self.saved['old'][0]}]
        model=Mock();model.passage_embed.side_effect=lambda pieces:[[1.0]+[0.0]*511 for _ in pieces]
        model.query_embed.return_value=[[1.0]+[0.0]*511]
        store=Store();self.assertEqual(ConversationMemory(model).index_pending(store,'owner','cid'),1)
        self.assertGreater(len(store.saved['old'][1]),1)
        result=ConversationMemory(model).recall(store,'owner','cid','同义表达')
        self.assertEqual(result[0]['message'],'原文。'*300)
        self.assertTrue(result[0]['_semantic_recalled'])
        self.assertEqual(ConversationMemory(model).index_pending(store,'owner','cid'),0)

    def test_invalid_vectors_and_stale_source_are_rejected(self):
        for vector in [[0.0]*511,[float('nan')]*512,[True]*512]:
            with self.assertRaises(ValueError):vector_literal(vector)
        model=Mock();model.query_embed.return_value=[[1.0]+[0.0]*511]
        store=Mock();store.search_memory.return_value=[{'message':'已变更','memory_source_hash':source_hash('原文')}]
        self.assertEqual(ConversationMemory(model).recall(store,'owner','cid','问题'),[])


class PortalUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.fixture=portal_fixture.PortalAuthenticationTests(methodName='test_cookie_requires_csrf_for_mutations')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.login();self.store=self.fixture.store;self.client=self.fixture.client

    def test_long_conversation_pages_and_request_lookup_are_owner_scoped(self):
        cid=self.store.create_conversation('U-1001','long')['id'];ids=[];requests=[]
        for i in range(137):
            tid=str(uuid4());rid=str(uuid4());ids.append(tid);requests.append(rid)
            self.store.turns[tid]={'id':tid,'request_id':rid,'conversation_id':cid,'message':str(i),'image_ids':[],
                'mode':'mock','kind':'agent','status':'COMPLETED','result':{'answer':'ok'}}
        page=self.client.get('/conversations/'+cid).json()
        self.assertEqual([t['id'] for t in page['turns']],ids[-50:])
        older=self.client.get('/conversations/'+cid,params={'before':page['next_before']}).json()
        self.assertEqual([t['id'] for t in older['turns']],ids[-100:-50])
        oldest=self.client.get('/conversations/'+cid,params={'before':older['next_before']}).json()
        self.assertEqual(len(oldest['turns']),37);self.assertIsNone(oldest['next_before'])
        self.assertEqual(self.client.get('/conversations/'+cid+'/requests/'+requests[0]).json()['id'],ids[0])
        other=self.store.create_conversation('U-1002','private')['id']
        self.assertEqual(self.client.get('/conversations/'+other).status_code,404)
        self.assertEqual(self.client.get('/conversations/'+cid,params={'before':str(uuid4())}).status_code,404)

    def test_memory_failure_releases_task_and_is_not_unknown_write(self):
        from conversation_memory import ConversationMemoryUnavailable
        cid=self.store.create_conversation('U-1001','memory')['id']
        with patch('portal_api.memory_context',side_effect=ConversationMemoryUnavailable('索引不可用')):
            response=self.client.post('/conversations/'+cid+'/turns',headers=self.fixture.csrf(),json={
                'request_id':str(uuid4()),'message':'查售后政策','mode':'live'})
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.store.conversation('U-1001',cid)['turns'][0]['status'],'FAILED')
        self.assertIsNone(self.store.conversations[cid]['active_turn_id'])
