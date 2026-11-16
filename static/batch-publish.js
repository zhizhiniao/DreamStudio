function draftReceipt(d){const records=data.publications.filter(p=>p.draft_id===d.id);return records.find(p=>['queued','running','needs_check','succeeded'].includes(p.status))||records[0]}
function draftReceiptLabel(d){const p=draftReceipt(d);return p?({queued:'排队中',running:'发布中',needs_check:'待核实',succeeded:'已接收',failed:'发布失败'}[p.status]||'待发布'):'待发布'}
function kuaishouCaptionCount(x){return x.caption_count??[x.title.trim(),(x.body||'').trim(),(x.tags||[]).map(t=>'#'+t).join(' ')].filter(Boolean).join('\n').length}
document.addEventListener('input',e=>{
 if(!e.target.matches('#draft-title,#draft-body,#draft-tags'))return;
 const counter=document.querySelector('[data-kuaishou-caption-count]');if(!counter)return;
 const count=kuaishouCaptionCount({title:document.querySelector('#draft-title').value,body:document.querySelector('#draft-body').value,tags:document.querySelector('#draft-tags').value.split(/[,，]/).map(t=>t.trim()).filter(Boolean)});
 counter.textContent=`合计 ${count} / 500 字`;counter.classList.toggle('status-error',count>500);
});
const publishSelection = new Set();
let publishReview = [];
let activePublishBatch='', batchResultTimer, batchResults=[];
let batchLoginGeneration=0, batchViewEpoch=0;
let lastPublishBatch=localStorage.getItem('lastPublishBatch')||'';
let savedPublishBatch=null, savedPublishBatchError='';
async function refreshSavedPublishBatch(){
 try{
  const history=(await api('drafts/batches')).items;
  const latest=history[0];
  savedPublishBatch=latest?await api('drafts/batches/'+latest.id):null;
  lastPublishBatch=latest?.id||'';
  savedPublishBatchError='';
 }catch(e){savedPublishBatchError='批次状态暂未刷新，请稍后查看结果。'}
}
function savedPublishBatchPanel(){
 const b=savedPublishBatch, counts={};
 for(const row of b?.items||[])counts[row.status]=(counts[row.status]||0)+1;
 const summary=b?`成功 ${counts.succeeded||0} · 失败 ${counts.failed||0} · 未提交 ${counts.blocked||0} · 处理中 ${(counts.queued||0)+(counts.running||0)} · 待核实 ${counts.needs_check||0}`:'暂无已确认的发布批次';
 return `<section class="panel" data-saved-publish-batch><div class="row between"><strong>本次发布结果</strong><div>${b?button('查看本批次结果','batch-result:'+b.id):''}${button('批次历史','batch-history')}</div></div><p role="status">${esc(summary)}</p>${savedPublishBatchError?`<p class="status-warn">${esc(savedPublishBatchError)}</p>`:''}<p class="mini">确认后在后台执行，关闭网页可继续处理其他任务；重新打开可查看结果。停止造梦室服务、关机或休眠会中断执行。</p></section>`;
}
document.addEventListener('close',e=>{
 if(e.target.id==='dialog'&&route[0]==='publish'&&route[1]!=='editor')render();
},true);
function openTaskDrafts(key) {
 sessionStorage.setItem('publishTask',key);
 publishSelection.clear();publishReview=[];
 location.hash='publish/drafts/'+key;render();
}
function draftScope() {
 const explicit=route[1]==='drafts'?route[2]:null;
 if(explicit==='all')return null;
 const key=explicit||sessionStorage.getItem('publishTask')||data.tasks.find(t=>!t.in_history&&t.reviewed_version===t.version)?.id;
 if(key)sessionStorage.setItem('publishTask',key);
 return key||'__none__';
}
function batchDrafts() {
 const scope=draftScope(),task=data.tasks.find(t=>t.id===scope);
 const drafts=data.drafts.filter(d=>(!scope||d.task_id===scope)&&data.tasks.find(t=>t.id===d.task_id)?.lifecycle!=='cancelled');
 const visible=new Set(drafts.map(d=>d.id));
 for(const id of publishSelection)if(!visible.has(id))publishSelection.delete(id);
 publishReview=publishReview.filter(d=>visible.has(d.id));
 const context=scope?publishTaskSummary(task,drafts.length):`<p class="mini">全部任务与历史稿件</p>`;
 const busy = new Set(data.publications.filter(p=>['queued','running','succeeded','needs_check'].includes(p.status)).map(p=>p.draft_id));
 return `${context}${!scope?button('查看批次记录','batch-history'):''}${!scope&&lastPublishBatch?button('查看上次批量结果','batch-result:'+lastPublishBatch):''}<div class="toolbar publish-selection-tools">${button('全选可发布稿件','batch-all')}${button('清空选择','batch-clear')}</div><p class="mini">统一查看各账号文案，一次确认发布；需要调整的稿件可单独编辑。</p><div class="grid publish-account-grid">${drafts.map(d=>`<article class="boardcard"><div class="row between"><label><input type="checkbox" data-publish-select="${esc(d.id)}" ${publishSelection.has(d.id)?'checked':''} ${busy.has(d.id)?'disabled':''}> ${platformLogo(d.platform)}</label>${pill(draftReceiptLabel(d))}</div><p>${esc(d.title)}</p>${draftReceipt(d)?.status==='failed'?`<small class="status-warn">${esc(draftReceipt(d).message||'上次发布未完成')}</small>`:''}<small>${accountIdentity(acct(d.account_id))} · v${d.task_version}</small><div style="margin-top:12px">${busy.has(d.id)?button('查看回执','publication:'+data.publications.find(p=>p.draft_id===d.id&&['queued','running','succeeded','needs_check'].includes(p.status)).id):link('编辑文案与封面','publish/editor/'+d.id)}</div></article>`).join('')||empty('暂无平台稿件','审片通过后生成平台稿件。')}</div><footer class="publish-next-actions" aria-label="平台选择后的操作"><span id="batch-count" aria-live="polite">已选 ${publishSelection.size} 项</span>${button('检查并批量发布','batch-review',true)}</footer>`;
}
document.addEventListener('change',e=>{
 const id=e.target.dataset.publishSelect;
 if(!id)return;
 if(e.target.checked)publishSelection.add(id);else publishSelection.delete(id);
 $('#batch-count').textContent=`已选 ${publishSelection.size} 项`;
});
document.addEventListener('click',async e=>{
 const el=e.target.closest('[data-action]');
 const action=el?.dataset.action;
 if(!action?.startsWith('batch-'))return;
 try {
  if(action==='batch-all'){
   document.querySelectorAll('[data-publish-select]:not(:disabled)').forEach(x=>publishSelection.add(x.dataset.publishSelect));render();
  } else if(action==='batch-clear'){publishSelection.clear();render();}
  else if(action==='batch-review'||action.startsWith('batch-one:')){
   activePublishBatch='';
   const ids=action.startsWith('batch-one:')?[action.split(':')[1]]:[...publishSelection];
   if(!ids.length)throw new Error('请先勾选稿件');
   el.disabled=true;
   publishReview=(await api('drafts/batch-preview','POST',{ids})).items;
   showBatchReview();
  } else if(action.startsWith('batch-remove:')){
   const id=action.split(':')[1];publishSelection.delete(id);publishReview=publishReview.filter(x=>x.id!==id);
   if(publishReview.length)showBatchReview();else {close();render();}
  } else if(action.startsWith('batch-account-check:')){
   el.disabled=true;
   await api('drafts/batch-repair','POST',{ids:[action.split(':')[1]]});await resumeBatchReview();
  } else if(action.startsWith('batch-account-enable:')){
   await api('accounts/batch','POST',{ids:[action.split(':')[1]],action:'enable'});await resumeBatchReview();
  } else if(action.startsWith('batch-sync:')){
   await api('drafts/'+action.split(':')[1]+'/sync-version','POST');await resumeBatchReview();
  } else if(action.startsWith('batch-now:')){
   await api('drafts/'+action.split(':')[1],'PUT',{planned_at:0});await resumeBatchReview();
  } else if(action.startsWith('batch-session-save:')){
   const file=$('#batch-session-file').files[0];if(!file)throw new Error('请选择账号会话文件');
   const form=new FormData();form.append('file',file);form.append('purpose','publish');
   await api('accounts/'+action.split(':')[1]+'/session','POST',form);await refresh(false);await resumeBatchReview();
  } else if(action.startsWith('batch-inline-login:')){
   clearTimeout(batchPollTimer);
   const id=action.split(':')[1], generation=++batchLoginGeneration;
   ++batchViewEpoch;
   if(acct(id).platform==='bilibili'){modal('更新B站账号会话','<label class="field"><span>导入此B站账号的会话 JSON</span><input type="file" id="batch-session-file" accept=".json"></label>',button('返回批量确认','batch-resume')+button('导入并检查','batch-session-save:'+id,true));return}
   const card=el.closest('[data-publish-account]');
   if(card&&$('#batch-confirm')){
    document.querySelectorAll('[data-publish-login]').forEach(x=>x.remove());
    card.querySelector('.batch-review-identity').insertAdjacentHTML('afterend',`<div data-publish-login class="panel" style="padding:16px;margin:12px 0;border:1px solid var(--accent,#ee996d)"><strong>在此扫码登录${esc(labels[acct(id).platform]||'账号')}</strong><div id="batch-login-progress" role="status" aria-live="polite">正在获取二维码，请稍候…</div>${button('收起登录并重新检查','batch-resume')}</div>`);
    $('#batch-confirm').checked=false;$('#batch-confirm').disabled=true;
    document.querySelector('[data-action="batch-submit"]').disabled=true;
    $('#batch-login-progress').scrollIntoView({block:'nearest'});
   }else{
    modal('在此登录账号','<div id="batch-login-progress" role="status">正在获取二维码…</div>',button('返回本次批量确认','batch-resume'));
   }
   let job;
   try{job=await api('accounts/'+id+'/login','POST')}catch(error){if(generation===batchLoginGeneration&&$('#batch-login-progress'))$('#batch-login-progress').innerHTML=`<p class="status-error">${esc(error.message)}</p>${button('重新获取二维码','batch-inline-login:'+id)}`;return}
   const poll=async()=>{
    if(generation!==batchLoginGeneration||!$('#dialog').open||!$('#batch-login-progress'))return;
    const p=await api(`accounts/${id}/login/${job.id}`);
    if(generation!==batchLoginGeneration||!$('#dialog').open||!$('#batch-login-progress'))return;
    $('#batch-login-progress').innerHTML=`<p>${esc(p.message||'等待扫码')}</p>${p.qrcode?`<img class="login-qr" src="${esc(p.qrcode)}" alt="登录二维码">`:''}`;
    if(p.status==='succeeded'){await refresh(false);await resumeBatchReview();return}
    if(p.status==='failed')$('#batch-login-progress').insertAdjacentHTML('beforeend',button('重新获取二维码','batch-inline-login:'+id));
    if(['failed','missing','cancelled'].includes(p.status)&&p.status!=='failed')$('#batch-login-progress').insertAdjacentHTML('beforeend',button('重新获取二维码','batch-inline-login:'+id));
    if(!['failed','missing','cancelled'].includes(p.status))setTimeout(()=>poll().catch(e=>{if(generation===batchLoginGeneration&&$('#batch-login-progress'))$('#batch-login-progress').innerHTML=`<p class="status-error">${esc(e.message)}</p>${button('重新获取二维码','batch-inline-login:'+id)}`}),2500);
   };await poll();
  } else if(action==='batch-history'){
   const history=(await api('drafts/batches')).items;
   modal('批次记录',history.map(x=>`<div class="lineitem"><span>${time(x.updated_at)} · ${x.count} 个账号</span>${button('查看这批结果','batch-result:'+x.id)}</div>`).join('')||'<p>暂无批次</p>');
  } else if(action.startsWith('batch-result:')){
   activePublishBatch=action.split(':')[1];await showBatchResults();
  } else if(action==='batch-retry-failed'){
   await loadBatchResults();
   const ids=batchResults.filter(x=>['failed','blocked'].includes(x.status)&&x.supported!==false).map(x=>x.id);
   if(!ids.length)throw new Error('没有可重试的失败项');
   publishReview=(await api('drafts/batch-preview','POST',{ids})).items;showBatchReview();
  } else if(action.startsWith('batch-detail:')){
   clearTimeout(batchResultTimer);
   const id=action.split(':')[1], p=await api('publications/'+id);
   modal('处理当前账号任务',`<p>${esc(p.message)}</p>${publicationRecovery(p,id)}${p.evidence_url?`<details><summary>查看上次失败现场</summary><img style="max-width:100%" src="${esc(p.evidence_url)}" alt="平台失败现场截图"></details>`:''}${p.qrcode?`<img class="login-qr" src="${esc(p.qrcode)}" alt="账号验证二维码">`:''}${p.verification?`<div data-nonce="${esc(p.verification)}" id="verification">${field('平台验证码','','verification-code')}${button('发送验证码','send-code:'+id)}${button('提交验证码','verify-code:'+id,true)}</div>`:''}`,button('返回本批次结果','batch-result:'+activePublishBatch));
  } else if(action==='batch-repair'){
   el.disabled=true;el.textContent='正在修复并检查…';
   publishReview=(await api('drafts/batch-repair','POST',{ids:publishReview.map(x=>x.id)})).items;
   showBatchReview();
  } else if(action.startsWith('batch-zone:')){
   const x=publishReview.find(x=>x.id===action.split(':')[1]);
   const zones=(await api('bilibili/zones')).items;
   modal('B站投稿分区',`<label class="field"><span>投稿分区</span><select id="bili-zone"><option value="">请选择</option>${zones.map(z=>`<option value="${z.tid}" ${Number(x.bilibili_tid)===z.tid?'selected':''}>${esc(z.name)}</option>`).join('')}</select></label>`,button('返回批量确认','batch-resume')+button('保存分区并返回','batch-zone-save:'+x.id,true));
  } else if(action.startsWith('batch-zone-save:')){
   if(!$('#bili-zone').value)throw new Error('请选择分区');
   await api('drafts/'+action.split(':')[1],'PUT',{bilibili_tid:Number($('#bili-zone').value)});await resumeBatchReview();
  } else if(action.startsWith('batch-resolve:')){
   if(!$('#receipt-checked').checked)throw new Error('请先核对平台记录并勾选确认');
   const parts=action.split(':');
   const p=await api('publications/'+parts[2]+'/resolve','POST',{confirmed:true,outcome:parts[1]});
   await refresh(false);
   modal('核对结果已保存',`<p>${esc(p.message)}</p>`,p.status==='failed'?button('检查并重新确认投稿','batch-one:'+p.draft_id,true):'');
  } else if(action.startsWith('batch-edit:')){
   clearTimeout(batchPollTimer);
   const x=publishReview.find(x=>x.id===action.split(':')[1]);
   modal('编辑此平台文案',`<p>${accountIdentity(acct(x.account_id)||{platform:x.platform,name:x.account})} · 本次 ${publishReview.length} 个账号保持选中</p>${field('标题',x.title,'batch-edit-title')}${x.platform==='wechat_channels'?field('视频号短标题 · 最多 16 字',x.short_title,'batch-edit-short-title'):''}${textarea('正文',x.body,'batch-edit-body')}${field('话题 · 逗号分隔',x.tags.join(','),'batch-edit-tags')}`,button('取消并返回批量确认','batch-resume')+button('保存并返回批量确认','batch-save:'+x.id,true));
  } else if(action==='batch-resume'){
   await resumeBatchReview();
  } else if(action.startsWith('batch-save:')){
   el.disabled=true;
   await api('drafts/'+action.split(':')[1],'PUT',{...($('#batch-edit-short-title')?{short_title:$('#batch-edit-short-title').value}:{}),title:$('#batch-edit-title').value,body:$('#batch-edit-body').value,tags:$('#batch-edit-tags').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean)});
   await refresh(false);await resumeBatchReview();
  } else if(action==='batch-submit'){
   if(!$('#batch-confirm').checked)throw new Error('请先勾选统一确认');
   el.disabled=true;
   const before=publishReview;
   const latest=(await api('drafts/batch-preview','POST',{ids:before.map(x=>x.id)})).items;
   if(latest.some(x=>x.checking)||latest.some(x=>x.fingerprint!==before.find(y=>y.id===x.id)?.fingerprint)||latest.some(x=>!before.find(y=>y.id===x.id)?.issues.length&&x.issues.length)){
    publishReview=latest;showBatchReview();return;
   }
   const valid=publishReview.filter(x=>!x.issues.length);
   el.disabled=true;el.textContent='正在加入发布队列…';
   const result=await api('drafts/batch-publish','POST',{ids:publishReview.map(x=>x.id),batch_id:activePublishBatch||undefined,fingerprints:Object.fromEntries(publishReview.map(x=>[x.id,x.fingerprint])),confirmed:true,ai_declaration:$('#batch-ai').checked});
   const queued=result.results.filter(x=>x.status==='queued');queued.forEach(x=>publishSelection.delete(x.id));
   activePublishBatch=result.batch_id;lastPublishBatch=result.batch_id;
   try{localStorage.setItem('lastPublishBatch',lastPublishBatch)}catch(_){/* Server history remains authoritative. */}
   close();
   toast(queued.length?`已加入后台发布队列（${queued.length} 项），可继续其他工作。进度见左侧「发布任务」。`:'本批次未提交，请在左侧「发布任务」查看原因。');
   await refresh();
  }
 }catch(error){toast(error.message)}finally{el.disabled=false}
});
document.addEventListener('click',e=>{if(e.target.closest('#dialog a[href^="#publish/"]'))$('#dialog').close()});

let batchPollTimer;
function showBatchReview() {
 ++batchViewEpoch;
 clearTimeout(batchResultTimer);clearTimeout(batchPollTimer);
   const valid=publishReview.filter(x=>!x.issues.length);
   modal('统一确认并发布',`<p role="status">${activePublishBatch?'仅处理原批次未成功项；已成功账号不会重复发布。 ':''}${publishReview.some(x=>x.checking)?'正在自动复查账号，请稍候… ':''}共 ${publishReview.length} 项，可发布 ${valid.length} 项。以下文案将分别提交到对应账号。</p>${publishReview.map(x=>`<section class="panel" data-publish-account="${esc(x.account_id)}" style="padding:12px;margin:10px 0"><div class="batch-review-identity"><img src="${draftCoverUrl(x.id,'landscape',x.cover_revision)}" alt="${esc(x.title)}横版封面"><img style="width:72px;aspect-ratio:3/4" src="${draftCoverUrl(x.id,'portrait',x.cover_revision)}" alt="${esc(x.title)}竖版封面"><div><strong>${accountIdentity(acct(x.account_id)||{platform:x.platform,name:x.account})}</strong><p>${esc(x.title)}</p></div></div>${x.platform==='kuaishou'?`<small class="${kuaishouCaptionCount(x)>500?'status-error':'status-good'}">快手合计 ${kuaishouCaptionCount(x)} / 500 字 · 含标题、简介、话题及分隔符</small>`:''}${x.short_title?`<small>视频号短标题：${esc(x.short_title)}（已校验 · 最多 16 字）</small>`:''}<p style="white-space:pre-wrap">${esc(x.body)}</p><div class="batch-review-tags">${esc(x.tags.join(' · '))}</div>${x.repair_note?`<small class="status-good">${esc(x.repair_note)}</small>`:''}${x.issues.length?`<p class="status-error">${esc(x.issues.join('；'))}</p>`:''}<div class="batch-review-actions" aria-label="此账号稿件操作">${x.platform==='bilibili'?`<small>投稿分区：${esc(x.bilibili_zone_name||'自动选择')}</small>`+button('修改分区','batch-zone:'+x.id):''}${x.supported?button('编辑此平台文案','batch-edit:'+x.id)+button('调整横竖封面','draft-cover:'+x.id)+button(x.platform==='bilibili'?'更新B站会话':'在此扫码登录','batch-inline-login:'+x.account_id):''}${x.repairable&&x.supported?button('重新检查此账号','batch-account-check:'+x.id):''}${x.supported&&x.paused?button('启用此账号并检查','batch-account-enable:'+x.account_id):''}${x.can_sync?button('同步已审成片版本','batch-sync:'+x.id):''}${x.scheduled?button('取消排期，改为本次发布','batch-now:'+x.id):''}${button('从本批次移除','batch-remove:'+x.id,false,'data-publish-remove')}</div></section>`).join('')}<div class="batch-review-confirm"><label class="confirm"><input id="batch-confirm" type="checkbox">我已核对上述可发布稿件的内容，确认向这些账号公开投稿</label><label class="confirm"><input id="batch-ai" type="checkbox" checked>确认适用的 AI 内容声明</label><p class="mini">快手自动保留前 3 个话题；编辑后会返回本次批量确认。需要登录或改写标题时会单独提示。有问题的项目将跳过；平台执行结果会分别保留在发布队列。</p></div>`,(publishReview.some(x=>x.repairable)?button('一键修复并重新检查','batch-repair'): '')+button(`确认发布 ${valid.length} 项`,'batch-submit',true,valid.length&&!publishReview.some(x=>x.checking)?'':'disabled'));
 if(publishReview.some(x=>x.checking)) batchPollTimer=setTimeout(pollBatchReview,2000);
}
async function pollBatchReview(){
 if(!$('#dialog').open||!$('#batch-confirm')||$('#batch-login-progress'))return;
 const epoch=batchViewEpoch;
 try{
  const checked=$('#batch-confirm').checked, ai=$('#batch-ai').checked;
  const result=await api('drafts/batch-preview','POST',{ids:publishReview.map(x=>x.id),poll:true});
  if(epoch!==batchViewEpoch||!$('#dialog').open||!$('#batch-confirm')||$('#batch-login-progress'))return;
  publishReview=result.items;showBatchReview();
  $('#batch-confirm').checked=checked;$('#batch-ai').checked=ai;
 }catch(error){toast('账号复查暂未完成：'+error.message)}
}

async function resumeBatchReview(){
 ++batchLoginGeneration;++batchViewEpoch;
 const ids=publishReview.length?publishReview.map(x=>x.id):[...publishSelection];
 publishReview=(await api('drafts/batch-preview','POST',{ids})).items;
 showBatchReview();
}

async function loadBatchResults(){
 batchResults=(await api('drafts/batches/'+activePublishBatch)).items;
}
async function showBatchResults(background=false){
 clearTimeout(batchResultTimer);clearTimeout(batchPollTimer);
 const revision=dialogRevision, batch=activePublishBatch;
 const result=await api('drafts/batches/'+batch);
 if(revision!==dialogRevision||batch!==activePublishBatch)return;
 batchResults=result.items;
 if(background&&(!$('#batch-results')||!$('#dialog').open))return;
 const counts={};batchResults.forEach(x=>counts[x.status]=(counts[x.status]||0)+1);
 const names={queued:'等待执行',running:'正在发布',succeeded:'已收到成功回执',failed:'发布失败',blocked:'未提交',needs_check:'结果待核实'};
 const retry=batchResults.filter(x=>['failed','blocked'].includes(x.status)&&x.supported!==false);
 modal('本批次发布结果',`<div id="batch-results"><p role="status">成功 ${counts.succeeded||0} · 失败 ${counts.failed||0} · 未提交 ${counts.blocked||0} · 处理中 ${(counts.queued||0)+(counts.running||0)} · 待核实 ${counts.needs_check||0}</p>${batchResults.map(x=>`<section class="panel" style="padding:12px;margin:10px 0"><strong>${accountIdentity(acct(x.account_id)||{platform:x.platform,name:x.account})}</strong><p>${esc(names[x.status]||x.status)} · ${esc(x.message||'')}</p>${x.status==='needs_check'?'<p class="status-warn">提交结果不明确，请先核对平台记录，暂不允许重投。</p>':''}${['failed','blocked'].includes(x.status)&&x.supported!==false?button('修改文案 / 封面并重新确认','batch-one:'+x.id,true):''}${x.publication_id?button('查看进度 / 处理验证','batch-detail:'+x.publication_id):''}${x.receipt?.manage_url?`<a class="textlink" target="_blank" rel="noopener" href="${esc(x.receipt.manage_url)}">打开平台回执 ↗</a>`:''}</section>`).join('')}<p class="mini">成功项保留回执；仅失败或未提交项进入修复与再次确认。平台最终审核另行确认。</p></div>`,button('刷新结果','batch-result:'+activePublishBatch)+(retry.length?button(`仅修复并重试未成功的 ${retry.length} 项`,'batch-retry-failed',true):''));
 if(batchResults.some(x=>['queued','running'].includes(x.status)))batchResultTimer=setTimeout(async()=>{
  if(!$('#batch-results')||!$('#dialog').open)return;
  try{await showBatchResults(true)}catch(e){toast(e.message)}
 },2500);
}

// The server owns execution. This view only observes persisted publications.
function publishMonitorRows(){
 const latest=new Set((savedPublishBatch?.items||[]).map(x=>x.publication_id));
 return data.publications.filter(p=>['queued','running','needs_check'].includes(p.status)||latest.has(p.id));
}
function publishNeedsAttention(p){return ['failed','needs_check','blocked'].includes(p.status)||(['queued','running'].includes(p.status)&&!!(p.qrcode||p.verification||/验证|扫码|登录/.test(p.message||'')))}
function publishMonitorSummary(){
 const rows=publishMonitorRows(), active=rows.filter(p=>['queued','running'].includes(p.status)).length;
 const blocked=(savedPublishBatch?.items||[]).filter(p=>p.status==='blocked').length;
 const attention=rows.filter(publishNeedsAttention).length+blocked;
 return {active,attention,text:active?`${active} 项进行中${attention?` · ${attention} 项待处理`:''}`:attention?`${attention} 项待处理`:rows.length?'发布已结束 · 查看结果':'暂无发布任务'};
}
function publishMonitorSidebar(){
 const s=publishMonitorSummary();
 return `<button class="publish-monitor-button ${s.attention?'needs-attention':''}" data-action="batch-monitor"><span>${icon('publish')}<strong>发布任务</strong>${s.active?`<b>${s.active}</b>`:''}</span><small role="status" aria-live="polite">${esc(s.text)}</small></button>`;
}
function publishMonitorBody(){
 const names={queued:'排队中',running:'发布中',succeeded:'已收到回执',failed:'失败',needs_check:'待核实'};
 return `<p class="mini">可以收起此窗口、切换页面或关闭网页。后台会继续发布；电脑和造梦室服务需保持运行。</p>${publishMonitorRows().map(p=>`<section class="panel publish-monitor-item"><strong>${accountIdentity(acct(p.account_id))}</strong><p>${esc(p.title||data.drafts.find(d=>d.id===p.draft_id)?.title||'平台稿件')}</p><p class="${publishNeedsAttention(p)?'status-warn':''}">${esc(names[p.status]||p.status)} · ${esc(p.message||'等待后台更新')}</p>${button(publishNeedsAttention(p)?'查看 / 处理':'查看详情','publication:'+p.id)}</section>`).join('')||'<p>暂无进行中的发布任务。</p>'}${savedPublishBatchPanel()}`;
}
function updatePublishMonitor(){
 const sidebar=$('#publish-monitor');if(sidebar)setStableHTML(sidebar,publishMonitorSidebar());
 const body=$('#publish-monitor-body');if(body&&$('#dialog').open)setStableHTML(body,publishMonitorBody());
}
document.addEventListener('click',e=>{
 if(e.target.closest('[data-action="batch-monitor"]'))modal('发布任务',`<div id="publish-monitor-body">${publishMonitorBody()}</div>`,button('收起，后台继续','close'));
});

function publicationRecovery(p,id){
 if(p.status==='failed'&&p.draft_id)return button('修改文案 / 封面并重新确认','batch-one:'+p.draft_id,true);
 if(p.status!=='needs_check')return '';
 const urls={wechat_channels:'https://channels.weixin.qq.com/platform/post/list',douyin:'https://creator.douyin.com/creator-micro/content/manage',bilibili:'https://member.bilibili.com/platform/upload-manager/article',kuaishou:'https://cp.kuaishou.com/article/manage/video'};
 return `<div class="panel"><p>先核对该账号的平台作品记录，再确认本次结果；未确认前不会重新投稿。</p>${urls[p.platform]?`<a target="_blank" rel="noopener" href="${urls[p.platform]}">打开平台作品记录 ↗</a>`:''}<label class="confirm"><input id="receipt-checked" type="checkbox">我已核对该账号作品列表和审核中的稿件</label>${button('确认未发布，允许重新检查','batch-resolve:not_published:'+id)}${button('确认已发布，保留成功记录','batch-resolve:published:'+id)}</div>`;
}
