import json
from unittest.mock import patch

import httpx
from openai import OpenAI
import pytest
from orcview import Agent, tool
from orcview._providers import OpenAIAdapter


def response(output, status='completed'):
    return {'id':'resp_test','object':'response','created_at':0,'model':'gpt-5.4-mini',
            'status':status,'output':output,'usage':{'input_tokens':100,'output_tokens':20,
            'total_tokens':120,'input_tokens_details':{'cached_tokens':10}}}


def text(value):
    return {'type':'message','id':'msg_test','role':'assistant','status':'completed',
            'content':[{'type':'output_text','text':value,'annotations':[]}]}


def test_openai_tool_round_trip_keeps_reasoning_and_observable_events():
    requests=[]
    reasoning={'type':'reasoning','id':'rs_test','summary':[], 'encrypted_content':'opaque'}
    call={'type':'function_call','id':'fc_test','call_id':'call_test','name':'double',
          'arguments':'{"value":3}','status':'completed'}
    def handle(request):
        body=json.loads(request.content);requests.append(body)
        if len(requests)==1:
            return httpx.Response(200,json=response([reasoning,call]))
        assert body['input'][1:3]==[reasoning,call]
        assert body['input'][3]=={'type':'function_call_output','call_id':'call_test','output':'{"value": 6}'}
        return httpx.Response(200,json=response([text('The result is 6.')]))
    @tool
    def double(value: int) -> dict:
        """Double a number."""
        return {'value':value*2}
    client=OpenAI(api_key='test-only',http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    events=[]
    with patch('orcview._providers.create_client',return_value=OpenAIAdapter(client)):
        assert Agent(label='Test',tools=[double]).run('Double 3',emit=events.append)=='The result is 6.'
    assert all(r['store'] is False for r in requests)
    assert requests[0]['tools'][0]['parameters']['required']==['value']
    assert [e['type'] for e in events].count('tool_result')==1
    metrics=next(e for e in events if e['type']=='run_metrics')
    assert metrics['error'] is None and metrics['cycles']==2
    assert metrics['total_cost_usd']==round(2 * ((90 * .75 + 10 * .075 + 20 * 4.5) / 1_000_000), 6)
    assert events[-1]['type']=='done'


def test_openai_nested_agents_and_failed_credentials_finish_cleanly():
    requests=[]
    def handle(request):
        body=json.loads(request.content);requests.append(body)
        if body['instructions']=='parent' and len(body['input'])==1:
            output=[{'type':'function_call','id':'fc_child','call_id':'call_child','name':'child',
                     'arguments':'{"task":"inspect"}','status':'completed'}]
        else:
            output=[text('Complete')]
        return httpx.Response(200,json=response(output))
    client=OpenAI(api_key='test-only',http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    child=Agent(label='Child',tools=[],system='child')
    events=[]
    with patch('orcview._providers.create_client',return_value=OpenAIAdapter(client)):
        assert Agent(label='Parent',tools=[child],system='parent').run('Inspect',emit=events.append)=='Complete'
    assert any(e.get('_sub_run_id') and e['type']=='agent_text' for e in events)
    assert all(r['model']=='gpt-5.4-mini' for r in requests)
    assert '__sub_run_id__' not in json.dumps(requests)
    events=[]
    with patch('orcview._providers.create_client',side_effect=RuntimeError('Missing OPENAI_API_KEY')):
        Agent(label='Test',tools=[]).run('Hello',emit=events.append)
    assert events[-1]['type']=='done'
    assert next(e for e in events if e['type']=='run_metrics')['error']=='Missing OPENAI_API_KEY'


def test_incomplete_response_is_not_reported_as_success():
    client=OpenAI(api_key='test-only',http_client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200,json=response([],status='incomplete')))))
    with pytest.raises(RuntimeError,match='incomplete'):
        OpenAIAdapter(client).create(model='gpt-5.4-mini',max_tokens=2048,system='',tools=[],messages=[])
