"""Read-only adapters for local Codex and Claude Code JSONL session logs.

No model calls, auth-file reads, telemetry, background collectors or copied logs.
Log formats are version-dependent; unknown records are ignored and counted.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

MAX_BYTES = 16 * 1024 * 1024
MAX_LINE = 1024 * 1024
MAX_EVENTS = 10000
MAX_FILES = 5000

def _records(path):
    consumed=0
    with path.open('rb') as stream:
        for number in range(1, 50001):
            raw=stream.readline(MAX_LINE+1)
            if not raw:
                return
            consumed+=len(raw)
            if len(raw)>MAX_LINE or consumed>MAX_BYTES:
                yield number, {'_truncated': True}
                return
            try:
                value=json.loads(raw)
            except (ValueError,UnicodeDecodeError):
                continue # A live session may end with a partial line.
            if isinstance(value,dict):
                yield number,value
        yield 50001, {'_truncated':True}

def _cwd(path, provider):
    try:
        for i,record in _records(path):
            if i>30:
                break
            if provider=='codex' and record.get('type')=='session_meta':
                return record.get('payload',{}).get('cwd')
            if provider=='claude' and record.get('cwd'):
                return record['cwd']
    except (OSError,ValueError):
        pass
    return None

def _public_id(provider,path,root):
    return provider+'-'+hashlib.sha256(str(path.relative_to(root)).encode()).hexdigest()[:24]

def _number(value):
    return value if type(value) in (int,float) and 0<=value<10**15 else None

def _duration(start,end):
    try:
        return max(0,round((datetime.fromisoformat(end.replace('Z','+00:00'))-datetime.fromisoformat(start.replace('Z','+00:00'))).total_seconds()*1000))
    except (TypeError,ValueError):
        return None

class CliTraceSource:
    def __init__(self, project_root, cli='both', *, home=None, codex_root=None, claude_root=None):
        self.project=Path(project_root).resolve()
        if not self.project.is_dir() or cli not in {'codex','claude','both'}:
            raise ValueError('Select an existing project and a supported CLI')
        base=Path(home) if home is not None else Path.home()
        self.roots={
            'codex':Path(codex_root) if codex_root is not None else Path(os.environ.get('CODEX_HOME',str(base/'.codex')))/'sessions',
            'claude':Path(claude_root) if claude_root is not None else base/'.claude/projects',
        }
        self.providers=['codex','claude'] if cli=='both' else [cli]
        self.discovery_limited=False

    def discover(self):
        found=[]
        self.discovery_limited=False
        for provider in self.providers:
            root=self.roots[provider].resolve()
            if not root.is_dir():
                continue
            search=root
            if provider=='claude':
                # Claude Code encodes project paths in this directory name.
                search=root/re.sub(r'[^A-Za-z0-9]', '-', str(self.project))
                if not search.is_dir():
                    continue
            candidates=[]
            for index,path in enumerate(search.rglob('*.jsonl')):
                if index>=MAX_FILES:
                    self.discovery_limited=True
                    break
                try:
                    if path.is_symlink() or not path.resolve().is_relative_to(root):
                        continue
                    candidates.append((path.stat().st_mtime,path))
                except OSError:
                    continue
            for _,path in sorted(candidates,reverse=True)[:200]:
                cwd=_cwd(path,provider)
                if cwd is None:
                    continue
                try:
                    if not Path(cwd).resolve().is_relative_to(self.project):
                        continue
                except (OSError,ValueError):
                    continue
                meta={}
                for n,record in _records(path):
                    if n>30:break
                    if provider=='codex' and record.get('type')=='session_meta':
                        meta=record.get('payload',{});break
                found.append({'run_id':_public_id(provider,path,root),'provider':provider,'path':path,'root':root,
                              'session_id':meta.get('id',meta.get('session_id')),
                              'parent_session_id':meta.get('parent_thread_id')})
            if len(candidates)>200:
                self.discovery_limited=True
        by_session={r['session_id']:r['run_id'] for r in found if r.get('session_id')}
        by_path={r['path']:r['run_id'] for r in found}
        for run in found:
            parent=by_session.get(run.get('parent_session_id'))
            if run['provider']=='claude' and run['path'].parent.name=='subagents':
                container=run['path'].parent.parent
                parent=by_path.get(container.parent/(container.name+'.jsonl'))
            run['parent_run_id']=parent
        return found

    def hierarchy(self,run_id):
        runs=self.discover()
        by_id={r['run_id']:r for r in runs}
        if run_id not in by_id:raise ValueError('Unknown run')
        top=run_id
        visited=set()
        while by_id.get(top,{}).get('parent_run_id') and top not in visited:
            visited.add(top);top=by_id[top]['parent_run_id']
        selected={top}
        for _ in range(len(runs)):
            expanded=selected|{r['run_id'] for r in runs if r.get('parent_run_id') in selected}
            if expanded==selected:break
            selected=expanded
        return {'root_run_id':top,'runs':[{'run_id':r['run_id'],'provider':r['provider'],'parent_run_id':r['parent_run_id']} for r in runs if r['run_id'] in selected],
                'note':'Only explicit parent metadata or Claude subagent paths are used. Missing relationships remain unknown.'}

    def status(self):
        runs=self.discover()
        return {'mode':'cli','providers':self.providers,'run_count':len(runs),'discovery_limited':self.discovery_limited,
            'setup':'No instrumentation needed for supported CLI logs. Start a normal CLI session in the configured project.',
            'retention':'CLI-owned session logs remain where the CLI writes them. OrcView does not copy or delete them.',
            'capabilities':{'tools':True,'reasoning_length_when_logged':True,'tokens_when_logged':True,'nested_runs_when_logged':True,'billed_cost':False,'provider_latency':False},
            'limits':'Newest 200 files per provider; at most 5000 paths discovered; 16 MB and 10000 normalized events per run. Log schemas may change.'}

    def load(self,run_id,include_content=False):
        match=next((r for r in self.discover() if r['run_id']==run_id),None)
        if match is None:
            raise ValueError('Unknown project-scoped CLI run')
        return normalize(match['path'],match['provider'],run_id,include_content=include_content)

def normalize(path,provider,run_id,*,include_content=False):
    events=[]
    first=None
    last=None
    tools={}
    usage=None
    messages={}
    truncated=False
    ignored=0
    reasoning_chars=0
    reasoning_seen=False
    parent_session=None
    child_agent=None
    def emit(kind,record,line,**fields):
        nonlocal first,last
        stamp=record.get('timestamp')
        if isinstance(stamp,str):
            first=first or stamp
            last=stamp
        event={'type':kind,'source_line':line,**fields}
        if stamp is not None:
            event['timestamp']=stamp
        events.append(event)
    def text_content(value,limit=4000):
        if isinstance(value,str):
            text=value
        elif isinstance(value,list):
            text='\n'.join(str(b.get('text','')) for b in value if isinstance(b,dict))
        else:
            text=''
        return text if limit is None else text[:limit]
    for line,r in _records(path):
        if r.get('_truncated') or len(events)>=MAX_EVENTS-1:
            truncated=True
            break
        stamp=r.get('timestamp')
        if isinstance(stamp,str):
            first=first or stamp
            last=stamp
        if provider=='codex':
            p=r.get('payload') or {}
            if not isinstance(p,dict):
                ignored+=1;continue
            kind=p.get('type')
            if r.get('type')=='session_meta':
                parent_session=p.get('parent_thread_id')
                emit('started',r,line)
            elif r.get('type')=='turn_context':
                emit('run_config',r,line,model=p.get('model'))
            elif r.get('type')=='token_usage_record':
                candidate=p.get('thread_token_usage')
                if isinstance(candidate,dict):usage=candidate
            elif r.get('type')=='event_msg' and kind=='token_count':
                candidate=(p.get('info') or {}).get('total_token_usage')
                if isinstance(candidate,dict):usage=candidate
            elif r.get('type')=='response_item' and kind in {'function_call','custom_tool_call'}:
                call_id=p.get('call_id')
                name=p.get('name','unknown')
                tools[call_id]=(name,stamp)
                extra={'input':p.get('arguments',p.get('input'))} if include_content else {}
                emit('tool_call',r,line,name=name,call_id=call_id,**extra)
            elif r.get('type')=='response_item' and kind in {'function_call_output','custom_tool_call_output'}:
                call_id=p.get('call_id')
                name,started=tools.get(call_id,('unknown',None))
                # Codex text output does not reliably encode success; do not infer it from prose.
                output=p.get('output')
                failed=output.get('is_error') if isinstance(output,dict) and type(output.get('is_error')) is bool else None
                extra={'result':text_content(output) if not isinstance(output,dict) else json.dumps(output)[:4000]} if include_content else {}
                emit('tool_result',r,line,name=name,call_id=call_id,is_error=failed,latency_ms=_duration(started,stamp),**extra)
            elif r.get('type')=='response_item' and kind=='reasoning':
                summaries=p.get('summary') or []
                text=text_content(summaries,limit=None)
                reasoning_seen=reasoning_seen or bool(text)
                reasoning_chars+=len(text)
                emit('reasoning',r,line,visible_summary_characters=len(text),**({'text':text[:4000]} if include_content else {}))
            elif r.get('type')=='event_msg' and kind in {'error','task_failed'}:
                emit('error',r,line,**({'message':str(p.get('message',''))[:4000]} if include_content else {'message':'CLI reported an error; content omitted'}))
            elif r.get('type')=='event_msg' and kind in {'task_complete','task_completed'}:
                emit('done',r,line)
            else:ignored+=1
        else:
            child_agent=child_agent or r.get('agentId')
            m=r.get('message') or {}
            if not isinstance(m,dict):ignored+=1;continue
            if r.get('type')=='assistant':
                message_id=m.get('id')
                raw_usage=m.get('usage')
                if message_id and isinstance(raw_usage,dict):
                    prior=messages.setdefault(message_id,{})
                    for key,value in raw_usage.items():
                        if _number(value) is not None:prior[key]=max(prior.get(key,0),value)
                for b in m.get('content') or []:
                    if not isinstance(b,dict):continue
                    if b.get('type')=='tool_use':
                        call_id=b.get('id');name=b.get('name','unknown')
                        tools[call_id]=(name,stamp)
                        extra={'input':b.get('input')} if include_content else {}
                        emit('tool_call',r,line,name=name,call_id=call_id,**extra)
                    elif b.get('type')=='thinking':
                        count=len(b.get('thinking') or '')
                        reasoning_seen=True;reasoning_chars+=count
                        emit('reasoning',r,line,visible_summary_characters=count,**({'text':str(b.get('thinking',''))[:4000]} if include_content else {}))
                    elif b.get('type')=='text':
                        emit('assistant_message',r,line,characters=len(b.get('text') or ''),**({'text':str(b.get('text',''))[:4000]} if include_content else {}))
                if r.get('isApiErrorMessage') is True:
                    emit('error',r,line,message='CLI reported a provider error')
            elif r.get('type')=='user' and isinstance(m.get('content'),list):
                for b in m['content']:
                    if not isinstance(b,dict) or b.get('type')!='tool_result':continue
                    call_id=b.get('tool_use_id');name,started=tools.get(call_id,('unknown',None))
                    extra={'result':text_content(b.get('content'))} if include_content else {}
                    emit('tool_result',r,line,name=name,call_id=call_id,is_error=b.get('is_error',False),latency_ms=_duration(started,stamp),**extra)
            elif r.get('type')=='system' and r.get('subtype')=='api_error':
                emit('error',r,line,message='CLI reported a provider error')
            else:ignored+=1
    if provider=='claude' and messages:
        keys=('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens')
        usage={k:sum(v.get(k,0) for v in messages.values()) for k in keys}
    input_tokens=_number(usage.get('input_tokens')) if usage else None
    if provider=='claude' and usage and input_tokens is not None:
        input_tokens+=usage.get('cache_read_input_tokens',0)+usage.get('cache_creation_input_tokens',0)
    metrics={'type':'run_metrics','total_input_tokens':input_tokens,
        'total_output_tokens':_number(usage.get('output_tokens')) if usage else None,
        'total_cost_usd':None,'total_latency_ms':None,'observed_duration_ms':_duration(first,last),
        'tool_calls':sum(e['type']=='tool_call' for e in events),'cycles':None,
        'visible_reasoning_characters':reasoning_chars if reasoning_seen else None}
    events.append(metrics)
    return {'run_id':run_id,'synthetic':False,'provider':provider,'events':events,'content_included':include_content,
        'source_truncated':truncated,'ignored_records':ignored,'parent_session_id':parent_session,'child_agent_id':child_agent,
        'limitations':'CLI logs are partial observations, not provider internals. Cost and model latency are unknown. Reasoning length counts only visible logged text. Codex output success may be unknown. Reading the active session can include earlier OrcView tool calls.'}
