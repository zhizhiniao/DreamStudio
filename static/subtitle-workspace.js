'use strict';
const subtitleViews=new Map();
let subtitleLoading=false;
function subtitleView(id){if(!subtitleViews.has(id))subtitleViews.set(id,{filter:'all',reviewMode:'issues',reviewId:null,edits:new Map(),state:null,error:'',busy:false});return subtitleViews.get(id)}
function subtitleIssue(r){
 if(r.state==='problem')return {kind:'error',label:'待定',reason:r.reason||'译文未通过检查，请补充或修正'};
 if(r.state==='pending'||r.state==='translating')return {kind:'pending',label:r.state==='translating'?'翻译中':'尚未翻译',reason:r.state==='translating'?'正在等待翻译返回':'尚未处理到此段；继续翻译会跳过已标记的异常句'};
 if(r.reviewed||r.state==='manual')return null;
 if(r.review_warning)return {kind:'warning',label:'语境待校对',reason:r.review_warning};
 const text=r.text||'';
 if((!r.semantic&&/[，,、；;：:]\s*$/.test(text))||/(?:但是|但|而且|并且|以及|一旦|如果|因为|所以|然后|就|它|的|在|将|会)\s*[。.]?$/.test(text))return {kind:'warning',label:'断句待校对',reason:'句尾可能未表达完整，请结合前后文检查断句（规则提示，未必有错）'};
 if(/[\u4e00-\u9fff]/.test(text)&&/[,;!?]/.test(text))return {kind:'warning',label:'标点待校对',reason:'中文中出现英文标点，请结合语境检查（网址和代码可能无需修改）'};
 if(text.length/Math.max(.1,r.end-r.start)>12&&text.length>12)return {kind:'warning',label:'阅读偏快',reason:'显示时间内文字较多，建议检查断句或精简表达（规则提示）'};
 return null;
}
function subtitleReviewQueue(v){return (v.state?.rows||[]).filter(r=>{const issue=subtitleIssue(r);return v.reviewMode==='pending'?issue?.kind==='pending':v.reviewMode==='all'?['completed','manual','problem'].includes(r.state)&&!r.reviewed:issue&&issue.kind!=='pending'}).sort((a,b)=>v.reviewMode==='issues'?(Number(subtitleIssue(b)?.kind==='error')-Number(subtitleIssue(a)?.kind==='error')||a.id-b.id):a.id-b.id)}
function subtitleReviewAccept(r,v,disabled){
 if(!r||r.reviewed)return '';
 const pending=r.state==='problem';if(!pending&&!['completed','manual'].includes(r.state))return '';
 return `<button class="btn" data-subtitle-action="${pending?'accept':'review-ack'}" ${disabled||v.edits.size||(pending&&!r.candidate)?'disabled':''} title="${pending?'确认当前显示的候选译文；没有候选时需先填写译文':'已检查当前译文，无需修改'}">确定无异常</button>`;
}
function subtitleBatchRows(v){return subtitleReviewQueue(v).filter(r=>!r.reviewed&&(r.state==='problem'?!!r.candidate?.trim():['completed','manual'].includes(r.state)&&!!r.text?.trim()))}
function subtitleReviewBar(v,disabled){
 const queue=subtitleReviewQueue(v),current=v.state.rows.find(r=>r.id===v.reviewId),issue=current&&subtitleIssue(current),batch=subtitleBatchRows(v);
 return `<div class="subtitle-review-bar"><div class="subtitle-review-nav"><strong>人工校对</strong><div class="subtitle-review-tabs" role="group" aria-label="校对范围">${[['issues','需校对'],['all','全部未校对'],['pending','未翻译']].map(([mode,label])=>`<button class="btn" aria-pressed="${v.reviewMode===mode}" data-subtitle-action="review-tab" data-review-mode="${mode}">${label}</button>`).join('')}</div><select data-subtitle-review-jump aria-label="跳转到字幕段"><option value="">直接跳转（${queue.length} 条）</option>${queue.map(r=>`<option value="${r.id}" ${r.id===v.reviewId?'selected':''}>第 ${r.id+1} 段 · ${subtitleTime(r.start)} · ${esc((r.text||r.candidate||r.source).slice(0,35))}</option>`).join('')}</select><button class="btn" data-subtitle-action="review-prev" ${!queue.length?'disabled':''}>上一个</button><button class="btn" data-subtitle-action="review-next" ${!queue.length?'disabled':''}>下一个</button>${subtitleReviewAccept(current,v,disabled)}<button class="btn primary" data-subtitle-action="review-all" ${disabled||v.edits.size||!batch.length?'disabled':''}>全选确认无异常（${batch.length}）</button></div><div class="subtitle-review-reason" role="status">${v.batchProgress?esc(v.batchProgress):current?`第 ${current.id+1} 段 · ${esc(issue?.reason||(current.reviewed?'已人工校对':'请检查原意、断句和标点'))}`:`当前范围 ${queue.length} 条；批量确认仅处理本范围有译文的字幕，空候选需手动补填。`}</div></div>`;
}
function locateSubtitleReview(v,id){v.reviewId=id;redrawSubtitles();const row=document.querySelector(`[data-subtitle-row-id="${id}"]`),list=document.querySelector('.subtitle-scroll');if(row&&list)list.scrollTop+=row.getBoundingClientRect().top-list.getBoundingClientRect().top-12;}
async function confirmSubtitleBatch(id,v){
 const rows=subtitleBatchRows(v).map(r=>({...r}));let done=0;
 try{for(const r of rows){
  if(r.state==='problem'){
   const task=await api('tasks/'+id+'/subtitle-workspace','POST',{version:v.version,signature:v.state.signature,accept_id:r.id,candidate:r.candidate,edits:[]});
   v.version=task.version;data.tasks=data.tasks.map(t=>t.id===id?task:t);
  }else await api('tasks/'+id+'/subtitle-review','POST',{version:v.version,signature:v.state.signature,id:r.id,text:r.text});
  done++;v.batchProgress=`正在确认 ${done}/${rows.length}`;redrawSubtitles();
 }
 toast(`已确认 ${done} 条字幕无异常`);
 }catch(e){throw new Error(`已确认 ${done}/${rows.length} 条；其余未确认：${e.message}`)}
 finally{v.batchProgress='';await refresh();}
}
function jumpSubtitleReview(v,direction){const queue=subtitleReviewQueue(v);if(!queue.length)return;const index=queue.findIndex(r=>r.id===v.reviewId);v.reviewId=queue[(index<0?(direction>0?0:queue.length-1):(index+direction+queue.length)%queue.length)].id;redrawSubtitles();const row=document.querySelector(`[data-subtitle-row-id="${v.reviewId}"]`),list=document.querySelector('.subtitle-scroll');if(row&&list)list.scrollTop+=row.getBoundingClientRect().top-list.getBoundingClientRect().top-12;}
function subtitleRowState(r){if(r.reviewed)return '已校对';const issue=subtitleIssue(r);if(issue)return issue.label;return r.state==='manual'?'已编辑':r.state==='completed'?'确认':r.state==='problem'?'待检查':r.state==='translating'?'翻译中':'待翻译'}
function subtitleConfirm(r,v,disabled=false){return v.edits.has(r.id)?`<button class="subtitle-confirm" data-subtitle-action="confirm" data-subtitle-row="${r.id}" ${disabled?'disabled':''}>确定</button>`:''}
function subtitleReleaseActions(t,v){
 const blocked=v.busy||v.edits.size||!flowReady(t);
 return `${flowRendered(t)?`<button class="btn" data-subtitle-action="preview" ${v.busy||v.edits.size?'disabled':''}>${flowReady(t)?'查看成片':'审核成片'}</button>`:''}${flowReady(t)?`<button class="btn primary" data-subtitle-action="publish" ${blocked?'disabled':''}>进入发布中心</button>`:''}`;
}
function subtitlePhase(t,s){
 const job=t.production_job||{};
 if(job.status==='running'||t.status==='rendering')return job.stage==='render'?'正在封装成片':s.pause_requested?'正在暂停':'正在生成字幕';
 if(job.status==='failed'||t.status==='failed')return '生成失败';
 if(flowReady(t))return '成片已完成 · 待发布';
 if(flowRendered(t))return '成片已完成 · 待审核';
 if(s.rows.some(r=>r.state==='problem'))return '等待字幕校对';
 return s.total&&s.completed===s.total?'字幕已齐全 · 待人工审核':'字幕尚未完成';
}
function subtitleEditor(t){
 const v=subtitleView(t.id),s=v.state;
 if(!s)return `<section class="subtitle-workspace" data-subtitle-task="${t.id}"><header class="subtitle-toolbar"><strong>字幕</strong><span role="status">${esc(v.error||'正在读取…')}</span><button class="btn" data-subtitle-action="refresh">刷新</button></header><div class="subtitle-scroll"></div></section>`;
 const running=t.production_job?.status==='running'||t.status==='rendering',locked=s.locked,disabled=running||locked||v.busy;
 const reviewIds=new Set(subtitleReviewQueue(v).map(r=>r.id));const rows=s.rows.filter(r=>v.filter==='review'?reviewIds.has(r.id):v.filter!=='problem'||r.state==='problem'),problems=s.rows.filter(r=>r.state==='problem').length;
 const status=subtitlePhase(t,s);
 return `<section class="subtitle-workspace" data-subtitle-task="${t.id}">
 <header class="subtitle-toolbar"><strong>${s.policy?"整句字幕":"字幕"}</strong><span class="subtitle-stat" role="status" aria-live="polite" title="${esc(t.production_job?.message||'')}">${status} ${s.completed}/${s.total}</span><select data-subtitle-filter aria-label="显示字幕"><option value="all" ${v.filter==='all'?'selected':''}>全部字幕</option><option value="review" ${v.filter==='review'?'selected':''}>当前校对范围</option><option value="problem" ${v.filter==='problem'?'selected':''}>待定 (${problems})</option></select>
 <span class="subtitle-toolbar-actions">${subtitleReleaseActions(t,v)}${!locked?`${!s.policy?`<button class="btn" data-subtitle-action="sentence-mode" ${disabled||v.edits.size?'disabled':''} title="备份旧译文，切换整句翻译；会重新调用 AI">整句重译</button>`:''}<button class="btn" data-flow="upload:translated_subtitles" ${disabled?'disabled':''}>导入</button>${running?`<button class="btn" data-subtitle-action="pause" ${s.pause_requested||v.busy?'disabled':''}>暂停翻译</button>`:`${problems?`<button class="btn" data-subtitle-action="retry" ${v.busy||v.edits.size?'disabled':''} title="仅重新请求待定句，会调用 AI">重试待定句</button>`:''}${!s.total||s.rows.some(r=>r.state==='pending'||r.state==='translating')?`<button class="btn primary" data-subtitle-action="continue" ${v.busy||v.edits.size?'disabled':''}>${s.completed?'继续未完成翻译':'开始全文翻译'}</button>`:''}<button class="btn primary" data-subtitle-action="approve" ${v.busy||v.edits.size||!s.total||s.completed!==s.total?'disabled':''}>${s.approved?'前往独立封装':'字幕审核通过，下一步'}</button>`}`:''}</span></header>
 <div class="subtitle-workflow-status" role="status">${esc(v.edits.size?'修改尚未保存，请先逐行确定。':running?(t.production_job?.message||'正在翻译；异常句会保留并继续后文。'):s.total&&s.completed===s.total?'全文译文已齐全，请人工审核后确认。下一步只做本机封装。':`已完成 ${s.completed}/${s.total} 条 · 待定 ${problems} 条 · 未处理 ${s.rows.filter(r=>r.state==='pending'||r.state==='translating').length} 条。可继续未完成翻译，待定句集中人工处理；全部处理后才能确认审核。`)}${!running&&t.production_job?.status==='failed'?`<span class="subtitle-alert">${esc(t.production_job.message||'翻译服务中断，已有结果保留。')}</span>`:''}</div>
 ${subtitleReviewBar(v,disabled)}
 ${v.error?`<p role="alert" class="subtitle-alert">${esc(v.error)}</p>`:''}
 <div class="subtitle-column-head"><span>编号 / 时间码</span><span>英文原文</span><span>中文译文</span><span>状态</span></div>
 <div class="subtitle-scroll" tabindex="0" aria-label="中英文字幕编辑区">${!s.total?'<p class="subtitle-empty">尚未取得字幕，开始生成后显示。</p>':!rows.length?'<p class="subtitle-empty">没有问题字幕。</p>':rows.map(r=>{const edit=v.edits.get(r.id),text=edit?.text??(r.text||r.candidate),issue=subtitleIssue(r);return `<article class="subtitle-row ${issue?'subtitle-'+issue.kind:''} ${r.id===v.reviewId?'subtitle-current':''}" data-subtitle-row-id="${r.id}"><div class="subtitle-time"><b>${r.id+1}</b><time>${subtitleTime(r.start)}<br>${subtitleTime(r.end)}</time></div><div class="subtitle-english">${esc(r.source)}</div><div class="subtitle-chinese"><label class="sr-only" for="subtitle-text-${r.id}">第 ${r.id+1} 段中文译文</label><textarea id="subtitle-text-${r.id}" data-subtitle-id="${r.id}" rows="1" maxlength="2000" ${disabled?'disabled':''} title="${esc(issue?.reason||'直接修改中文，修改后点确定保存')}" placeholder="${r.state==='problem'?'待定 · 暂无候选，请补充译文':r.state==='translating'?'翻译中…':r.state==='pending'?'尚未翻译 · 等待继续处理':'填写译文'}">${esc(text||'')}</textarea><span class="subtitle-row-action">${subtitleConfirm(r,v,disabled)}</span></div><span class="subtitle-row-state" title="${esc(issue?.reason||'')}">${edit?'未确定':subtitleRowState(r)}</span></article>`}).join('')}</div></section>`;
}
function resizeSubtitleInputs(){document.querySelectorAll('[data-subtitle-id]').forEach(el=>{el.style.height='auto';el.style.height=Math.max(32,el.scrollHeight)+'px'})}
function subtitleTime(t){const n=Math.max(0,Number(t));return `${String(Math.floor(n/60)).padStart(2,'0')}:${(n%60).toFixed(1).padStart(4,'0')}`}
function redrawSubtitles(){const el=document.querySelector('[data-subtitle-task]');if(!el)return;const t=flowTask(el.dataset.subtitleTask);if(!t)return;const v=subtitleView(t.id),html=subtitleEditor(t);if(v.lastHTML===html)return;const scroll=el.querySelector('.subtitle-scroll')?.scrollTop||0;el.outerHTML=html;v.lastHTML=html;resizeSubtitleInputs();const body=document.querySelector('.subtitle-scroll');if(body)body.scrollTop=scroll;}
async function loadSubtitles(force=false){
 const el=document.querySelector('[data-subtitle-task],[data-subtitle-packaging]');if(!el||subtitleLoading)return;
 const id=el.dataset.subtitleTask||el.dataset.subtitlePackaging,v=subtitleView(id);if(v.busy)return;subtitleLoading=true;
 try{const state=await api('tasks/'+id+'/subtitle-workspace');
  if(v.state&&v.edits.size&&v.state.signature!==state.signature){v.error='原文或时间轴已变化。请保留当前编辑文字，重新读取后再修改。';return;}
  if(!v.edits.size||!v.state)v.version=state.version;
  const wasApproved=v.state?.approved;v.state=state;if(force)v.error='';
  const t=flowTask(id);if(t){t.production_job=state.job;if(state.job.status==='paused'&&t.status==='rendering')t.status='draft';}
  if(document.querySelector('[data-subtitle-task]')?.dataset.subtitleTask===id&&!document.activeElement?.matches('[data-subtitle-id],[data-subtitle-filter],[data-subtitle-review-mode],[data-subtitle-review-jump]'))redrawSubtitles();
  if(document.querySelector('[data-subtitle-packaging]')?.dataset.subtitlePackaging===id&&wasApproved!==state.approved)render();
 }catch(e){v.error=e.message;redrawSubtitles()}finally{subtitleLoading=false}
}
setInterval(()=>loadSubtitles(),1800);
document.addEventListener('input',e=>{
 const area=e.target.closest('[data-subtitle-task]');if(!area)return;
 const v=subtitleView(area.dataset.subtitleTask),el=e.target;
 if(el.matches('[data-subtitle-id]')){
  const i=Number(el.dataset.subtitleId),r=v.state.rows[i],baseline=r.text||r.candidate||'';
  if(el.value===baseline)v.edits.delete(i);else v.edits.set(i,{id:i,text:el.value,keep_original:el.value.trim()===r.source.trim()});
  const row=el.closest('.subtitle-row');row.querySelector('.subtitle-row-action').innerHTML=subtitleConfirm(r,v);row.querySelector('.subtitle-row-state').textContent=v.edits.has(i)?'未确定':subtitleRowState(r);
  area.querySelectorAll('[data-subtitle-action="approve"],[data-subtitle-action="continue"],[data-subtitle-action="retry"],[data-subtitle-action="review-all"],[data-subtitle-action="accept"],[data-subtitle-action="review-ack"],[data-subtitle-action="publish"],[data-subtitle-action="preview"]').forEach(b=>b.disabled=!!v.edits.size);
  const approve=area.querySelector('[data-subtitle-action="approve"]');if(approve)approve.disabled=!!v.edits.size||!v.state.total||v.state.completed!==v.state.total;
  const phase=area.querySelector('.subtitle-workflow-status');if(phase&&v.edits.size)phase.textContent='字幕有未保存修改，请先逐行确定；保存后需重新生成成片。';
  el.style.height='auto';el.style.height=Math.max(32,el.scrollHeight)+'px';
 }
});
document.addEventListener('change',e=>{
 const area=e.target.closest('[data-subtitle-task]');if(!area)return;const v=subtitleView(area.dataset.subtitleTask),el=e.target;
 if(el.matches('[data-subtitle-review-mode]')){v.reviewMode=el.value;v.reviewId=null;redrawSubtitles();return}
 if(el.matches('[data-subtitle-review-jump]')){if(el.value!=='')locateSubtitleReview(v,Number(el.value));return}
 if(el.matches('[data-subtitle-filter]')){v.filter=el.value;redrawSubtitles()}
});
document.addEventListener('click',async e=>{
 const button=e.target.closest('[data-subtitle-action]');if(!button||button.disabled)return;
 const el=button.closest('[data-subtitle-task]'),id=el.dataset.subtitleTask,v=subtitleView(id),action=button.dataset.subtitleAction;
 if(action==='review-tab'){v.reviewMode=button.dataset.reviewMode;v.filter='review';v.reviewId=null;redrawSubtitles();return}
 if(action==='review-next'||action==='review-prev'){jumpSubtitleReview(v,action==='review-next'?1:-1);return}
 if(action==='refresh'){await loadSubtitles(true);return}
 if(['publish','preview'].includes(action)){
  if(v.busy)return;
  if(v.edits.size){v.error='请先保存字幕修改，再重新生成成片。';redrawSubtitles();return}
  const task=flowTask(id);
  if(action==='preview'){if(flowRendered(task))location.hash='tasks/review/'+id;return}
  if(!flowReady(task)){v.error='当前版本尚未生成并审核，请先完成成片。';redrawSubtitles();return}
  v.busy=true;v.error='';redrawSubtitles();
  try{await api('tasks/'+id+'/drafts','POST');await refresh(false);openTaskDrafts(id)}
  catch(e){v.error=e.message}finally{v.busy=false;redrawSubtitles()}
  return;
 }
 if(v.busy)return;if(action==='sentence-mode'&&v.edits.size){v.error='请先确定所有修改';redrawSubtitles();return}const rowId=Number(button.dataset.subtitleRow);const chosen=action==='confirm'?[v.edits.get(rowId)].filter(Boolean):[];if(action==='confirm'&&!chosen.length)return;if(['approve','continue','accept','review-ack','review-all'].includes(action)&&v.edits.size){v.error='请先点击修改行旁边的确定';redrawSubtitles();return}const scroll=el.querySelector('.subtitle-scroll')?.scrollTop||0;v.busy=true;v.error='';redrawSubtitles();
 try{
  if(action==='approve'){const task=await api('tasks/'+id+'/subtitles-approve','POST',{version:v.version,signature:v.state.signature,confirmed:true});data.tasks=data.tasks.map(t=>t.id===id?task:t);setFlowStep(task,7);await refresh();toast('字幕审核已确认；可单独封装成片')}
  else if(action==='review-all'){await confirmSubtitleBatch(id,v)}
  else if(action==='review-ack'){const r=v.state.rows.find(r=>r.id===v.reviewId);await api('tasks/'+id+'/subtitle-review','POST',{version:v.version,signature:v.state.signature,id:r.id,text:r.text});r.reviewed=true;toast('本段已校对')}
  else if(action==='sentence-mode'){const task=await api('tasks/'+id+'/subtitle-sentence-mode','POST',{version:v.version});v.version=task.version;v.reviewId=null;data.tasks=data.tasks.map(t=>t.id===id?task:t);await refresh();toast('已切换整句翻译；点击继续翻译后开始，旧译文已备份')}
  else if(action==='pause'){const r=await api('tasks/'+id+'/subtitle-pause','POST',{});toast(r.message)}
  else{
   const acceptRow=action==='accept'?v.state.rows.find(r=>r.id===v.reviewId):null;
   let task=await api('tasks/'+id+'/subtitle-workspace','POST',{version:v.version,signature:v.state.signature,edits:chosen,...(acceptRow?{accept_id:acceptRow.id,candidate:acceptRow.candidate}:{}),resume:['continue','retry'].includes(action),retry:action==='retry'});
   if(action==='confirm')v.edits.delete(rowId);v.version=task.version;data.tasks=data.tasks.map(t=>t.id===id?task:t);
   if(['continue','retry'].includes(action))await api('tasks/'+id+'/production','POST',{kind:'translate',version:task.version,ai_confirmed:true});
   await refresh();resizeSubtitleInputs();const list=document.querySelector('.subtitle-scroll');if(list)list.scrollTop=scroll;toast(action==='continue'?'已从断点继续':action==='confirm'?'本行已编辑并保存':action==='accept'?'已确认无异常，采用当前候选译文':'已提交');
  }
 }catch(e){v.error=e.message}finally{v.busy=false;await loadSubtitles();redrawSubtitles()}
});
window.addEventListener('beforeunload',e=>{if([...subtitleViews.values()].some(v=>v.edits.size)){e.preventDefault();e.returnValue=''}});
