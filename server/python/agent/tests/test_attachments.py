import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from agent import attachments, graph, harness
from agent.toolbox import ToolBox, load_tools


def file(name='error.log', data=b'ERROR timeout', kind='text/plain'):
    return {'id':'file-1','filename':name,'mediaType':kind,'content':base64.b64encode(data).decode()}


class AttachmentTests(unittest.TestCase):
    def test_log_lines_redacted_and_long_input_marks_omission(self):
        text, truncated=attachments.excerpt('password=private123\nAuthorization: Bearer token123\nERROR timeout')
        self.assertNotIn('private123',text);self.assertNotIn('token123',text)
        self.assertIn('L3: ERROR timeout',text);self.assertFalse(truncated)
        text,truncated=attachments.excerpt('ERROR line\n'*5000)
        self.assertTrue(truncated);self.assertIn('중간 내용 생략',text);self.assertIn('L5000:',text)

    def test_image_is_sent_as_multimodal_input_and_not_just_filename(self):
        parts,evidence=attachments.content_parts([file('error.png',b'png-bytes','image/png')])
        self.assertEqual(parts[1]['type'],'image_url')
        self.assertTrue(parts[1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertNotIn('content',evidence[0]);self.assertEqual(evidence[0]['filename'],'error.png')

    def test_attachment_route_ignores_no_incident_and_never_proposes_actions(self):
        state={'type':'ATTACHMENT','question':'order-api 오류','context':{'open_incidents':[], 'services':[{'name':'order-api'}]}}
        self.assertEqual(graph.route_triage(state),'analyze_attachments')
        self.assertEqual(graph.route_after_runbook(state),'report')
        self.assertEqual(graph.route_report({**state,'incident_id':1,'notice_draft':'ignore this'}),'__end__')
        self.assertTrue(harness.NODE_TOOLS['analyze_attachments'].isdisjoint(harness.DESTRUCTIVE_TOOLS))

    def test_bad_files_are_not_silently_ignored(self):
        with self.assertRaises(ValueError):attachments.content_parts([])
        with self.assertRaises(ValueError):attachments.content_parts([file(kind='text/html')])


class AttachmentFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_graph_uses_file_metrics_and_runbook_without_mutating_tools(self):
        calls=[]
        async def call(node,name,args,counts):
            calls.append(name)
            payload=([{'uri':'runbook://db','section':'DB-1','text':'DB 연결 확인'}] if name=='rag_search_documents'
                     else [] if name=='business_list_incidents' else {'name':'A','data':[]})
            return ToolMessage(content=json.dumps(payload),status='success',tool_call_id='t')
        runtime=graph.Context(toolbox=SimpleNamespace(call=call))
        class Model:
            async def ainvoke(self,messages):
                self.messages=messages
                return AIMessage(content='error.log L1: timeout 관측. 원인 미확정. DB 연결을 확인한다.')
            async def astream(self,messages):
                assert 'runbook://db' in messages[1].content
                yield AIMessageChunk(content='첨부 로그와 DB-1을 대조한 분석')
        model=Model()
        state={'task_id':'t','tenant_id':'tenant-a','type':'ATTACHMENT','question':'분석해줘','trace_id':'trace'}
        with patch.object(attachments.backend,'attachments',AsyncMock(return_value=[file()])), \
             patch.object(attachments.backend,'emit',AsyncMock()),patch.object(harness,'chat_model',return_value=model):
            result=await graph.build(None).ainvoke(state,context=runtime)
        self.assertIn('대조한 분석',result['report'])
        self.assertEqual(result['runbook'][0]['section'],'DB-1')
        self.assertEqual(result['evidence'][0]['filename'],'error.log')
        self.assertFalse(set(calls)&harness.DESTRUCTIVE_TOOLS)
        self.assertNotIn('image_url',json.dumps(result,default=str))
        self.assertIn('ERROR timeout',str(model.messages[1].content))

    async def test_unavailable_tools_do_not_hide_attachment_or_claim_monitoring_is_healthy(self):
        model=SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content='첨부에서 오류 확인. 현재 상태 확인 불가.')))
        runtime=graph.Context(toolbox=ToolBox([], 'tenant-a','t'))
        state={'task_id':'t','tenant_id':'tenant-a','type':'ATTACHMENT','question':'분석','tool_counts':{}}
        with patch.object(attachments.backend,'attachments',AsyncMock(return_value=[file()])), \
             patch.object(attachments.backend,'emit',AsyncMock()),patch.object(harness,'chat_model',return_value=model):
            result=await attachments.analyze(state,SimpleNamespace(context=runtime))
        self.assertIn('확인 불가',result['findings'])
        self.assertIn('error',result['evidence'][-1]['summary'])

    async def test_partial_tool_loading_keeps_available_server(self):
        async def fetch(*,server_name):
            if server_name=='business': return ['business_tool']
            raise ConnectionError('offline')
        with patch('agent.toolbox.MultiServerMCPClient') as client:
            client.return_value.get_tools=AsyncMock(side_effect=fetch)
            self.assertEqual(await load_tools('tenant-a','t','trace',allow_partial=True),['business_tool'])


class AttachmentAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_model_key_reports_configuration_help_without_raw_error(self):
        import httpx
        from openai import AuthenticationError
        from agent.runner import Runner
        response=httpx.Response(401,request=httpx.Request('POST','https://api.openai.com/v1/chat/completions'))
        failure=AuthenticationError('do-not-display-secret',response=response,body=None)
        runner=Runner(SimpleNamespace(ainvoke=AsyncMock(side_effect=failure)),None)
        runner._elapsed=AsyncMock(return_value=0)
        runner._add_elapsed=AsyncMock();runner._release=AsyncMock()
        state={'tenant_id':'tenant-a','trace_id':'trace','type':'ATTACHMENT'}
        with patch('agent.runner.load_tools',AsyncMock(return_value=[])), patch('agent.runner.backend.result',AsyncMock()) as result:
            status=await runner._drive_claimed('task',state,state,{},True)
        self.assertEqual(status,'FAILED')
        answer=result.await_args.kwargs['answer']
        self.assertIn('OPENAI_API_KEY',answer);self.assertNotIn('do-not-display-secret',answer)
