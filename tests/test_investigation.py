from orcview._server import create_app
from test_web_privacy import FakeAgent
from orcview.mcp_public import create_public_app
from starlette.testclient import TestClient

def test_guided_entrypoint_preserves_runtime():
    with TestClient(create_public_app(create_app(FakeAgent())), base_url='http://localhost:9021') as client:
        assert 'Investigate sample run' in client.get('/').text
        assert 'Investigate this run' in client.get('/investigate').text
        assert 'cfg-max-cycles' in client.get('/runtime').text
        assert client.get('/static/investigation.js').status_code == 200
        headers={'accept':'application/json,text/event-stream'}
        def tool(name,args):
            response=client.post('/mcp/orcview/server',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})
            assert response.status_code==200
            return response.json()['result']['structuredContent']
        assert 'demo-failure' in tool('list_runs',{})['run_ids']
        hits=tool('find_failures',{'run_id':'demo-failure','include_content':True})['findings']
        tree=tool('inspect_hierarchy',{'run_id':'demo-failure'})
        failed=next(h for h in hits if h['event']['type']=='tool_result')
        assert failed['event']['_sub_run_id']==tree['children'][0]['sub_run_id']
        index=int(failed['event_id'].split(':e')[1])
        event=tool('inspect_run',{'run_id':'demo-failure','offset':index,'limit':1,'include_content':True})['events'][0]
        assert event==failed
        assert event['event']['result']=='synthetic timeout'
