from orcview._server import create_app
class FakeAgent:
    label='Example'; model='example-model'; max_cycles=3; system='public default'; _tools=[]; enabled_tools=set(); record_runs=False
    def _tool_name(self, tool): return 'example'
    def run(self, question, emit=None, **kwargs):
        assert not self.record_runs
        emit({'type':'run_metrics','run_id':'test-run','cycles':1,'total_cost_usd':0,'total_latency_ms':1})
        emit({'type':'done'})
def test_browser_configuration_history_and_tickets_are_private():
    app=create_app(FakeAgent());a=app.test_client();b=app.test_client()
    assert a.post('/config',json={'label':'private-label','system_prompt':'private instructions'}).status_code==200
    assert b.get('/config').json['label']=='Example'
    assert a.get('/config').json['label']=='private-label'
    ticket=a.post('/run',json={'question':'private question'}).json['ticket']
    assert b.get('/run?ticket='+ticket).status_code==404
    assert a.get('/run?ticket='+ticket).status_code==200
    assert b.get('/runs').json==[]
    assert b.get('/runs/test-run').status_code==404
    assert a.get('/runs').json[0]['question']=='private question'
    assert a.get('/runs/test-run').json['events']
    assert a.get('/run?ticket='+ticket).status_code==404
    assert a.get('/run?question=private').status_code==405
    assert a.post('/config',json={},headers={'Origin':'https://hostile.example'}).status_code==403
    assert 'HttpOnly' in a.get('/config').headers['Set-Cookie']
    assert a.get('/runs').headers['Cache-Control']=='no-store'
def test_sdk_recording_is_explicit_opt_in():
    from orcview import Agent
    assert Agent(label='private',tools=[]).record_runs is False
    assert Agent(label='local opt-in',tools=[],record_runs=True).record_runs is True
