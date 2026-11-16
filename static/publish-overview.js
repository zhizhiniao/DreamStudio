let publicationFilter='all',timelineTask='all';
const publicationNames={queued:'排队中',running:'发布中',succeeded:'已接收',failed:'失败',needs_check:'待核实'};
function taskCover(t){const a=data.assets.find(a=>a.id===t?.asset_id)||{};return t?.cover_landscape_path?coverUrl(t,'landscape'):`/api/media/assets/${t?.asset_id}/${a.original_thumbnail?'original-cover':'thumbnail'}`}
function publishTaskSummary(t,count){
 if(!t)return `<div class="panel">${link('选择制作任务','tasks/list')}</div>`;
 return `<section class="panel publish-task-summary"><a href="#tasks/${t.in_history?'history':'detail'}/${t.id}"><img src="${esc(taskCover(t))}" alt="${esc(t.title)}封面"></a><div><div class="row between"><h2>${esc(t.title)}</h2><small>${count} 个账号</small></div><details class="publish-intro"><summary>${esc(t.description||'暂无简介')}</summary><p>${esc(t.description||'暂无简介')}</p></details><div class="history-tags">${(t.tags||[]).map(tag=>`<span>#${esc(tag)}</span>`).join('')}</div><div class="actions">${link('查看成片','tasks/'+(t.in_history?'history/':'detail/')+t.id)}${taskTargetButton(t)}${canCompleteTask(t)?taskCompletionButton(t):''}${link('全部任务稿件','publish/drafts/all')}</div></div></section>`;
}
function groupedPublications(){
 const groups=new Map();
 for(const p of data.publications){const key=p.task_id||'unknown:'+p.id;if(!groups.has(key))groups.set(key,{id:key,task:data.tasks.find(t=>t.id===p.task_id),publications:[]});groups.get(key).publications.push(p)}
 return [...groups.values()].map(g=>({...g,updated_at:Math.max(...g.publications.map(p=>p.updated_at||0))})).sort((a,b)=>b.updated_at-a.updated_at);
}
function effectiveReceipt(records){return records.find(p=>p.status==='succeeded')||records.find(p=>p.status==='needs_check')||records.find(p=>['queued','running'].includes(p.status))||records[0]}
function publicationRecords(){
 const groups=groupedPublications();
 const match=p=>publicationFilter==='all'||publicationFilter==='running'&&['queued','running'].includes(p.status)||publicationFilter==='attention'&&['failed','needs_check'].includes(p.status)||publicationFilter==='succeeded'&&p.status==='succeeded';
 const cards=groups.map(g=>{
  const accounts=new Map();for(const p of recentFirst(g.publications)){const id=p.account_id||p.id;if(!accounts.has(id))accounts.set(id,[]);accounts.get(id).push(p)}
  const rows=[...accounts.values()].map(records=>({records,current:effectiveReceipt(records)})).filter(x=>match(x.current));if(!rows.length)return '';
  const title=g.task?.title||g.publications[0].title||'历史内容';
  return `<article class="panel publication-group" data-publication-task="${esc(g.id)}"><header class="publication-group-head">${g.task?`<img src="${esc(taskCover(g.task))}" alt="${esc(title)}封面">`:''}<div><h2>${esc(title)}</h2><small>${accounts.size} 个账号 · ${[...accounts.values()].filter(v=>effectiveReceipt(v).status==='succeeded').length} 个已接收</small><div class="actions">${g.task?link('内容与成片','tasks/'+(g.task.in_history?'history/':'detail/')+g.id)+taskTargetButton(g.task):''}</div></div></header><div class="publication-account-grid">${rows.map(({records,current:p})=>`<section class="publication-account" data-account-id="${esc(p.account_id||p.id)}"><div class="row between">${accountIdentity(acct(p.account_id)||{platform:p.platform,name:'已移除账号'})}${pill(publicationNames[p.status]||p.status,p.status==='succeeded'?'good':'warn')}</div><p>${esc(p.message||'')}</p><small>记录更新 ${time(p.updated_at)}</small><div class="actions">${button('查看详情','publication:'+p.id)}${p.receipt?.manage_url?`<a class="btn" target="_blank" rel="noopener" href="${esc(p.receipt.manage_url)}">平台记录</a>`:''}</div>${records.length>1?`<details><summary>历次尝试（${records.length}）</summary>${records.map(r=>`<div class="attempt-row"><time>${time(r.updated_at)}</time>${pill(publicationNames[r.status]||r.status)}<span>${esc(r.message||'')}</span>${button('详情','publication:'+r.id)}</div>`).join('')}</details>`:''}</section>`).join('')}</div></article>`;
 }).filter(Boolean);
 return `<div class="toolbar publication-filters">${[['all','全部'],['running','进行中'],['attention','需处理'],['succeeded','已接收']].map(([id,label])=>button(label,'publication-filter:'+id,publicationFilter===id)).join('')}</div><div class="publication-groups">${cards.join('')||empty('暂无对应记录','发布执行后会按内容汇总到这里。')}</div>`;
}
function milestoneLabel(kind,h){
 if(h.event==='upload_step')return {upload_video_file:'开始上传视频',fill_meta:'填写投稿文案',wait_for_upload_complete:'等待平台处理视频',set_thumbnail:'设置投稿封面',check_original_declaration:'填写内容声明',apply_original_statement:'填写内容声明',submit_publish:'提交平台'}[h.step]||'更新投稿步骤';
 if(h.event==='cover_verified')return '封面画面校验通过';
 if(h.event==='confirmed')return ['确认标题','确认简介','确认标签','确认封面'][h.step]||'确认制作内容';
 if(h.event==='workflow')return {content:'整理内容',cover:'制作封面',cover_review:'审核封面',script:'准备讲稿',script_review:'等待讲稿审核',voice:'选择音色',ready_to_generate:'确认生成内容',generating:'生成配音与成片',ready:'成片待发布',generation_failed:'成片生成失败'}[h.stage]||'更新制作步骤';
 if(h.event==='speech_reviewed')return '讲稿审核通过';if(h.event==='voice_selected')return '确认配音音色';
 if(h.event==='created')return {tasks:'任务立项',downloads:'创建下载',publications:'确认投稿'}[kind];
 if(h.event==='reviewed')return '成片审核通过';if(h.event==='completed')return '手动标记完成';if(h.event==='reopened')return '继续发布';if(h.event==='targets')return '更新发布账号';
 if(h.event==='production')return ({running:'开始',completed:'完成',failed:'失败'}[h.status]||h.status)+({script:'整理讲稿',finish:'配音与封装',auto:'制作流程'}[h.kind]||'制作');
 if(kind==='downloads')return {queued:'等待下载',running:'开始下载',completed:'下载完成',failed:'下载失败'}[h.status]||h.status;
 if(kind==='publications')return h.event==='phase'?({preparing:'准备投稿',submitting:'提交平台',submitted:'收到平台回执',confirmed_not_published:'人工核对未发布',rejected:'平台拒绝'}[h.phase]||h.phase):publicationNames[h.status]||h.status;
 return {draft:'制作草稿',rendering:'生成成片',review:'等待审片',approved:'审核通过',failed:'制作失败',cancelled:'取消任务'}[h.status]||h.status;
}
function taskMilestones(t){
 const rows=[];const append=(record,kind)=>{
  const history=record.history||[];history.filter(h=>!(h.event==='status'&&history.some(other=>other.at===h.at&&(other.event==='created'&&['queued','draft'].includes(h.status)||other.event==='reviewed'&&kind==='tasks'&&h.status==='approved'||other.event==='workflow'&&kind==='tasks'&&h.status==='rendering')))).forEach(h=>rows.push({at:h.at,label:milestoneLabel(kind,h),account:kind==='publications'?record.account_id:null}));
  if(!history.length&&record.created_at)rows.push({at:record.created_at,label:milestoneLabel(kind,{event:'created'})});
  if(!history.some(h=>h.event==='reviewed')&&record.reviewed_at)rows.push({at:record.reviewed_at,label:'成片审核通过'});
  if(kind==='tasks'&&!history.some(h=>h.event==='production')&&record.production_job?.started_at)rows.push({at:record.production_job.started_at,label:'开始制作'});
  if(!history.length&&record.updated_at)rows.push({at:record.updated_at,label:'旧记录更新 · '+(kind==='publications'?publicationNames[record.status]||record.status:kind==='downloads'?'下载记录':'任务记录'),account:kind==='publications'?record.account_id:null,legacy:true});
 };
 append(t,'tasks');(data.downloads||[]).filter(d=>d.task_id===t.id||!d.task_id&&d.asset_id===t.asset_id).forEach(d=>append(d,'downloads'));data.publications.filter(p=>p.task_id===t.id).forEach(p=>append(p,'publications'));
 return rows.filter(r=>r.at&&r.label).sort((a,b)=>b.at-a.at);
}
function publicationSchedule(){
 const tasks=recentFirst(data.tasks).filter(t=>timelineTask==='all'||t.id===timelineTask);
 const planned=recentFirst(data.drafts.filter(d=>d.planned_at)).sort((a,b)=>a.planned_at-b.planned_at);
 return `<div class="toolbar"><label>查看任务 <select id="timeline-task"><option value="all">全部任务</option>${recentFirst(data.tasks).map(t=>`<option value="${esc(t.id)}" ${timelineTask===t.id?'selected':''}>${esc(t.title)}</option>`).join('')}</select></label></div><section class="panel planned-publications"><h2>发布排期</h2>${planned.map(d=>`<div class="planned-row"><time>${time(d.planned_at)}</time><span>${esc(d.title)}</span>${accountIdentity(acct(d.account_id))}${link('查看稿件','publish/editor/'+d.id)}</div>`).join('')||'<p class="muted">暂无排期</p>'}</section><div class="task-timelines">${tasks.map(t=>{const rows=taskMilestones(t);return `<article class="panel task-timeline"><header class="publication-group-head"><img src="${esc(taskCover(t))}" alt="${esc(t.title)}封面"><div><h2>${esc(t.title)}</h2>${link('查看任务','tasks/'+(t.in_history?'history/':'detail/')+t.id)}</div></header><ol>${rows.map(r=>`<li><time>${time(r.at)}</time><span>${esc(r.label)}</span>${r.account?accountIdentity(acct(r.account)||{name:'已移除账号'}):''}</li>`).join('')||'<li>旧任务未记录事件时间</li>'}</ol></article>`}).join('')}</div><p class="mini">旧记录更新不代表事件发生时间。预约到时仍需人工确认投稿。</p>`;
}
document.addEventListener('click',e=>{const el=e.target.closest('[data-action^="publication-filter:"]');if(el){publicationFilter=el.dataset.action.split(':')[1];render()}});
document.addEventListener('change',e=>{if(e.target.id==='timeline-task'){timelineTask=e.target.value;render()}});
