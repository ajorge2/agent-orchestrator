import asyncio
from flask import Flask
from mcp import Client
from orcview.mcp_server import create_server
from orcview.cli_traces import CliTraceSource
from orcview.mcp_public import create_public_app
from starlette.testclient import TestClient

def test_public_asgi_pages_and_legacy_routes():
    legacy=Flask(__name__)
    @legacy.get('/')
    def root():return 'Original UI route'
    with TestClient(create_public_app(legacy),base_url='http://localhost:9021') as c:
        assert c.get('/').text=='Original UI route'
        assert c.get('/mcp/orcview/').status_code==200
        assert c.get('/mcp/install/orcview-0.2.1-py3-none-any.whl').status_code==200
        assert c.get('/mcp/health').json()['sample_only'] is True
        result=c.post('/mcp/sqlharness/server',headers={'accept':'application/json,text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}})
        assert result.status_code==200
        assert result.json()['result']['serverInfo']['name']=='SQLHarness Database Authority'

def test_orcview_public_contract_without_content():
    async def run():
        async with Client(create_server()) as c:
            assert len((await c.list_tools()).tools)==6
            failure=await c.call_tool('find_failures',{'run_id':'demo-failure'})
            assert failure.structured_content['total']==4
            assert all('input' not in f['event'] for f in failure.structured_content['findings'])
            comparison=await c.call_tool('compare_runs',{'baseline_id':'demo-failure','candidate_id':'demo-success'})
            assert comparison.structured_content['delta']['total_latency_ms']==-50
    asyncio.run(run())
