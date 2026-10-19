'use strict';
const el = id => document.getElementById(id);
const labels = {open:'Open',in_progress:'In progress',waiting:'Waiting',resolved:'Resolved'};
let selected = null, listRequest = 0, detailRequest = 0, searchTimer;
async function api(path, payload) {
  const options = payload === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'The request failed');
  return data;
}
function text(tag, value, className) { const node=document.createElement(tag);node.textContent=value;if(className)node.className=className;return node; }
function showError(id, message) {el(id).textContent=message;el(id).hidden=!message;}
async function loadCases() {
  const version=++listRequest;
  const params=new URLSearchParams({q:el('search').value,status:el('filter-status').value,priority:el('filter-priority').value});
  try {
    const data=await api('/api/cases?'+params);
    if(version!==listRequest)return;
    showError('global-error','');
    for(const [key,value] of Object.entries(data.counts))el('count-'+key).textContent=value;
    el('result-count').textContent=data.items.length+' shown · max '+data.limit;
    el('cases').replaceChildren();
    if(!data.items.length)el('cases').append(text('p','No matching cases. Create one or change your filters.','muted'));
    for(const item of data.items) {
      const button=document.createElement('button');button.className='case-row'+(selected===item.id?' selected':'');button.setAttribute('aria-pressed',String(selected===item.id));
      const row=text('div','','row-top');row.append(text('span','#'+String(item.id).padStart(4,'0'),'muted'),text('span',item.priority,'badge '+item.priority));
      button.append(row,text('strong',item.title),text('small',labels[item.status]+' · '+item.category));
      button.onclick=()=>openCase(item.id);el('cases').append(button);
    }
  }catch(error){if(version===listRequest)showError('global-error',error.message+' — displayed cases may be stale.');}
}
async function openCase(id) {
  const version=++detailRequest;
  try {
    const item=await api('/api/cases/'+id);
    if(version!==detailRequest)return;
    selected=id;el('empty-detail').hidden=true;el('case-detail').hidden=false;
    el('case-id').textContent='CASE #'+String(id).padStart(4,'0');
    el('case-priority').textContent=item.priority;el('case-priority').className='badge '+item.priority;
    el('case-title').textContent=item.title;el('case-description').textContent=item.description;
    el('case-meta').textContent=item.category+' · Created '+new Date(item.created_at).toLocaleString();
    el('case-status').value=item.status;el('resolution').value=item.resolution;el('note').value='';
    el('timeline').replaceChildren();
    for(const event of item.events){const row=document.createElement('li');const time=text('time',new Date(event.created_at).toLocaleString());time.dateTime=event.created_at;row.append(text('b',event.kind),time,text('p',event.body));el('timeline').append(row);}
    showError('case-error','');await loadCases();
  }catch(error){showError('global-error',error.message);}
}
async function submit(formId, errorId, action) {
  const button=el(formId).querySelector('button[type="submit"],button:not([type])');button.disabled=true;
  try{showError(errorId,'');await action();}catch(error){showError(errorId,error.message);}finally{button.disabled=false;}
}
el('new-case').onclick=()=>{el('create-form').reset();showError('create-error','');el('create-dialog').showModal();};
el('close-dialog').onclick=()=>el('create-dialog').close();
el('create-form').onsubmit=event=>{event.preventDefault();submit('create-form','create-error',async()=>{const item=await api('/api/cases',{title:el('title').value,description:el('description').value,category:el('category').value,priority:el('priority').value});el('create-dialog').close();await openCase(item.id);});};
el('status-form').onsubmit=event=>{event.preventDefault();const id=selected;submit('status-form','case-error',async()=>{await api('/api/cases/'+id+'/status',{status:el('case-status').value,resolution:el('resolution').value});if(selected===id)await openCase(id);else await loadCases();});};
el('note-form').onsubmit=event=>{event.preventDefault();const id=selected;submit('note-form','case-error',async()=>{await api('/api/cases/'+id+'/notes',{body:el('note').value});if(selected===id)await openCase(id);else await loadCases();});};
el('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(loadCases,200);};
el('filter-status').onchange=loadCases;el('filter-priority').onchange=loadCases;
el('demo').onclick=async()=>{el('demo').disabled=true;try{for(const item of [{title:'Example: workstation cannot resolve a hostname',description:'Fictional training case. IP connectivity succeeds but a hostname lookup fails. Compare DNS configuration and query results.',category:'Networking',priority:'high'},{title:'Example: application container exits on startup',description:'Fictional training case. Capture the exit code, review container logs, and inspect environment configuration before making a change.',category:'Docker',priority:'normal'},{title:'Example: restore verification requested',description:'Fictional training case. Restore a small archive to a new folder and compare file contents before closing the case.',category:'Linux',priority:'low'}])await api('/api/cases',item);await loadCases();el('demo').textContent='Sample cases loaded';}catch(error){showError('global-error',error.message);el('demo').disabled=false;}};
loadCases();
