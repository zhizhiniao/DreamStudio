function taskNext(t){
 const pubs=data.publications.filter(p=>p.task_id===t.id);
 if(pubs.some(p=>p.status==='needs_check'))return {group:'attention',label:'核对发布结果',hash:'publish/drafts/'+t.id,note:'结果未确认，请先核对回执，勿重复发布'};
 if(pubs.some(p=>['queued','running'].includes(p.status)))return {group:'running',label:'查看发布进度',hash:'publish/drafts/'+t.id,note:'发布正在执行'};
 if(t.production_job?.status==='running'||t.status==='rendering')return {group:'running',label:'查看制作进度',hash:'tasks/detail/'+t.id,note:t.production_job?.message||t.progress||'后台制作中'};
 if(t.status==='failed'||t.production_job?.status==='failed')return {group:'attention',label:'修复并继续制作',hash:'tasks/detail/'+t.id,note:t.error||t.production_job?.message||'制作未完成，已有结果保留'};
 if(t.reviewed_version===t.version&&t.status==='approved'){
  const drafts=data.drafts.filter(d=>d.task_id===t.id&&acct(d.account_id));
  if(!drafts.length)return {group:'attention',label:'选择发布账号',action:'task-targets:'+t.id,note:'成片已审核，尚无可用平台稿件'};
  const accepted=new Set(pubs.filter(p=>p.status==='succeeded').map(p=>p.account_id));
  const failed=pubs.some(p=>p.status==='failed'&&!accepted.has(p.account_id));
  return {group:failed?'attention':'publish',label:failed?'处理未完成平台':'检查平台稿件',hash:'publish/drafts/'+t.id,note:accepted.size?`${accepted.size} 个账号已接收，其余账号待处理`:'审核已通过，确认文案与发布账号后再投稿'};
 }
 if(t.status==='review')return {group:'attention',label:'审片并确认',hash:'tasks/review/'+t.id,note:'成片已准备，请检查当前版本'};
 return {group:'running',label:'继续制作',hash:'tasks/detail/'+t.id,note:!t.mode?'先选择处理方式':'保存修改并完成当前版本制作'};
}
function recentFirst(items){return [...items].sort((a,b)=>(b.updated_at||b.created_at||0)-(a.updated_at||a.created_at||0)||String(a.id).localeCompare(String(b.id)))}
function publishedTasks(){return data.tasks.filter(t=>(t.task_complete||t.publication_complete)&&t.lifecycle!=='cancelled')}
function taskVisual(t){
 const a=data.assets.find(a=>a.id===t.asset_id)||{},next=t.in_history?null:taskNext(t);
 const cover=!!t.cover_landscape_path,done=!!(t.task_complete||t.publication_complete);
 const error=next?.group==='attention'&&(t.status==='failed'||t.production_job?.status==='failed'||data.publications.some(p=>p.task_id===t.id&&['failed','needs_check'].includes(p.status)));
 return {title:t.title||a.title||'未命名视频',src:cover?coverUrl(t,'landscape'):`/api/media/assets/${t.asset_id}/${a.original_thumbnail?'original-cover':'thumbnail'}`,tone:error?'red':done?'green':'blue',stage:t.lifecycle==='cancelled'?'已取消':done?(t.lifecycle==='completed'?'已完成':'已发送'):error?'需处理':t.production_job?.status==='running'?(t.production_job.kind==='finish'?'发布待命 · '+Math.floor(t.production_job.percent||0)+'%':'制作中'):t.status==='review'?'待审片':t.reviewed_version===t.version?'待发布':'制作中',coverLabel:cover?(t.cover_style==='original'?'原始封面':'中文封面'):'原始封面 · 制作中'};
}
function visualTaskCard(t,history=false){
 const v=taskVisual(t),next=history?{label:'查看历史与回执',hash:'tasks/history/'+t.id,note:t.lifecycle==='cancelled'?'原片与制作记录保留':t.lifecycle==='completed'?'已手动完成，可继续添加发布账号':'平台已接收，审核结果以平台为准'}:taskNext(t);
 return `<article class="task-card visual-task tone-${v.tone}" data-task-id="${t.id}"><a class="task-cover" href="#${history?'tasks/history/': 'tasks/detail/'}${t.id}"><img loading="lazy" src="${esc(v.src)}" alt="${esc(v.coverLabel+'：'+v.title)}"><span>${v.coverLabel}</span></a><div class="task-copy"><div class="task-meta"><span class="stage-label">${t.production_job?.kind==='finish'&&t.production_job.status==='running'?`<progress max="100" value="${Math.min(99,t.production_job.percent||0)}" aria-label="生成进度"></progress>`:''}${v.stage}</span><time>${time(t.updated_at)}</time></div><h3 title="${esc(v.title)}">${esc(v.title)}</h3><small class="task-note">${esc(next.note)}</small><div class="actions">${next.action?button(next.label,next.action):link(next.label,next.hash)}${history&&t.video_path?button('打开文件位置','reveal-task:'+t.id):''}${!history&&canCompleteTask(t)?taskCompletionButton(t):''}${history||['queued','running','needs_check','succeeded'].some(status=>data.publications.some(p=>p.task_id===t.id&&p.status===status))||t.production_job?.status==='running'||t.status==='rendering'?'':button('取消','cancel-task:'+t.id)}</div></div></article>`;
}
function rawAssetCard(a){return `<article class="task-card visual-task tone-yellow" data-asset-id="${a.id}"><button class="task-cover" data-action="asset:${a.id}" aria-label="查看原始素材：${esc(a.title)}"><img loading="lazy" src="/api/media/assets/${a.id}/${a.original_thumbnail?'original-cover':'thumbnail'}" alt="${esc(a.title)}"><span>原始封面</span></button><div class="task-copy"><div class="task-meta"><span class="stage-label">待制作 · 原始素材</span><time>${time(a.updated_at)}</time></div><h3 title="${esc(a.title)}">${esc(a.title)}</h3><small>${esc(a.source||'本机导入')} · ${Math.round(a.duration||0)} 秒</small><div class="actions">${button('开始制作','create-task:'+a.id)}</div></div></article>`}
function waitingDownloads(){return (data.downloads||[]).filter(j=>j.status!=='completed'&&!data.tasks.some(t=>t.id===j.task_id)&&!data.assets.some(a=>a.id===j.asset_id))}
function downloadCard(j){return `<article class="task-card visual-task tone-${j.status==='failed'?'red':'yellow'}"><div class="download-placeholder" aria-label="封面尚未获取">${icon('play')}<small>封面待获取</small></div><div class="task-copy"><div class="task-meta"><span class="stage-label">${j.status==='failed'?'下载失败':'下载中'}</span><time>${time(j.updated_at)}</time></div><h3>${esc(j.title||'YouTube 视频')}</h3><small>${esc(j.message||'正在获取视频与封面')}</small>${link('查看下载','assets/download')}</div></article>`}
function overviewHome(){
 const unused=data.assets.filter(a=>!a.used),valid=data.accounts.filter(a=>a.state==='valid'&&!a.stale&&!a.paused).length;
 const rows=[...data.tasks.map(t=>({id:'t'+t.id,updated_at:t.updated_at,html:visualTaskCard(t,!!t.in_history)})),...unused.map(a=>({id:'a'+a.id,updated_at:a.updated_at,html:rawAssetCard(a)})),...waitingDownloads().map(j=>({id:'d'+j.id,updated_at:j.updated_at,html:downloadCard(j)}))];
 return heading('首页',button('粘贴 YouTube 链接','youtube-open',true)+button('导入视频','upload')+link('工作台','home'))+
 `<div class="overview-stats">${[['账号有效 / 全部',valid+' / '+data.accounts.length,'accounts/list'],['待制作素材',unused.length,'assets/library/unused'],['进行中任务',activeTasks().length,'tasks/list'],['已完成任务',publishedTasks().length,'tasks/history']].map(([label,value,url])=>`<a href="#${url}"><span>${label}</span><strong>${value}</strong></a>`).join('')}</div>`+
 `<div class="overview-connections"><span class="source-brand">${platformLogo('youtube')}YouTube ${youtubeConnection()}</span>${link('连接设置','accounts/youtube')}<span class="overview-accounts">${data.accounts.map(a=>`<a href="#accounts/list" title="${esc(states[a.state]||a.state)}">${accountIdentity(a)}</a>`).join('')}</span>${link('管理账号与发布目标','landing/manage')}</div>`+
 `<section class="overview-tasks block"><h2>最近任务与素材 <small>最近更新在前 · 黄色原片 / 蓝色制作 / 绿色完成 / 红色异常</small></h2><div class="overview-grid scroll-region">${recentFirst(rows).map(r=>r.html).join('')||empty('从一条视频开始','粘贴链接或导入视频后，会在这里显示封面与进度。')}</div></section>`;
}
function workbench(){
 const groups=workbenchItems();
 return heading('工作台',button('粘贴 YouTube 链接','youtube-open',true)+button('导入本机视频','upload')+link('素材库','assets/library'))+tabs('home','overview')+
 `<div class="board-caption"><span>按阶段推进 · 最近更新在前</span><a href="#tasks/history">全部记录 →</a></div>`+
 `<div class="workbench-board">${[['preparation','准备','暂无待用素材'],['production','准备制作','暂无制作任务'],['confirmation','待确认','暂无待确认内容'],['history','记录','暂无历史记录']].map(([key,title,hint])=>`<section class="block board-${key}" aria-label="${title}"><h2>${title}<small>${groups[key].length}</small></h2><div class="scroll-region">${recentFirst(groups[key]).map(r=>r.html).join('')||`<p class="workbench-empty">${hint}</p>`}</div></section>`).join('')}</div>`;
}
function workbenchStage(t){
 const next=taskNext(t);
 return ['attention','publish'].includes(next.group)||data.publications.some(p=>p.task_id===t.id&&['queued','running','needs_check'].includes(p.status))?'confirmation':'production';
}
function workbenchItems(){
 const groups={preparation:[],production:[],confirmation:[],history:[]};
 activeTasks().forEach(t=>groups[workbenchStage(t)].push({id:'task:'+t.id,updated_at:t.updated_at,html:taskCard(t)}));
 data.tasks.filter(t=>t.in_history).forEach(t=>groups.history.push({id:'task:'+t.id,updated_at:t.updated_at,html:historyCard(t)}));
 data.assets.filter(a=>!a.used).forEach(a=>groups.preparation.push({id:'asset:'+a.id,updated_at:a.updated_at,html:rawAssetCard(a)}));
 waitingDownloads().forEach(j=>groups[j.status==='failed'?'confirmation':'preparation'].push({id:'download:'+j.id,updated_at:j.updated_at,html:downloadCard(j)}));
 return groups;
}
