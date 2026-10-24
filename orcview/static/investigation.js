"use strict";
const endpoint='/mcp/orcview/server';
const runId='demo-failure';
const $=id=>document.getElementById(id);
let requestId=0,busy=false,children=[];
const titles={retry_scheduled:'Retry scheduled',tool_result:'Order lookup failed',guardrail_triggered:'Tool-call limit reached',error:'Run ended with an error'};
function node(tag,text,className){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;}
async function rpc(method,params){
 const request={jsonrpc:'2.0',id:++requestId,method,params};
 const item=node('li');item.append(node('strong',method==='tools/call'?params.name:'MCP connection'));if(method==='tools/call')item.append(node('p',JSON.stringify(params.arguments),'citation'));const progress=node('p','Requesting…','muted');item.append(progress);
 const details=node('details');details.append(node('summary','Request and response'));const req=node('pre',JSON.stringify(request,null,2));details.append(req);item.append(details);$('calls').append(item);
 try{
  const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'},body:JSON.stringify(request),cache:'no-store',credentials:'omit'});
  if(!response.ok)throw new Error(response.status===429?'Demo busy. Retry shortly.':'MCP request failed (HTTP '+response.status+').');
  const text=await response.text();const message=response.headers.get('content-type')?.includes('text/event-stream')?JSON.parse(text.split('\n').filter(l=>l.startsWith('data: ')).at(-1).slice(6)):JSON.parse(text);
  details.append(node('pre',JSON.stringify(message,null,2)));
  if(message.error||message.result?.isError)throw new Error(message.error?.message||'The MCP tool returned an error. Inspect the response.');
  progress.textContent='Completed';return message.result;
 }catch(e){progress.textContent=e.message;throw e;}
}
async function tool(name,args){const result=await rpc('tools/call',{name,arguments:args});return result.structuredContent||JSON.parse(result.content.find(c=>c.type==='text').text);}
function owner(event){const id=event._sub_run_id;return id?(children.find(c=>c.sub_run_id===id)?.agent_label||id):'Coordinator';}
function hierarchy(selected){$('hierarchy').replaceChildren();const root=node('div','Coordinator','agent'+(!selected?' active':''));$('hierarchy').append(root);children.forEach(c=>{$('hierarchy').append(node('div',c.agent_label+' · '+c.sub_run_id,'agent child'+(c.sub_run_id===selected?' active':'')));});}
async function selectFinding(finding,button){
 if(busy)return;busy=true;document.querySelectorAll('.finding').forEach(b=>b.disabled=true);$('status').textContent='Retrieving the cited event…';
 try{const index=Number(finding.event_id.split(':e').at(-1));const data=await tool('inspect_run',{run_id:runId,offset:index,limit:1,include_content:true});const hit=data.events.find(e=>e.event_id===finding.event_id);if(!hit)throw new Error('The cited event was not returned. Retry the investigation.');
 hierarchy(hit.event._sub_run_id);$('event-citation').textContent=hit.event_id+' · '+owner(hit.event);$('event-json').textContent=JSON.stringify(hit.event,null,2);
 $('event-explanation').textContent=hit.event.type==='tool_result'?'The recorded order lookup returned an error: '+hit.event.result+'.':hit.event.type==='guardrail_triggered'?'The analyst recorded a '+hit.event.guardrail+' guardrail with limit '+hit.event.limit+'.':hit.event.type==='retry_scheduled'?'A retry was scheduled for the analyst. A retry alone does not establish that the run failed.':'The coordinator recorded: '+hit.event.message+'.';
 $('evidence-panel').hidden=false;document.querySelectorAll('.finding').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));$('status').textContent='Evidence retrieved from '+hit.event_id+'.';$('evidence-panel').focus();
 }catch(e){$('status').textContent=e.message;}finally{busy=false;document.querySelectorAll('.finding').forEach(b=>b.disabled=false);}
}
$('investigate').addEventListener('click',async()=>{
 if(busy)return;busy=true;$('investigate').disabled=true;$('calls').replaceChildren();$('findings').replaceChildren(node('p','Retrieving the run…','muted'));$('evidence-panel').hidden=true;$('hierarchy-panel').hidden=true;$('status').textContent='Connecting to the MCP server…';
 try{
  await rpc('initialize',{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'orcview-guided-investigation',version:'1.0.0'}});
  const runs=await tool('list_runs',{});if(!runs.run_ids.includes(runId))throw new Error('The example run is unavailable. Retry shortly.');
  const run=await tool('inspect_run',{run_id:runId,include_content:true});
  const failures=await tool('find_failures',{run_id:runId,include_content:true});const tree=await tool('inspect_hierarchy',{run_id:runId});children=tree.children;
  $('findings').replaceChildren(node('p',failures.total+' diagnostic events · '+run.event_count+' recorded events','muted'));
  failures.findings.forEach(f=>{const button=node('button',undefined,'finding');button.type='button';button.setAttribute('aria-pressed','false');button.append(node('strong',titles[f.event.type]||f.event.type),node('small',owner(f.event)+' · '+f.event_id));button.addEventListener('click',()=>selectFinding(f,button));$('findings').append(button);});
  if(!failures.total)$('findings').append(node('p','No diagnostic events returned.'));
  hierarchy(null);$('hierarchy-panel').hidden=false;$('status').textContent='Investigation ready. Select a finding to retrieve its exact event.';$('investigate').textContent='Run investigation again';
 }catch(e){$('status').textContent=e.message;$('findings').replaceChildren(node('p','Investigation incomplete. Retry to retrieve fresh evidence.'));}finally{busy=false;$('investigate').disabled=false;}
});
