from __future__ import annotations
import asyncio
import hashlib
import json
import socket
from mcp import Client
from sqlharness.mcp_server import create_server, create_demo_database


def test_synthetic_workflow_without_network_or_data_writes(tmp_path,monkeypatch):
    root=create_demo_database(tmp_path)
    path=root/'shop/shop.sqlite'
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    files=sorted(p.relative_to(root) for p in root.rglob('*'))
    def no_network(*a,**k):
        raise AssertionError('Unexpected network call')
    monkeypatch.setattr(socket.socket,'connect',no_network)
    async def run():
        async with Client(create_server(root,max_rows=2)) as client:
            schema=await client.call_tool('inspect_schema',{'db_id':'shop'})
            assert {t['name'] for t in schema.structured_content['tables']}=={'customers','orders'}
            good=await client.call_tool('execute_readonly_sql',{'db_id':'shop','sql':"SELECT SUM(amount_usd) AS total FROM orders WHERE status = 'paid'"})
            assert good.structured_content['execution']['rows']==[[1220.0]]
            for sql in ['DROP TABLE orders','DELETE FROM orders','SELECT * FROM orders; DELETE FROM orders', "ATTACH DATABASE '/tmp/forbidden.sqlite' AS other", 'PRAGMA user_version']:
                blocked=await client.call_tool('execute_readonly_sql',{'db_id':'shop','sql':sql})
                assert blocked.structured_content['decision']=='blocked'
            huge=await client.call_tool('execute_readonly_sql',{'db_id':'shop','sql':'SELECT zeroblob(100000000) AS oversized'})
            assert huge.structured_content['decision']=='execution_failed'
            rows=await client.call_tool('execute_readonly_sql',{'db_id':'shop','sql':'SELECT * FROM orders'})
            assert rows.structured_content['execution']['truncated'] is True
            assert len(rows.structured_content['execution']['rows'])==2
            invalid=await client.call_tool('inspect_schema',{'db_id':'../PRIVATE_SENTINEL'})
            assert invalid.is_error
            missing=await client.call_tool('inspect_schema',{'db_id':'missing'})
            assert missing.is_error
            assert str(root) not in json.dumps(missing.model_dump())
            assert (await client.call_tool('validate_sql',{'db_id':'shop','sql':'x'*16001})).is_error
    asyncio.run(run())
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert sorted(p.relative_to(root) for p in root.rglob('*'))==files
