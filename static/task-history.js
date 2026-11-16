function canCompleteTask(t){
 return !t.in_history&&t.status==='approved'&&t.reviewed_version===t.version&&t.production_job?.status!=='running'&&!data.publications.some(p=>p.task_id===t.id&&['queued','running','needs_check'].includes(p.status));
}
function taskCompletionButton(t){return button('标记完成','complete-task:'+t.id,false,data.workflow_features?.task_completion?'':'disabled title="当前发布结束后启用"')}
function taskTargetButton(t,label='新增发布账号'){
 const pending=!data.workflow_features?.history_targets&&data.publications.some(p=>p.task_id===t.id&&p.status==='succeeded');
 return button(label,'task-targets:'+t.id,false,pending?'disabled title="当前发布结束后启用"':'');
}
function patchReadOnlyView(main,html){
 const template=document.createElement('template');template.innerHTML=html;
 const key=node=>node.nodeType===1?(node.getAttribute('data-publication-task')||node.getAttribute('data-account-id')||node.id||''):'';
 function patch(current,next){
  if(current.nodeType!==next.nodeType||current.nodeName!==next.nodeName){current.replaceWith(next);return}
  if(current.nodeType===3){if(current.data!==next.data)current.data=next.data;return}
  if(current.nodeType!==1)return;
  for(const attribute of [...current.attributes])if(!(current.tagName==='DETAILS'&&attribute.name==='open')&&!next.hasAttribute(attribute.name))current.removeAttribute(attribute.name);
  for(const attribute of [...next.attributes])if(current.getAttribute(attribute.name)!==attribute.value)current.setAttribute(attribute.name,attribute.value);
  let cursor=current.firstChild;
  for(const child of [...next.childNodes]){
   const id=key(child);let existing=cursor;
   if(id&&key(existing||{})!==id)existing=[...current.childNodes].find(node=>key(node)===id);
   if(!existing){current.insertBefore(child,cursor);continue}
   if(existing!==cursor)current.insertBefore(existing,cursor);
   const after=existing.nextSibling;patch(existing,child);cursor=after;
  }
  while(cursor){const after=cursor.nextSibling;cursor.remove();cursor=after}
 }
 if(main.firstChild&&template.content.firstChild)patch(main.firstChild,template.content.firstChild);else main.replaceChildren(template.content);
}
function taskHistoryDetail(t){
 const asset=data.assets.find(a=>a.id===t.asset_id)||{},cancelled=t.lifecycle==='cancelled';
 const cover=t.cover_landscape_path?coverUrl(t,'landscape'):`/api/media/assets/${t.asset_id}/${asset.original_thumbnail?'original-cover':'thumbnail'}`;
 const count=new Set(data.publications.filter(p=>p.task_id===t.id&&p.status==='succeeded').map(p=>p.account_id)).size;
 return `<section class="task-history-detail"><section class="panel history-copy"><div class="history-state">${pill(cancelled?'已取消':t.lifecycle==='completed'?'已完成':'已发送',cancelled?'warn':'good')}<small>${count?`${count} 个账号已接收`:'暂无成功回执'}</small></div><img class="history-cover" src="${esc(cover)}" alt="${esc(t.title)}封面"><h2>${esc(t.title)}</h2><div class="history-tags">${(t.tags||[]).map(tag=>`<span>#${esc(tag)}</span>`).join('')}</div><div class="history-description">${esc(t.description||'暂无简介')}</div></section><section class="panel history-media">${t.video_path?`<video controls playsinline preload="metadata" aria-label="完整成片" src="/api/media/tasks/${t.id}/video?v=${encodeURIComponent(t.content_hash||t.version||1)}"></video>`:'<p class="muted">暂无成片</p>'}${localFilePath(t.video_path)}<div class="actions">${t.video_path?button('打开文件位置','reveal-task:'+t.id):''}${!cancelled?taskTargetButton(t)+link('稿件与回执','publish/drafts/'+t.id):''}${button('原始素材','asset:'+t.asset_id)}${button('重新制作','redo-task:'+t.id)}${link('返回历史','tasks/history')}</div></section></section>`;
}
