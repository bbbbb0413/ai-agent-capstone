import asyncio,json
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from langchain_core.messages import AIMessage
from agent import graph,signals,harness
rows=[]
def record(name,expected,actual):
 rows.append(dict(name=name,expected=expected,actual=actual,passed=actual==expected))
for name,sample,expect in [
 ('TC05_normal',{'error_rate':.01,'p95_seconds':.3},True),
 ('TC05_null',{'error_rate':.01,'p95_seconds':None},False),
 ('TC05_missing',{'error_rate':.01},False),
 ('TC05_negative',{'error_rate':-.1,'p95_seconds':-.3},False),
 ('TC05_string',{'error_rate':.01,'p95_seconds':'0.3'},False),
 ('TC05_at_error_boundary',{'error_rate':.2,'p95_seconds':.3},False),
 ('TC05_at_latency_boundary',{'error_rate':.01,'p95_seconds':1},False)]:
 try: actual=signals.within_thresholds(sample)
 except Exception as e:actual=type(e).__name__
 record(name,expect,actual)
text='원인 후보:\n1) RB-STD-03 트래픽 급증\n2) RB-STD-05 DB 지연\n처리할 원인: RB-STD-03'
for share,expected in [(.49,'RB-STD-03'),(.5,'RB-STD-05'),(.51,'RB-STD-05')]:
 facts=[{'clause':'RB-STD-03','evidence':True},{'clause':'RB-STD-05','evidence':True,'dependency_share':share}]
 record('TC08_'+str(share),expected,graph.parse_causes(text,facts)[1])
record('parser_markdown_target','RB-STD-03',graph.parse_causes(text.replace('처리할 원인:','**처리할 원인:**'))[1])
async def proposal(turns):
 ai=AIMessage(content='synthetic invalid proposal',tool_calls=[{'id':'c1','name':'business_execute_runbook_action','args':{'action':'restart_pods','service':'order-api','reason':'RB-STD-02'}}])
 model=SimpleNamespace(ainvoke=AsyncMock(return_value=ai))
 tool=SimpleNamespace(name='business_execute_runbook_action')
 box=SimpleNamespace(call=AsyncMock(return_value=SimpleNamespace(content='[]')),for_node=lambda n:[tool])
 state={'task_id':'audit','incident_id':1,'turns':turns,'tool_counts':{},'signals':{},'context':{},'findings':'synthetic','runbook':[],'handled':[]}
 with patch('agent.graph.harness.chat_model',return_value=model),patch('agent.graph.action_guards.first_problem',return_value='always invalid') as guard:
  result=await graph.propose_action(state,SimpleNamespace(context=SimpleNamespace(check_time=lambda:None,toolbox=box)))
 record('proposal_invalid_turns_'+str(turns),None,result.get('proposed_action'))
 rows.append({'name':'proposal_call_counts_'+str(turns),'model_calls':model.ainvoke.await_count,'guard_calls':guard.call_count})
asyncio.run(proposal(0));asyncio.run(proposal(7))
print(json.dumps(rows,ensure_ascii=False,indent=2))
