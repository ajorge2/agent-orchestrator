"""Optional one-command MCP registration; never touches model authentication."""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

def setup_plan(project_root: Path, cli='both', *, executable=None):
    root=project_root.resolve()
    if not root.is_dir():raise ValueError('Project root must exist')
    python=executable or sys.executable
    launch=[python,'-B','-m','orcview.mcp_server','--project-root',str(root),'--cli',cli]
    targets=['codex','claude'] if cli=='both' else [cli]
    commands=[]
    for target in targets:
        args=([target,'mcp','add','orcview','--'] if target=='codex' else
              [target,'mcp','add','--transport','stdio','--scope','local','orcview','--'])
        commands.append({'cli':target,'command':args+launch,'cwd':str(root),
            'config_scope':'user' if target=='codex' else 'local project'})
    return {'commands':commands,'credentials_required':False,'model_calls':False,
            'storage':'No new trace store. Existing CLI session logs are read-only.',
            'note':'Codex registration is user-wide but this server reads only the selected project. Setup never edits model, login or API-key settings. Existing same-name registrations are not overwritten.'}

def main(argv=None):
    p=argparse.ArgumentParser(description='Register OrcView for your existing CLI account; no new credentials')
    p.add_argument('--project-root',type=Path,required=True)
    p.add_argument('--cli',choices=['codex','claude','both'],default='both')
    p.add_argument('--install',action='store_true',help='Execute registration; otherwise print the plan')
    args=p.parse_args(argv)
    try:plan=setup_plan(args.project_root,args.cli)
    except ValueError:p.error('Project root must be an existing directory')
    if not args.install:
        print(json.dumps(plan,indent=2));return
    results=[]
    for item in plan['commands']:
        target=item['cli']
        binary=shutil.which(target)
        if binary is None:
            results.append({'cli':target,'installed':False,'reason':'CLI not found on PATH'});continue
        try:
            # Never silently overwrite a user's existing server registration.
            existing=subprocess.run([binary,'mcp','get','orcview'],cwd=item['cwd'],capture_output=True,timeout=20)
            if existing.returncode==0:
                results.append({'cli':target,'installed':False,'reason':'Existing orcview registration retained; inspect it with your CLI'});continue
            result=subprocess.run([binary,*item['command'][1:]],cwd=item['cwd'],capture_output=True,timeout=30)
            results.append({'cli':target,'installed':result.returncode==0,'reason':'Registered; restart your CLI session' if result.returncode==0 else 'Registration failed; run the printed plan manually'})
        except (OSError,subprocess.TimeoutExpired):
            results.append({'cli':target,'installed':False,'reason':'CLI unavailable or timed out'})
    print(json.dumps({'results':results,'credentials_required':False},indent=2))
    if any(not r['installed'] for r in results):raise SystemExit(1)
