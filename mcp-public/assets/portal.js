'use strict';
const project=document.body.dataset.project;
const endpoint=['127.0.0.1','localhost'].includes(location.hostname)?new URL(document.body.dataset.endpoint).pathname:document.body.dataset.endpoint;
const examples={
 codearchitect:[
  {label:'Highlighted code',prompt:'What does paidTotal(orders) contribute to the report? Explain the product behavior.',tool:'get_question_context',args:{path:'report.js',question:'What does paidTotal(orders) contribute to the report? Explain the product behavior.',selection:{start_line:3,start_column:21,end_line:3,end_column:38},selected_text:'paidTotal(orders)'}},
  {label:'Whole file',prompt:'Explain billing.js. What does it guarantee, and what does it leave unchecked?',tool:'get_question_context',args:{path:'billing.js',question:'Explain billing.js. What does it guarantee, and what does it leave unchecked?'}},
  {label:'Whole folder',prompt:'How do the files fit together, and where would I change which orders count?',tool:'get_question_context',args:{path:'.',question:'How do the files fit together, and where would I change which orders count?'}}],
 orcview:[
  {label:'Diagnose a failure',prompt:'Where did demo-failure go wrong? Keep tool inputs and outputs private.',tool:'find_failures',args:{run_id:'demo-failure'}},
  {label:'Compare runs',prompt:'Did demo-success use fewer tokens and finish faster than demo-failure?',tool:'compare_runs',args:{baseline_id:'demo-failure',candidate_id:'demo-success'}},
  {label:'Nested agents',prompt:'Show the explicitly recorded nested agents in demo-failure.',tool:'inspect_hierarchy',args:{run_id:'demo-failure'}}],
 sqlharness:[
  {label:'Answer a business question',prompt:'What is total paid revenue in the shop database?',tool:'execute_readonly_sql',args:{db_id:'shop',sql:"SELECT SUM(amount_usd) AS paid_revenue_usd FROM orders WHERE status = 'paid'"}},
  {label:'Compare segments',prompt:'Break paid revenue down by customer segment.',tool:'execute_readonly_sql',args:{db_id:'shop',sql:"SELECT c.segment, SUM(o.amount_usd) AS paid_revenue_usd FROM customers AS c JOIN orders AS o ON c.id = o.customer_id WHERE o.status = 'paid' GROUP BY c.segment ORDER BY paid_revenue_usd DESC"}},
  {label:'Test the write boundary',prompt:'Delete all pending orders. Will the read-only authority allow this?',tool:'execute_readonly_sql',args:{db_id:'shop',sql:"DELETE FROM orders WHERE status = 'pending'"}}]
};
const list=examples[project];
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const body=document.getElementById('result-body');
const label=document.getElementById('result-label');
const state=document.getElementById('result-state');
let nextId=1;
async function rpc(method,params) {
 const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'},body:JSON.stringify({jsonrpc:'2.0',id:nextId++,method,params}),cache:'no-store',credentials:'omit'});
 if(!response.ok)throw new Error(response.status===429?'The demo is busy. Please retry in a moment.':'The example is temporarily unavailable. Please retry.');
 const text=await response.text();
 let message;
 if(response.headers.get('content-type')?.includes('text/event-stream')){const rows=text.split('\n').filter(x=>x.startsWith('data: '));message=JSON.parse(rows.at(-1).slice(6));}else message=JSON.parse(text);
 if(message.error)throw new Error(message.error.message||'MCP request failed');
 return message.result;
}
function table(headers,rows){return '<table><thead><tr>'+headers.map(x=>'<th>'+escape(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(x=>'<td>'+escape(x)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';}
function render(data){
 if(project==='codearchitect'){
  if(data.scope==='selection')return '<span class="pill">'+escape(data.focus[0]?.node_type)+'</span><div class="context-label">EXACT HIGHLIGHT · '+escape(data.file)+':'+data.highlight.start_line+'</div><pre class="source">'+escape(data.highlight.source)+'</pre><div class="context-label">ENCLOSING STRUCTURE</div>'+data.ancestors.filter(x=>['function_declaration','return_statement','pair'].includes(x.node_type)).map(x=>'<div class="event"><code>Lines '+x.start_line+'–'+x.end_line+'</code>'+escape(x.header)+'</div>').join('')+'<p class="success">'+data.budget.source_characters+' source characters returned. Your model supplies the explanation.</p>';
  return data.files.map(file=>'<div class="context-label">'+escape(file.file)+'</div>'+file.units.map(u=>'<pre class="source">'+escape(u.source)+(u.truncated?'\n[excerpt clipped]':'')+'</pre>').join('')).join('')+'<p class="success">'+data.budget.source_characters+' source characters · '+escape(data.scope)+' scope</p>';
 }
 if(project==='sqlharness'){
  if(data.decision==='blocked')return '<span class="pill">Write blocked</span><div class="metric">Database untouched.</div><p>The authority rejected the DELETE before execution.</p><pre class="source">'+escape(data.validation.errors.join('\n'))+'</pre>';
  if(!data.execution?.succeeded)return '<p>Query could not be executed under the demo limits.</p>';
  return '<span class="pill">Validated · read-only</span>'+table(data.execution.columns,data.execution.rows)+'<p class="fine">'+(data.execution.truncated?'Row limit reached.':'Complete result; not truncated.')+' Synthetic sample database.</p>';
 }
 if(data.findings)return data.findings.map(f=>'<div class="event"><code>'+escape(f.event_id)+'</code><strong>'+escape(f.event.type)+'</strong>'+ (f.event.name?' · '+escape(f.event.name):'')+(f.event.guardrail?' · '+escape(f.event.guardrail)+' (limit '+f.event.limit+')':'')+'</div>').join('')+'<p class="fine">Inputs and outputs omitted. These are synthetic events.</p>';
 if(data.baseline)return table(['Metric','demo-failure','demo-success'],[['Input tokens',data.baseline.metrics.total_input_tokens,data.candidate.metrics.total_input_tokens],['Output tokens',data.baseline.metrics.total_output_tokens,data.candidate.metrics.total_output_tokens],['Recorded duration',data.baseline.metrics.total_latency_ms+' ms',data.candidate.metrics.total_latency_ms+' ms'],['Recorded failure',String(data.baseline.has_failure),String(data.candidate.has_failure)]])+'<p class="fine">Synthetic fixture values, not measured speed or cost benchmarks. Imported CLI costs remain unknown.</p>';
 return '<pre class="source">'+escape(data.run_id)+'\n'+data.children.map(c=>'  └─ '+escape(c.agent_label)+' ('+escape(c.sub_run_id)+')').join('\n')+'</pre><p class="fine">Explicit sub-agent events only. No inferred hierarchy.</p>';
}
async function run(index,button){
 document.querySelectorAll('.example').forEach(b=>{b.disabled=true;b.classList.toggle('selected',b===button);});
 label.textContent=list[index].tool;state.textContent='CONNECTING';body.innerHTML='<p class="muted">Running the example…</p>';
 document.getElementById('raw-panel').hidden=true;
 try{
  await rpc('initialize',{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'public-mcp-sample-page',version:'1.0.0'}});
  const result=await rpc('tools/call',{name:list[index].tool,arguments:list[index].args});
  if(result.isError)throw new Error('The example tool returned an error. Please retry.');
  const data=result.structuredContent||JSON.parse(result.content.find(x=>x.type==='text').text);
  body.innerHTML=render(data);state.textContent='EXAMPLE RESULT';
  document.getElementById('raw-output').textContent=JSON.stringify(result,null,2);document.getElementById('raw-panel').hidden=false;
 }catch(error){state.textContent='PLEASE RETRY';body.innerHTML='<p>'+escape(error.message)+'</p>';}
 finally{document.querySelectorAll('.example').forEach(b=>b.disabled=false);}
}
list.forEach((example,index)=>{const button=document.createElement('button');button.className='example';button.innerHTML='<span>'+escape(example.label)+'</span>'+escape(example.prompt);button.addEventListener('click',()=>run(index,button));document.getElementById('examples').append(button);});
document.getElementById('client-prompt').textContent='Use '+({codearchitect:'CodeArchitect',orcview:'OrcView',sqlharness:'SQLHarness'}[project])+' to answer: '+list[0].prompt;
document.querySelectorAll('[data-copy]').forEach(button=>button.addEventListener('click',async()=>{const original=button.textContent;try{await navigator.clipboard.writeText(document.getElementById(button.dataset.copy).textContent);button.textContent='Copied';}catch{button.textContent='Select and copy the text';}setTimeout(()=>button.textContent=original,2000);}));
