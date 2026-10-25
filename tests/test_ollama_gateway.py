"""本地模型协议、地址限制和用户指引的离线回归，不发真实模型请求。"""
import json
import os
import unittest
from unittest.mock import Mock, patch

import httpx

from agent_graph import ConfiguredAgentModelGateway, AgentGraphStartRequest, build_agent_graph, start_agent_graph
from model_runtime import ollama_configuration, public_runtime
from ollama_gateway import native_messages, ollama_chat,LocalModelFormatError,LocalModelTruncatedError
from pending_actions import PendingActionStore
from preview_tool_call import request_message, ModelAPIError
from service_guidance import service_guidance
from vision_service import ConfiguredVisionGateway, validate_image
from tests.test_customer_portal import fixture_image

LOCAL = {"OPSPILOT_MODEL_PROVIDER":"ollama","OLLAMA_BASE_URL":"http://127.0.0.1:11434",
         "OLLAMA_MODEL":"qwen3-vl:4b"}


@patch.dict(os.environ, LOCAL)
class OllamaTests(unittest.TestCase):
    def test_local_tool_arguments_are_converted_but_not_executed(self):
        seen=[]
        def handler(request):
            seen.append(json.loads(request.content))
            self.assertNotIn("authorization",request.headers)
            return httpx.Response(200,json={"done":True,"message":{"role":"assistant","content":"",
                "tool_calls":[{"function":{"name":"query_order","arguments":{"order_id":"O-7101"}}}]}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch("ollama_gateway.acquire_model_request"):
            result=request_message("should-not-send-cloud-key",client,[{"role":"user","content":"查订单"}])
        self.assertEqual(json.loads(result["tool_calls"][0]["function"]["arguments"]),{"order_id":"O-7101"})
        self.assertTrue(seen[0]["tools"])
        self.assertFalse(seen[0]["stream"])
        self.assertNotIn("should-not-send-cloud-key",json.dumps(seen))

    def test_tool_result_name_is_bound_to_prior_call(self):
        messages=[{"role":"assistant","content":None,"tool_calls":[{"id":"call-1",
            "function":{"name":"query_order","arguments":'{"order_id":"O-7101"}'}}]},
            {"role":"tool","tool_call_id":"call-1","content":"{}"}]
        converted=native_messages(messages)
        self.assertEqual(converted[1]["tool_name"],"query_order")
        self.assertIsInstance(converted[0]["tool_calls"][0]["function"]["arguments"],dict)
        with self.assertRaises(ValueError):
            native_messages([{"role":"tool","tool_call_id":"unknown","content":"{}"}])

    def test_remote_urls_cloud_models_and_path_injection_are_rejected(self):
        for url in ("https://evil.invalid:11434","http://127.0.0.1:11434/path",
                    "http://user:pass@127.0.0.1:11434","http://169.254.169.254:11434"):
            with patch.dict(os.environ,{"OLLAMA_BASE_URL":url}),self.assertRaises(ValueError):
                ollama_configuration()
        with patch.dict(os.environ,{"OLLAMA_MODEL":"qwen3-vl:235b-cloud"}),self.assertRaises(ValueError):
            ollama_configuration()

    def test_local_response_does_not_follow_redirect_or_fallback(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(302,
                headers={"Location":"https://evil.invalid"}))) as client, patch("ollama_gateway.acquire_model_request"):
            with self.assertRaises(ModelAPIError):
                ollama_chat(client,[{"role":"user","content":"x"}])

    def test_json_review_has_no_tools_and_rejects_tools_in_response(self):
        seen=[]
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200,json={"done":True,"message":{"role":"assistant","content":"{}",
                "tool_calls":[{"function":{"name":"query_order","arguments":{}}}]}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch("ollama_gateway.acquire_model_request"):
            with self.assertRaises(ValueError):
                ollama_chat(client,[{"role":"user","content":"x"}],json_output=True)
        self.assertEqual(seen[0]["format"],"json")
        self.assertNotIn("tools",seen[0])

    def test_local_gateway_does_not_read_cloud_key(self):
        with patch("agent_graph.load_api_key") as cloud, patch("agent_graph.request_message_with_retry",
                return_value={"role":"assistant","content":"hi"}):
            ConfiguredAgentModelGateway().request("live",[{"role":"user","content":"x"}],offer_tools=False)
        cloud.assert_not_called()

    def test_local_structured_review_uses_schema_and_larger_output_bound(self):
        from evidence_review import EvidenceVerdict
        seen=[]
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200,json={"done":True,"message":{
                "role":"assistant","content":'{"supported":false,"supporting_quotes":[],"missing_information":"missing"}'}})
        schema=EvidenceVerdict.model_json_schema()
        with httpx.Client(transport=httpx.MockTransport(handler)) as client,patch("ollama_gateway.acquire_model_request"):
            request_message("unused",client,[{"role":"user","content":"review"}],offer_tools=False,
                            response_format={"type":"json_schema","json_schema":{"schema":schema}})
        self.assertEqual(seen[0]["format"],schema)
        self.assertEqual(seen[0]["options"]["num_predict"],4096)
        self.assertNotIn("tools",seen[0])

    def test_truncated_local_result_is_never_used(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={
                "done":True,"done_reason":"length","message":{"role":"assistant","content":"partial"}}))) as client,patch(
                "ollama_gateway.acquire_model_request"):
            with self.assertRaises(LocalModelTruncatedError):
                ollama_chat(client,[{"role":"user","content":"x"}])

    def test_truncation_reports_only_usage_not_private_reasoning(self):
        for message in ({'role':'assistant','content':'partial','thinking':'private hidden reasoning'},[]):
            with httpx.Client(transport=httpx.MockTransport(lambda _,m=message:httpx.Response(200,json={
                    'done':True,'done_reason':'length','prompt_eval_count':20,'eval_count':512,'message':m}))) as client,patch(
                    'ollama_gateway.acquire_model_request'):
                with self.assertRaises(LocalModelTruncatedError) as caught:
                    ollama_chat(client,[{'role':'user','content':'x'}])
            self.assertEqual(caught.exception.context_usage['prompt_tokens'],20)
            self.assertEqual(caught.exception.context_usage['output_tokens'],512)
            self.assertNotIn('private hidden reasoning',str(caught.exception.context_usage))

    def test_malformed_response_shapes_always_become_known_model_failure(self):
        for response in ([],None,True,123,"text",{},
                {"done":True,"message":[]},
                {"done":True,"message":{"role":"assistant","content":[]}},
                {"done":True,"message":{"role":"assistant","content":""}},
                {"done":True,"message":{"role":"assistant","tool_calls":{}}},
                {"done":True,"message":{"role":"assistant","tool_calls":[{"function":[]}]}},
                {"done":True,"message":{"role":"assistant","tool_calls":[{"function":{
                    "name":"query_order","arguments":{}}}]}}):
            with self.subTest(response=response),httpx.Client(transport=httpx.MockTransport(
                    lambda _,r=response:httpx.Response(200,content=json.dumps(r).encode()))) as client,patch(
                    "ollama_gateway.acquire_model_request"):
                with self.assertRaises(LocalModelFormatError):
                    ollama_chat(client,[{"role":"user","content":"x"}],offer_tools=True)

    def test_local_vision_encodes_image_directly_and_has_no_business_tools(self):
        seen=[]
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200,json={"done":True,"message":{"role":"assistant","content":json.dumps({
                "answer":"red","observations":["red"],"uncertainty":"material unknown"})}})
        client=httpx.Client(transport=httpx.MockTransport(handler))
        with patch("vision_service.httpx.Client",return_value=client),patch("vision_service.load_api_key") as cloud,patch(
                "ollama_gateway.acquire_model_request"):
            result=ConfiguredVisionGateway()("color",[validate_image(fixture_image())],[],"live")
        cloud.assert_not_called()
        self.assertEqual(result["answer"],"red")
        self.assertTrue(seen[0]["messages"][-1]["images"])
        self.assertNotIn("tools",seen[0])

    def test_public_runtime_contains_no_urls_or_secrets(self):
        result=public_runtime()
        self.assertEqual(result["location"],"local")
        self.assertFalse(result["requires_image_consent"])
        self.assertNotIn("11434",json.dumps(result))

    def test_local_mode_does_not_enable_cloud_reranker_even_if_key_exists(self):
        from siliconflow_reranker import build_configured_reranker
        with patch.dict(os.environ,{"SILICONFLOW_API_KEY":"old-key-never-send"}),patch(
                "siliconflow_reranker.SiliconFlowReranker") as cloud:
            self.assertIsNone(build_configured_reranker())
        cloud.assert_not_called()


class ServiceGuidanceTests(unittest.TestCase):
    def test_greeting_and_missing_id_never_call_model_or_rag(self):
        for question in ("你好","查询订单怎么做","查询订单","帮我申请退货"):
            gateway=Mock()
            graph=build_agent_graph(PendingActionStore(),gateway)
            result=start_agent_graph(graph,AgentGraphStartRequest(thread_id="guidance",message=question,mode="live"))
            self.assertEqual(result.status,"COMPLETED")
            self.assertEqual(result.tool_steps,0)
            self.assertEqual(result.model_http_attempts,0)
            self.assertNotIn("没有找到足够证据",result.answer)
            gateway.request.assert_not_called()

    def test_guidance_does_not_swallow_policy_or_business_requests(self):
        for question in ("退款多久到账","查询订单 O-7101","你好，直接改数据库",
                         "帮我申请退货 O-7101","生鲜商品退货政策"):
            self.assertIsNone(service_guidance(question))
