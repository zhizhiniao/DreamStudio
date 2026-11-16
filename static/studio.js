'use strict';
const $=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={douyin:'抖音',kuaishou:'快手',xiaohongshu:'小红书',wechat_channels:'视频号',bilibili:'B站',youtube:'YouTube'};
const states={valid:'有效',unchecked:'待检查',checking:'检查中',expired:'已失效',missing:'未登录',verification:'待验证',unavailable:'未确认',environment:'环境异常',unsupported:'待接入'};
const nav=[['landing','首页'],['home','工作台'],['assets','素材库'],['tasks','制作任务'],['publish','发布中心'],['accounts','账号与运营'],['settings','设置']];
const sections={home:[['overview','今日概况'],['activity','活动记录'],['notifications','通知与异常'],['map','功能全景']],assets:[['library','全部素材'],['collect','YouTube 搜索'],['monitor','频道跟踪'],['download','链接下载']],tasks:[['list','当前任务'],['review','内容审核'],['history','历史记录'],['templates','制作模板']],publish:[['drafts','平台稿件'],['records','发布记录'],['calendar','排期与历程']],accounts:[['list','全部账号'],['youtube','YouTube 设置'],['works','已发布作品'],['comments','评论'],['follows','关注与粉丝'],['messages','私信'],['analytics','数据概览'],['risk','账号状态']],settings:[['general','运行设置'],['production','制作服务'],['connections','能力接入'],['storage','文件与备份']]};
const paths={landing:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',home:'M3 10 12 3l9 7v10H3z M9 20v-7h6v7',assets:'M3 6h7l2 3h9v11H3z M3 6V4h7l2 2',tasks:'M4 4h16v16H4z M8 8h8 M8 12h5 M8 16h7',publish:'m3 11 18-8-8 18-3-8z M10 13l11-10',accounts:'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M4 21v-3a8 8 0 0 1 16 0v3',settings:'M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2',arrow:'M5 12h14 M14 7l5 5-5 5',play:'m8 4 13 8-13 8z',check:'m5 12 4 4 10-10',plus:'M12 4v16 M4 12h16',close:'M6 6l12 12 M6 18 18 6',expand:'M8 3H3v5 M16 3h5v5 M3 16v5h5 M21 16v5h-5',menu:'M4 6h16 M4 12h16 M4 18h16'};
const icon=n=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[n]||paths.landing}"/></svg>`;
const button=(label,action,primary=false,extra='')=>`<button class="btn ${primary?'primary':''}" data-action="${action}" ${extra}>${label}</button>`;
const link=(label,hash,primary=false)=>`<a class="btn ${primary?'primary':''}" href="#${hash}">${label}${icon('arrow')}</a>`;
const pill=(label,cls='')=>`<span class="pill ${cls}">${esc(label)}</span>`;
const accountPill=a=>pill(a.paused?'已停用':states[a.state]||a.state,a.state==='valid'&&!a.paused?'good':a.state==='expired'?'bad':'warn');
const field=(label,value='',id='',type='text')=>`<label class="field"><span>${label}</span><input id="${id}" value="${esc(value)}" type="${type}"></label>`;
const textarea=(label,value,id)=>`<label class="field"><span>${label}</span><textarea id="${id}">${esc(value)}</textarea></label>`;
const table=(heads,rows)=>`<div class="tablewrap read-table"><table><thead><tr>${heads.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${v}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
const empty=(title,detail,action='')=>`<div class="empty"><h2>${title}</h2><p>${detail}</p>${action}</div>`;
const heading=(title,actions='')=>`<div class="page-header"><h1>${title}</h1><div class="actions">${route[0]==='tasks'?`<button class="iconbtn mobilemenu" data-action="menu" aria-label="打开导航">${icon('menu')}</button>`:''}${actions}</div></div>`;
const acct=id=>data.accounts.find(a=>a.id===id);
const titleAccount=a=>a?String(a.name||'昵称待确认').replace(/\s*\/\s*\d+$/,''):'账号已移除';
const dateInput=t=>{const d=new Date(t*1000);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16)};
const time=t=>t?new Date(t*1000).toLocaleString('zh-CN',{hour12:false}):'尚未检查';
const tabs=(group,sub)=>sections[group]?`<nav class="tabs" aria-label="${group}子页面">${sections[group].map(([id,label])=>`<a href="#${group}/${id}" class="${id===sub?'active':''}">${label}</a>`).join('')}</nav>`:'';
const statusTask={draft:'待制作',rendering:'制作中',review:'待审核',approved:'已审核',failed:'失败'};
let data={accounts:[],context:{source:'',targets:[]},assets:[],tasks:[],drafts:[],publications:[],events:[],issues:[],collections:[],monitors:[]};
let accountPlatform='',accountTag='',accountCheckSubmitting=false;
function accountCheckActions(){
 const j=data.account_check,busy=accountCheckSubmitting||j?.running;
 const label=accountCheckSubmitting?'正在开始检查…':j?.running?`检查中 ${j.completed} / ${j.total}`:'检查全部账号';
 return button(label,'check-all',false,`${busy?'disabled aria-busy="true"':''} aria-live="polite"`)+(!busy&&j?.items.some(x=>x.state!=='valid'&&x.state!=='removed')?button('重试未通过账号','retry-accounts'):'');
}
async function startAccountCheck(retry=false){
 if(accountCheckSubmitting)return;accountCheckSubmitting=true;accountPlatform='';accountTag='';location.hash='accounts/list';render();
 try{data.account_check=await api('accounts/check-batch','POST',{retry});await refresh()}finally{accountCheckSubmitting=false;render()}
}
let route=[],platform='全部',selected=new Set(),opAccount='',pollTimer,toastTimer,loginTimer;

async function api(path,method='GET',payload){
 const options={method};if(payload instanceof FormData)options.body=payload;else if(method!=='GET'){options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(payload||{})}
 const response=await fetch('/api/'+path,options);const result=await response.json();if(!response.ok){const error=new Error(result.error||result.detail||'操作未完成');error.status=response.status;throw error}return result;
}
function toast(message){$('#toast').textContent=message;$('#toast').style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').style.display='none',4500)}
function modal(title,body,actions=''){const d=$('#dialog');++dialogRevision;d.classList.remove('media-screen');d.innerHTML=`<div class="dialoghead"><h2 id="dialog-title">${title}</h2><button class="iconbtn" data-action="close" aria-label="关闭弹窗">${icon('close')}</button></div><div class="dialogbody">${body}<p id="dialog-error" class="status-error" role="alert" aria-live="assertive"></p></div><div class="dialog-actions">${button('关闭','close')}${actions}</div>`;d.setAttribute('aria-labelledby','dialog-title');if(!d.open)d.showModal()}
let dialogRevision=0;
document.addEventListener('close',e=>{if(e.target.id==='dialog')dialogRevision++},true);
function close(){++dialogRevision;clearInterval(loginTimer);$('#dialog').close()}
async function refresh(redraw=true){const subtitleScroll=document.querySelector('.subtitle-scroll')?.scrollTop;data=await api('state');await attachVoiceCatalogue();await refreshSavedPublishBatch();if(redraw){render();if(typeof resizeSubtitleInputs==='function')resizeSubtitleInputs();const list=document.querySelector('.subtitle-scroll');if(list&&subtitleScroll!==undefined)list.scrollTop=subtitleScroll}else updateFlowStatus();updatePublishMonitor();schedulePoll()}
function schedulePoll(){clearTimeout(pollTimer);if((data.youtube_discovery?.sources||[]).some(s=>['queued','running'].includes(s.status))||(data.downloads||[]).some(j=>['queued','running'].includes(j.status)||j.subtitle_busy)||data.accounts.some(a=>a.state==='checking')||data.tasks.some(t=>t.status==='rendering'||t.production_job?.status==='running')||data.publications.some(p=>['queued','running'].includes(p.status))){pollTimer=setTimeout(async()=>{try{await refresh(!$('#production-settings')&&!$('#youtube-settings')&&!$('#dialog').open&&!document.activeElement?.matches('input,textarea,select')&&!voicePreviewAudio)}catch(e){toast(e.message);schedulePoll()}},2200)}}
function flow(){return `<nav class="flow" aria-label="创作流程">${[['assets','挑选素材','assets/library'],['tasks','制作成片','tasks/list'],['check','内容审核','tasks/review'],['publish','发布内容','publish/drafts'],['accounts','查看反馈','accounts/analytics']].map(([ic,label,h])=>`<a href="#${h}">${icon(ic)}<strong>${label}</strong></a>`).join('')}</nav>`}
function gatePage(){return heading('先确认本次发布账号')+`<div class="panel">${data.issues.map(i=>`<div class="lineitem"><div>${accountIdentity(acct(i.id))}<small>${esc(i.reason)}</small></div></div>`).join('')||'<p>请在首页选择素材账号和发布目标，并完成在线检查。</p>'}<div class="actions" style="margin-top:20px">${link('前往首页处理','landing',true)}</div></div>`}
function setStableHTML(element,html){if(element._renderedHTML!==html){element.innerHTML=html;element._renderedHTML=html}}
function render(){
 if(typeof stopVoicePreview==='function')stopVoicePreview();
 route=(location.hash.slice(1)||'landing').split('/');let[group,sub]=route;if(!nav.some(n=>n[0]===group))group='landing';sub=sub||sections[group]?.[0][0]||'';if(group==='publish'&&['queue','history'].includes(sub))sub='records';route=[group,sub,...route.slice(2)];
 document.title=`${nav.find(n=>n[0]===group)[1]} · 造梦室`;document.body.dataset.page=group==='assets'&&['collect','monitor','download'].includes(sub)?'assets-'+sub:group==='home'&&sub!=='overview'?'home-'+sub:group;
 setStableHTML($('#sidebar'),`<a class="brand" href="#landing"><img src="/static/assets/dreamroom.svg?v=20261115" width="32" height="32" alt=""><strong>造梦室</strong></a><nav>${nav.map(([id,label])=>`<a class="navlink ${id===group?'active':''}" href="#${id}">${icon(id)}<span>${label}</span></a>`).join('')}</nav><div class="sideend"><div id="publish-monitor">${publishMonitorSidebar()}</div><div class="sidecard"><a href="#home/map">${icon('landing')}功能全景</a></div><div class="identity"><div class="avatar">梦</div><div>DreamStudio<br><small>Y2A-Auto · CreatorHub</small></div></div></div>`);
 setStableHTML($('#topbar'),`<div class="crumb"><button class="iconbtn mobilemenu" data-action="menu" aria-label="打开导航">${icon('menu')}</button><span>我的工作空间</span><span>/</span><strong>${nav.find(n=>n[0]===group)[1]}</strong></div><div class="actions"><button class="btn gate-status-compact" data-action="check-key" title="${esc(data.issues.map(i=>i.reason).join('；')||'点击重新检查关键账号')}">${pill(data.ready?'发布账号检查通过':data.accounts.some(a=>a.state==='checking')?'发布账号检查中':'发布账号待处理',data.ready?'good':'warn')}</button><span class="mini">${data.accounts.filter(a=>a.state==='valid'&&!a.stale&&!a.paused).length} / ${data.accounts.length} 个账号有效</span><span class="mini" title="关闭或切换网页后，任务继续运行；电脑需保持开机且不休眠">后台服务运行中</span>${button('刷新','refresh',false)}</div>`);
 const views={landing:landing,home:home,assets:assetsPage,tasks:tasksPage,publish:publishPage,accounts:accountsPage,settings:settings};
 const main=$('#main'),scrollSelector='.overview-grid,.workbench-board .scroll-region,.workbench-board';
 const scrollPositions=main.dataset.scrollRoute===location.hash?[...main.querySelectorAll(scrollSelector)].map(el=>el.scrollTop):[];
 const html=`<div class="page">${views[group](sub)}</div>`;
 if(main._renderedHTML!==html||main.dataset.scrollRoute!==location.hash){
  const readonly=group==='publish'&&['records','calendar'].includes(sub)||group==='tasks'&&data.tasks.some(t=>t.id===route[2]&&t.in_history);
  if(readonly&&main.dataset.scrollRoute===location.hash)patchReadOnlyView(main,html);else main.innerHTML=html;
  main._renderedHTML=html;
 }
 main.dataset.scrollRoute=location.hash;main.querySelectorAll(scrollSelector).forEach((el,i)=>{if(scrollPositions[i]!==undefined)el.scrollTop=scrollPositions[i]});
 if(group==='settings'&&sub==='production')loadProductionSettings();
 if(group==='accounts'&&sub==='youtube')loadYouTubeSettings();
 if(group==='accounts'&&['works','comments','follows','messages'].includes(sub))loadOperations();
}
function accountManagement(){
 const filtered=data.accounts.filter(a=>platform==='全部'||a.platform===platform);const ids=data.context.targets.filter(Boolean);
 return heading('账号管理',button('添加账号','add-account')+accountCheckActions()+button('进入工作台 →','enter',true))+
 `<div class="gate-summary"><div><span class="metriclabel">账号</span><strong>${data.accounts.length}</strong></div><div><span class="metriclabel">有效</span><strong>${data.accounts.filter(a=>a.state==='valid'&&!a.stale&&!a.paused).length}</strong></div><div><span class="metriclabel">本次发布账号</span><strong>${new Set(ids).size}</strong></div><div><span class="metriclabel">工作状态</span><b class="${data.ready?'':'gate-warn'}">可获取素材与制作</b></div></div>`+
 `<section class="gate-config source-strip"><div class="overseas-source"><div class="source-strip-heading"><span class="source-brand" title="海外素材来源">${platformLogo('youtube')}YouTube</span>${youtubeConnection()}</div><div class="actions">${button('粘贴链接','youtube-open',true)}<a class="btn" href="#accounts/youtube" title="YouTube 连接设置与检测">设置</a></div></div><div class="publish-target-strip"><span class="metriclabel" title="在下方账号表中勾选发布目标">发布到</span><div class="account-targets">${data.context.targets.map(id=>`<span class="pill">${accountIdentity(acct(id))}</span>`).join('')||'<small>在下方勾选账号</small>'}</div></div></section>`+

 `<div class="gate-toolbar"><div class="chips">${['全部',...Object.keys(labels)].filter(p=>p==='全部'||data.accounts.some(a=>a.platform===p)).map(p=>`<button class="chip ${p===platform?'active':''}" data-action="filter:${p}">${p==='全部'?'全部':platformLogo(p)} ${data.accounts.filter(a=>p==='全部'||a.platform===p).length}</button>`).join('')}</div><div class="actions"><span class="mini">已选 ${selected.size}</span>${button('批量检查','batch-check',false,selected.size?'':'disabled')}${button('登录管理','batch-login',false,selected.size?'':'disabled')}${button('停用','batch-pause',false,selected.size?'':'disabled')}${button('启用','batch-enable',false,selected.size?'':'disabled')}</div></div>`+
 `<div class="gate-table" tabindex="0" aria-label="账号列表">${table(['<input type="checkbox" id="select-all" aria-label="选择当前平台全部账号">','平台 / 账号身份','登录 / 读取','最近检查','发布目标','操作'],filtered.map(a=>[
 `<input type="checkbox" data-select="${a.id}" aria-label="选择 ${esc(titleAccount(a))}" ${selected.has(a.id)?'checked':''}>`,
 `${accountIdentity(a)}`,
 `${accountPill(a)}<small>读取：${esc(states[a.read_state]||'待检查')}</small><small>${esc(a.message||'')}</small>`,time(a.checked_at),`<label class="confirm" style="margin:0"><input type="checkbox" data-target="${a.id}" ${data.context.targets.includes(a.id)?'checked':''} ${a.platform==='youtube'?'disabled':''} aria-label="${esc(titleAccount(a))}">关键账号</label>`,`<div class="actions">${button(a.state==='checking'?'检查中…':'检查','check:'+a.id,false,a.state==='checking'?'disabled':'')}${button('登录管理','login:'+a.id)}${button('修改名称','edit-account:'+a.id)}${button('删除','delete-account:'+a.id,false,'aria-label="删除账号"')}</div>`]))}</div>`;
}
function activeTasks(){return data.tasks.filter(t=>!t.in_history)}
function assetStatus(a){return pill(a.used?'已使用':'未使用')+' '+pill(({submitted:'已投稿 · 平台已接收',needs_check:'投稿结果待核实',unpublished:'未投稿'})[a.publication_state]||'未投稿',a.publication_state==='submitted'?'good':'')+(a.has_pending_publication&&a.publication_state!=='needs_check'?' '+pill('投稿结果待核实','warn'):'')+(a.returned_to_library?' '+pill('已退回素材库'):'')}
function historyCard(t){return visualTaskCard(t,true)}
function taskCard(t){return visualTaskCard(t)}
function landing(sub){return sub==='manage'?accountManagement():overviewHome()}

function home(sub){
 if(sub==='map')return heading('功能全景')+tabs('home',sub)+`<div class="sitemap">${nav.map(([g,label])=>`<section class="mapgroup"><h2>${label}</h2>${(sections[g]||[['','账号检查']]).map(([id,n])=>`<a href="#${g}/${id}">${n} ↗</a>`).join('')}</section>`).join('')}</div>`;
 if(sub==='activity')return heading('活动记录')+tabs('home',sub)+table(['时间','动作','内容'],data.events.map(e=>[time(e.at),esc(e.action),esc(e.detail)]));
 if(sub==='notifications')return heading('通知与异常')+tabs('home',sub)+table(['类型','内容','处理'],[...data.accounts.filter(a=>a.state!=='valid').map(a=>['账号',accountIdentity(a)+' · '+esc(a.message||states[a.state]),link('处理账号','landing')]),...activeTasks().filter(t=>t.status==='failed').map(t=>['任务',esc(t.title)+' · '+esc(t.error),link('打开任务','tasks/detail/'+t.id)]),...data.publications.filter(p=>['failed','needs_check'].includes(p.status)).map(p=>['投稿',esc(p.title)+' · '+esc(p.message),link('核对记录','publish/history')])]);
 return workbench();
}
function assetsPage(sub){
 if(['collect','monitor'].includes(sub))return youtubeDiscoveryPage(sub);
 const top=heading('素材库',button('导入视频','upload',true))+tabs('assets',sub);
 if(sub==='collect')return top+`<div class="form-split"><section class="panel"><h2 style="margin-bottom:18px">创建关键词采集</h2>${textarea('关键词 · 每行一个','','keywords')}${field('每个关键词作品数量',10,'collection-limit','number')}${domesticPicker()}${button('开始采集','collect',true)}</section><section class="block"><h2>采集任务</h2><div class="scroll-region">${data.collections.length?data.collections.map(j=>`<div class="task-card"><h3>${esc(j.keywords.join('、'))}</h3><small>${accountIdentity(acct(j.account_id))}</small><div class="actions">${button('查看进度','collection:'+j.id)}${button('导入已下载素材','import-collection:'+j.id)}</div></div>`).join(''):empty('暂无采集任务','采集结果保存在本项目中，可进入素材库继续制作。')}</div></section></div>`;
 if(sub==='monitor')return top+`<div class="form-split"><section class="panel"><h2 style="margin-bottom:18px">新增作品监控</h2>${domesticPicker()}${field('账号或作品链接','','monitor-url')}${field('名称','','monitor-name')}${button('创建监控','monitor',true)}</section><section class="panel"><h2>监控列表</h2>${data.monitors.map(m=>`<div class="lineitem"><div>${esc(m.label)}<small>${accountIdentity(acct(m.account_id))}</small></div><div class="actions">${pill(m.status)}${button(m.status==='active'?'暂停':'恢复','monitor-toggle:'+m.id)}${button('立即检查','monitor-run:'+m.id)}${button('查看作品','monitor-contents:'+m.id)}</div></div>`).join('')||empty('暂无监控','添加目标后，CreatorHub 会按独立配置读取作品。')}</section></div>`;
 if(sub==='download')return top+youtubeDownloadWorkspace();
 const filter=assetFilterState(route);const materials=data.assets.filter(a=>assetMatches(a,filter));
 return top+assetFilterBar(filter)+`<div class="grid asset-grid">${materials.map(a=>`<article class="asset"><img class="thumb" src="/api/media/assets/${a.id}/thumbnail" alt="${esc(a.title)}"><div class="body"><small>${esc(a.source)} · ${Math.round(a.duration)} 秒</small><h3>${esc(a.title)}</h3><small>${esc(a.author||titleAccount(acct(a.source_account)))}</small><div>${assetStatus(a)}</div><div class="foot">${button('查看详情','asset:'+a.id)}${button('制作','create-task:'+a.id,true)}</div></div></article>`).join('')||empty(data.assets.length?'没有符合筛选的素材':'暂无素材',data.assets.length?'可切换上方筛选条件。':'导入视频或从关键词采集结果中导入。',data.assets.length?'':button('导入视频','upload',true))}</div>`;
}
function tasksPage(sub){
 const key=route[2],t=data.tasks.find(x=>x.id===key);const top=heading('制作任务')+tabs('tasks',sub);
 if(t?.in_history)return top+taskHistoryDetail(t);
 if(sub==='history')return top+`<div class="task-list">${data.tasks.filter(t=>t.in_history).map(historyCard).join('')||empty('暂无历史记录','已投稿和已取消的制作任务会保留在这里。')}</div>`;
 if(sub==='templates')return top+`<div class="settings-grid">${Object.entries(productionModes).map(([mode,label])=>`<section class="panel"><h2>${label}</h2><p class="muted" style="margin:14px 0">${{dub:'完整中文配音与中文字幕；支持生成或导入已有配音。',subtitles:'保留原台词、音乐与音效，增加中文字幕。',original:'保留原声音与画面，不重新编码。'}[mode]}三种方式均逐步确认中文标题、简介、标签和封面。</p>${link('选择素材','assets/library',true)}</section>`).join('')}</div>`;
 if(sub==='review'&&t&&!flowRendered(t))return productionPage(t);
 if(sub==='detail'&&t)return productionPage(t);
 if(sub==='review'&&t)return heading('内容审核',link('返回制作任务','tasks/detail/'+t.id))+`<div class="editor"><section><video controls preload="metadata" src="/api/media/tasks/${t.id}/video"></video><div class="video-tools"><small>成片 v${t.version}</small>${button(icon('expand')+' 放大审片','maximize:'+t.id)}</div><div class="covers"><button type="button" class="cover-enlarge" data-cover-preview="landscape" data-task-id="${t.id}" aria-label="放大查看横版封面"><img src="${coverUrl(t,'landscape')}" alt="横版封面"></button><button type="button" class="cover-enlarge" data-cover-preview="portrait" data-task-id="${t.id}" aria-label="放大查看竖版封面"><img src="${coverUrl(t,'portrait')}" alt="竖版封面"></button></div></section><section class="panel"><h2>${esc(t.title)}</h2><p style="white-space:pre-wrap;margin:16px 0">${esc(t.description||'简介待补充')}</p><p>${(t.tags||[]).map(tag=>'#'+esc(tag)).join('　')}</p><p class="mini">封面方式：${t.cover_style==='original'?'原始封面':'撕纸拼贴'}</p><label class="confirm"><input id="review-confirm" type="checkbox">已检查中文标题、画面、声音、字幕、简介、标签和封面</label>${t.reviewed_version===t.version?button('进入当前任务平台稿件','drafts:'+t.id,true):button('审核通过并进入平台稿件','approve:'+t.id,true)}</section></div>`;
 const items=sub==='review'?activeTasks().filter(t=>['review','approved'].includes(t.status)):activeTasks();
 return top+`<div class="task-list">${recentFirst(items).map(taskCard).join('')||empty('暂无任务','从素材库选择视频后开始制作。',link('打开素材库','assets/library',true))}</div>`;
}
function publishPage(sub){
 const top=heading('发布中心')+tabs('publish',sub)+savedPublishBatchPanel();const d=data.drafts.find(x=>x.id===route[2]);
 if(sub==='editor'&&d)return heading('平台稿件',link('返回当前任务稿件','publish/drafts/'+d.task_id))+`<div class="editor"><section class="panel"><h2 style="margin-bottom:18px">${accountIdentity(acct(d.account_id))}</h2>${button('调整横版 / 竖版封面','draft-cover:'+d.id)}${field('标题',d.title,'draft-title')}${d.platform==='wechat_channels'?field('视频号短标题 · 最多 16 字',d.short_title||'','draft-short-title'):''}${textarea(d.platform==='kuaishou'?'快手简介':'正文',d.body,'draft-body')}${d.platform==='kuaishou'?`<small data-kuaishou-caption-count>合计 ${kuaishouCaptionCount(d)} / 1000 字</small>`:''}${field('话题 · 逗号分隔',d.tags.join(','),'draft-tags')}${field('计划发布时段 · 到期仍需人工确认',d.planned_at?dateInput(d.planned_at):'','draft-time','datetime-local')}<div class="form-actions">${button(publishSelection.has(d.id)?'保存并返回批量确认':'保存草稿','save-draft:'+d.id)}${data.tasks.find(t=>t.id===d.task_id)?.version!==d.task_version?button('同步当前成片，保留文案','sync-draft:'+d.id):''}</div></section><section class="panel"><h2>发布前检查</h2><div class="lineitem"><div>成片版本<small>v${d.task_version}</small></div></div><div class="lineitem"><div>账号状态</div>${accountPill(acct(d.account_id))}</div><div class="lineitem"><div>稿件审核</div>${pill(d.reviewed?'已确认':'待确认',d.reviewed?'good':'warn')}</div>${button(publishSelection.has(d.id)?'返回批量确认':'统一确认并发布',publishSelection.has(d.id)?'batch-review':'batch-one:'+d.id,true)}<p class="mini">无需单独审核；多个账号可返回发布中心一起勾选。</p></section></div>`;
 if(sub==='records')return top+publicationRecords();
 if(sub==='calendar')return top+publicationSchedule();
 return top+batchDrafts();
}
function accountsPage(sub){
 const top=heading('账号与运营',link('YouTube 设置','accounts/youtube')+accountCheckActions()+link('批量管理','landing/manage')+button('添加账号','add-account',true))+tabs('accounts',sub);
 if(sub==='youtube')return top+youtubeSettingsPage();
 if(sub==='list'){
 const pool=data.accounts.filter(a=>!accountPlatform||a.platform===accountPlatform);
 const tags=[...new Set(data.accounts.flatMap(a=>a.tags||[]))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
 const count=t=>pool.filter(a=>t==='__untagged'?!a.tags?.length:(a.tags||[]).includes(t)).length;
 const shown=pool.filter(a=>!accountTag||(accountTag==='__untagged'?!a.tags?.length:(a.tags||[]).includes(accountTag)));
 const filters=`<div class="account-filters"><label>平台 <select id="account-platform" aria-label="筛选账号平台"><option value="">全部平台</option>${Object.entries(labels).map(([id,l])=>`<option value="${id}" ${accountPlatform===id?'selected':''}>${l}</option>`).join('')}</select></label><div class="account-tag-filters" aria-label="按标签筛选">${[['','全部',pool.length],['__untagged','未标记',count('__untagged')],...tags.map(t=>[t,t,count(t)])].map(([t,l,n])=>button(`${esc(l)} <small>${n}</small>`,'account-tag:'+encodeURIComponent(t),accountTag===t,`aria-pressed="${accountTag===t}"`)).join('')}</div><small>显示 ${shown.length} 个账号</small></div>`;
 return top+filters+(shown.length?`<div class="grid accounts-grid">${shown.map(a=>`<article class="accountcard" aria-busy="${a.state==='checking'}"><div class="row between">${accountIdentity(a)}${accountPill(a)}</div><div class="account-tags">${(a.tags||[]).map(t=>pill(t)).join('')||'<small>未标记</small>'}</div><div class="numbers"><div><small>最近检查</small><small>${time(a.checked_at)}</small></div><div><small>会话</small>${pill(a.session_saved?'已保存':'未保存')}</div></div><div class="actions">${button(a.state==='checking'?'检查中…':'检查','check:'+a.id,false,a.state==='checking'?'disabled':'')}${button('登录管理','login:'+a.id)}${button('标签 / 资料','edit-account:'+a.id)}${button('删除账号','delete-account:'+a.id,false,'aria-label="删除账号"')}</div><small role="status" aria-live="polite">${esc(a.state==='checking'?'等待或正在检查…':a.message||'')}</small></article>`).join('')}</div>`:empty('没有符合条件的账号','可更换平台或标签，或清除筛选。',button('清除筛选','clear-account-filter')));
 }

 if(sub==='risk')return top+table(['账号','状态','原因','检查时间','处理'],data.accounts.map(a=>[accountIdentity(a),accountPill(a),esc(a.message||''),time(a.checked_at),button('登录管理','login:'+a.id)]));
 if(sub==='analytics')return top+`<div class="metrics">${[['账号',data.accounts.length],['素材',data.assets.length],['已审核成片',data.tasks.filter(t=>t.status==='approved').length],['收到投稿回执',data.publications.filter(p=>p.status==='succeeded').length]].map(([l,v])=>`<div class="metric"><span class="metriclabel">${l}</span><div class="metricval">${v}</div></div>`).join('')}</div>`+table(['账号','关联任务','平台稿件'],data.accounts.map(a=>[accountIdentity(a),data.tasks.filter(t=>t.targets.includes(a.id)||t.source_account===a.id).length,data.drafts.filter(d=>d.account_id===a.id).length]));
 return top+`<div class="toolbar"><input type="hidden" id="operation-account" value="${esc(opAccount||data.accounts.find(a=>a.platform!=='youtube'&&a.platform!=='bilibili')?.id||'')}"><button class="btn source-picker" data-action="choose-operation">${accountIdentity(acct(opAccount)||data.accounts.find(a=>a.platform!=='youtube'&&a.platform!=='bilibili'))}<span aria-hidden="true">⌄</span></button>${button('同步平台数据','sync:'+sub)}</div><div id="operations">读取中…</div>`;
}
async function loadOperations(){const key=$('#operation-account')?.value;if(!key){$('#operations').innerHTML=empty('没有可用账号','请先添加账号');return}try{const result=await api(`operations/${key}/${route[1]}`);const rows=Array.isArray(result)?result:result.items||result.works||result.conversations||result.data||[];if(!Array.isArray(rows))throw new Error('平台返回了未识别的数据结构');const whitelist=['title','nickname','text','content','created_at','name','description','like_count','comment_count','follower_count'];const keys=[...new Set(rows.flatMap(x=>Object.keys(x)))].filter(k=>whitelist.includes(k)).slice(0,5);$('#operations').innerHTML=rows.length?table(keys,rows.map(x=>keys.map(k=>esc(x[k]||'')))):empty('暂无已同步数据','点击同步读取本账号平台数据。')}catch(e){$('#operations').innerHTML=`<div class="status-error">${esc(e.message)}</div>`}}
function settings(sub){const top=heading('设置')+tabs('settings',sub);if(sub==='production')return top+productionSettingsPage();if(sub==='storage')return top+`<section class="panel" style="max-width:900px"><h2>文件与备份</h2><div class="lineitem"><div>数据库<small>data/studio.sqlite</small></div></div><div class="lineitem"><div>账号独立会话<small>data/accounts/账号编号/</small></div></div><div class="lineitem"><div>素材与成片<small>${esc(data.storage_root)} · 每个来源视频独立归档</small></div></div><div class="lineitem"><div>检查点<small>data/backups/</small></div>${button('创建备份','backup',true)}</div></section>`;if(sub==='connections')return top+table(['平台','账号检查','关键词采集','发布'],Object.keys(labels).map(p=>[labels[p],data.capabilities[p].login?'扫码 / 在线检查':p==='bilibili'?'会话导入 / 在线检查':'待接入',data.capabilities[p].collect?'已连接 CreatorHub':'未接入',data.capabilities[p].publish?'人工审核后执行':'未接入']));return top+`<div class="settings-grid"><section class="panel"><h2>本机运行</h2><div class="lineitem"><div>主界面<small>127.0.0.1:8020</small></div></div><div class="lineitem"><div>采集组件<small>127.0.0.1:8021 · 独立数据目录</small></div></div><div class="lineitem"><div>账号检测<small>启动检查；发布前复查；有效结果最多保留 12 小时</small></div></div></section><section class="panel"><h2>制作与发布</h2><div class="lineitem"><div>视频制作<small>FFmpeg · 原声或导入配音</small></div></div><div class="lineitem"><div>审核<small>绑定成片版本与平台稿件</small></div></div><div class="lineitem"><div>投稿回执<small>平台接收与平台审核分别确认</small></div></div></section></div>`}
function downloadJobs(failedOnly=false,activeOnly=false){return youtubeDownloadCards(failedOnly,activeOnly)}

function youtubeForm(){return youtubeDownloadForm()}
async function saveContext(){const source=data.context.source||'';await api('context','POST',{source,targets:data.context.targets});await refresh()}
document.addEventListener('change',async e=>{try{if(e.target.id==='account-platform'){accountPlatform=e.target.value;render();return}if(e.target.dataset.target){e.target.checked?data.context.targets.push(e.target.dataset.target):data.context.targets=data.context.targets.filter(id=>id!==e.target.dataset.target);await saveContext()}if(e.target.id==='source-account')await saveContext();if(e.target.dataset.select){e.target.checked?selected.add(e.target.dataset.select):selected.delete(e.target.dataset.select);render()}if(e.target.id==='select-all'){data.accounts.filter(a=>platform==='全部'||a.platform===platform).forEach(a=>e.target.checked?selected.add(a.id):selected.delete(a.id));render()}if(e.target.id==='operation-account'){opAccount=e.target.value;await loadOperations()}}catch(error){toast(error.message)}});
document.addEventListener('click',async e=>{const target=e.target.closest('[data-action]');if(!target)return;const [action,key]=target.dataset.action.split(':');e.preventDefault();try{
 if(action==='youtube-open'){modal('从 YouTube 开始',youtubeForm());return}
 if(action==='youtube-start'){target.disabled=true;try{await api('youtube','POST',{url:$('#youtube-url').value,mode:youtubeDownloadMode()});youtubeDownloadDraft.url='';close();await refresh();toast('已加入后台任务')}catch(error){$('#youtube-error').textContent=error.message}finally{target.disabled=false}return}
 if(action==='youtube-subtitles'){await api('youtube/'+key+'/subtitles','POST');await refresh();return}
 if(action==='production-settings-save'){await saveProductionSettings();return}
 if(await youtubeSettingsAction(action))return;
 if(await discoveryAction(action,key,target))return;
 if(action==='youtube-retry'){await api('youtube/'+key+'/retry','POST');await refresh();return}
 if(action==='reveal-asset'||action==='reveal-task'){if(target.disabled)return;const label=target.innerHTML;target.disabled=true;target.textContent='正在打开…';try{await api('local-files/'+(action==='reveal-asset'?'assets':'tasks')+'/'+encodeURIComponent(key)+'/reveal','POST');toast('已请求在资源管理器中定位文件')}finally{target.disabled=false;target.innerHTML=label}return}
 if(action==='close'){close();return}if(action==='menu'){$('#sidebar').classList.toggle('open');return}if(action==='refresh'){await refresh();return}
 if(action==='account-tag'){accountTag=decodeURIComponent(key||'');render();return}
 if(action==='clear-account-filter'){accountTag='';accountPlatform='';render();return}
 if(action==='tag-preset'){const input=$('#account-tags-input');const tags=parseAccountTags(input.value);const tag=decodeURIComponent(key);input.value=(tags.includes(tag)?tags.filter(t=>t!==tag):[...tags,tag]).join('，');return}
 if(action==='filter'){platform=key;selected.clear();render();return}
 if(action==='add-account'){modal('添加账号',`<label class="field"><span>平台</span><select id="new-platform">${Object.entries(labels).map(([id,l])=>`<option value="${id}">${l}</option>`).join('')}</select></label>`+field('昵称','','new-name')+field('识别备注','','new-alias')+accountTagEditor([]),button('添加','save-account',true));return}
 if(action==='save-account'){if(target.disabled)return;target.disabled=true;try{await api('accounts','POST',{platform:$('#new-platform').value,name:$('#new-name').value,alias:$('#new-alias').value,tags:parseAccountTags($('#account-tags-input').value)});close();accountPlatform='';accountTag='';location.hash='accounts/list';await refresh();toast('账号已添加，请点击登录管理连接平台')}finally{target.disabled=false}return}
 if(action==='check-all'||action==='retry-accounts'){await startAccountCheck(action==='retry-accounts');return}
 if(action==='complete-task'){modal('标记完成',`<p>归入已发布历史，保留各账号的实际回执。</p>`,button('标记完成','confirm-complete:'+key,true));return}
 if(action==='confirm-complete'){if(target.disabled)return;target.disabled=true;target.textContent='正在更新…';try{await api('tasks/'+key+'/complete','POST');close();location.hash='tasks/history/'+key;await refresh();toast('任务已完成')}finally{target.disabled=false;target.textContent='标记完成'}return}
 if(action==='task-targets'){const task=data.tasks.find(t=>t.id===key);const accepted=new Set(data.publications.filter(p=>p.task_id===key&&p.status==='succeeded').map(p=>p.account_id));modal('新增发布账号',`<div class="source-account-options">${data.accounts.filter(a=>a.platform!=='youtube'&&!accepted.has(a.id)).map(a=>`<label class="confirm"><input type="checkbox" data-task-target="${a.id}">${accountIdentity(a)}</label>`).join('')||'<p>没有可添加的账号，可先到账号首页添加。</p>'}</div>`,button('准备稿件','save-task-targets:'+key,true));return}
 if(action==='save-task-targets'){if(target.disabled)return;target.disabled=true;try{const targets=[...document.querySelectorAll('[data-task-target]:checked')].map(e=>e.dataset.taskTarget);await api('tasks/'+key+'/targets','POST',{targets});close();await refresh();openTaskDrafts(key)}finally{target.disabled=false}return}
 if(action==='choose-operation'){modal('选择运营账号',`<div class="source-account-options">${data.accounts.filter(a=>a.platform!=='youtube'&&a.platform!=='bilibili').map(a=>`<button class="btn" data-action="set-operation:${a.id}">${accountIdentity(a)}</button>`).join('')}</div>`);return}
 if(action==='set-operation'){opAccount=key;close();render();await loadOperations();return}
 if(action==='choose-source'){modal('选择国内采集账号',`<p class="mini">仅用于国内平台采集，不影响 YouTube 下载和发布目标。</p><div class="source-account-options">${data.accounts.filter(a=>(route[1]==='collect'?['douyin','xiaohongshu']:['douyin','xiaohongshu','kuaishou']).includes(a.platform)).map(a=>`<button class="btn" data-action="set-source:${a.id}">${accountIdentity(a)}${a.id===data.context.source?'<small>当前使用</small>':''}</button>`).join('')}</div>`);return}
 if(action==='set-source'){await api('context','POST',{source:key,targets:data.context.targets});close();await refresh();return}
 if(action==='delete-account'){const a=acct(key);modal('删除账号',`<p>确认删除 <strong>${accountIdentity(a)}</strong>？</p><p>仅移除本系统账号记录与当前工作账号选择，独立登录态移入本地备份，不再用于此账号。</p><p>不会注销或删除平台真实账号，也不会删除素材、作品、制作任务或投稿历史。重新添加需重新连接登录。</p>`,button('确认删除本系统账号','confirm-delete-account:'+key,true));return}
 if(action==='confirm-delete-account'){if(target.disabled)return;target.disabled=true;try{await api('accounts/'+key,'DELETE',{confirm:key});selected.delete(key);close();await refresh();toast('本系统账号已删除，作品与历史保留')}finally{target.disabled=false}return}

 if(['check-all','check-key','check','batch-check','batch-pause','batch-enable'].includes(action)){const ids=action==='check-all'?data.accounts.map(a=>a.id):action==='check-key'?data.context.targets.filter(Boolean):action==='check'?[key]:[...selected];await api('accounts/batch','POST',{ids,action:action==='batch-pause'?'pause':action==='batch-enable'?'enable':'check'});await refresh();return}
 if(action==='enter'){await api('enter','POST');location.hash='home';return}
 if(action==='batch-login'){modal('批量登录管理',[...selected].map(id=>`<div class="lineitem"><div>${accountIdentity(acct(id))}</div>${button('处理此账号','login:'+id)}</div>`).join(''));return}
 if(action==='edit-account'){const a=acct(key);modal('账号资料',field('显示昵称',a.name,'edit-name')+field('识别备注',a.alias,'edit-alias')+accountTagEditor(a.tags||[]),button('保存','save-account-info:'+key,true));return}
 if(action==='save-account-info'){await api('accounts/'+key,'PATCH',{name:$('#edit-name').value,alias:$('#edit-alias').value,tags:parseAccountTags($('#account-tags-input').value)});close();await refresh();return}
 if(action==='login'){const a=acct(key);if(a.platform==='youtube'){close();location.hash='accounts/youtube';return}modal('登录管理',`<p>${accountIdentity(a)}</p><p>${esc(a.message||'')}</p><div class="lineitem"><div>独立会话<small>${a.session_saved?'已保存，检测以平台实际结果为准':'尚未保存'}</small></div></div><label class="field"><span>会话用途</span><select id="session-purpose"><option value="publish">登录与发布会话</option><option value="read">作品读取会话</option></select></label><label class="field"><span>导入此账号会话 JSON</span><input id="session-file" type="file" accept=".json"></label><div id="login-progress"></div>`,button('导入并检查','import-session:'+key)+button('获取登录二维码','qr:'+key,true,a.platform==='bilibili'||a.platform==='youtube'?'disabled':''));return}
 if(action==='import-session'){const f=$('#session-file').files[0];if(!f)throw new Error('请选择会话文件');const fd=new FormData();fd.append('file',f);fd.append('purpose',$('#session-purpose').value);await api('accounts/'+key+'/session','POST',fd);close();await refresh();return}
 if(action==='redo-task'){const sourceButton=e.target.closest('button');if(sourceButton)sourceButton.disabled=true;const requestId=sourceButton?.dataset.redoRequest||(crypto.randomUUID());if(sourceButton)sourceButton.dataset.redoRequest=requestId;try{const t=await api('tasks/'+key+'/redo','POST',{request_id:requestId});await refresh();location.hash='tasks/detail/'+t.id;toast('已创建独立重做版本，原任务与投稿记录保留')}finally{if(sourceButton)sourceButton.disabled=false}return}
 if(action==='qr'){const job=await api('accounts/'+key+'/login','POST');clearInterval(loginTimer);const check=async()=>{const p=await api(`accounts/${key}/login/${job.id}`);$('#login-progress').innerHTML=`<p>${esc(p.message||'')}</p>${p.qrcode?`<img class="login-qr" src="${esc(p.qrcode)}" alt="登录二维码">`:''}`;if(['succeeded','failed'].includes(p.status)){clearInterval(loginTimer);await refresh(false);toast(p.message)}};await check();loginTimer=setInterval(()=>check().catch(e=>toast(e.message)),2500);return}
 if(action==='upload'){modal('导入视频',field('素材标题','','upload-title')+field('作者 / 来源署名','','upload-author')+'<label class="field"><span>视频文件</span><input id="upload-file" type="file" accept="video/*"></label>',button('导入','save-upload',true));return}
 if(action==='save-upload'){const f=$('#upload-file').files[0];if(!f)throw new Error('请选择视频');const fd=new FormData();fd.append('file',f);fd.append('title',$('#upload-title').value);fd.append('author',$('#upload-author').value);await api('assets/upload','POST',fd);close();await refresh();return}
 if(action==='cancel-task'){modal('取消制作并退回素材库','<p>取消后任务从工作台和当前任务移除。原片、已有产物与历史记录保留，可从素材库重新制作。</p>',button('确认取消制作','confirm-cancel:'+key,true));return}
 if(action==='confirm-cancel'){
  target.disabled=true;target.textContent='正在取消…';$('#dialog-error').textContent='';
  try{
   await api('tasks/'+key+'/cancel','POST',{});
   close();
   try{await refresh(false);location.hash='assets/library';render();toast('制作已取消，素材已保留')}
   catch(error){toast('任务已取消，但列表刷新失败，请点击刷新重试。')}
  }catch(error){
   $('#dialog-error').textContent=[404,405].includes(error.status)?'当前后台尚未加载取消功能。请停止并重新启动 DreamStudio，再刷新页面重试；本次尚未取消。':('取消未完成：'+error.message);
  }finally{target.disabled=false;target.textContent='确认取消制作'}
  return
 }
 if(action==='asset'){const a=data.assets.find(x=>x.id===key);modal('素材详情',`<video controls style="width:100%;max-height:250px" src="/api/media/assets/${a.id}/video"></video><h3>${esc(a.title)}</h3><p>${assetStatus(a)}</p><p>来源：${esc(a.source)}<br>作者：${esc(a.author)}<br>${esc(a.source_url)}</p>${localFilePath(a.path)}`,button('打开文件位置','reveal-asset:'+key)+button('制作成片','create-task:'+key,true));return}
 if(action==='create-task'){const t=await api('tasks','POST',{asset_id:key});await api('tasks/'+t.id+'/production','POST',{kind:'auto',version:t.version});close();await refresh(false);location.hash='tasks/detail/'+t.id;render();return}
 if(action==='save-task'){await api('tasks/'+key,'PUT',{title:$('#task-title').value,cover_title:$('#task-cover-title').value,start:Number($('#task-start').value),duration:Number($('#task-duration').value),subtitle:$('#task-subtitle').value});await refresh();toast('参数已保存，审核已重置');return}
 if(action==='voice'){modal('导入配音','<label class="field"><span>音频文件</span><input type="file" id="voice-file" accept="audio/*"></label>',button('保存配音','save-voice:'+key,true));return}
 if(action==='save-voice'){const fd=new FormData();fd.append('file',$('#voice-file').files[0]);await api('tasks/'+key+'/voice','POST',fd);close();await refresh();return}
 if(action==='render'){await api('tasks/'+key+'/render','POST');await refresh();return}
 if(action==='maximize'){const t=data.tasks.find(x=>x.id===key);modal('审片 · '+esc(t.title),`<video controls autoplay src="/api/media/${t.video_path?'tasks':'assets'}/${t.video_path?t.id:t.asset_id}/video"></video>`);$('#dialog').classList.add('media-screen');return}
 if(action==='approve'){target.disabled=true;try{const t=data.tasks.find(x=>x.id===key);await api('tasks/'+key+'/review','POST',{version:t.version,confirmed:$('#review-confirm').checked});await refresh(false);openTaskDrafts(key)}finally{target.disabled=false}return}
 if(action==='drafts'){await api('tasks/'+key+'/drafts','POST');await refresh(false);openTaskDrafts(key);return}
 if(action==='sync-draft'){await api('drafts/'+key+'/sync-version','POST');await refresh();toast('已同步成片版本，保留人工文案，请重新审核');return}
 if(action==='save-draft'){await api('drafts/'+key,'PUT',{...($('#draft-short-title')?{short_title:$('#draft-short-title').value}:{}),title:$('#draft-title').value,body:$('#draft-body').value,tags:$('#draft-tags').value.split(/[,，]/).map(v=>v.trim()).filter(Boolean),planned_at:$('#draft-time').value?new Date($('#draft-time').value).getTime()/1000:0});await refresh();if(publishSelection.has(key)){publishReview=[];await resumeBatchReview()}else toast('草稿已保存');return}
 if(action==='review-draft'){modal('稿件审核','<label class="confirm"><input type="checkbox" id="draft-confirm">已确认文案与内容性质</label><label class="confirm"><input type="checkbox" id="ai-confirm" checked>内容含 AI 辅助生成部分</label>',button('确认审核','approve-draft:'+key,true));return}
 if(action==='approve-draft'){await api('drafts/'+key+'/review','POST',{confirmed:$('#draft-confirm').checked,ai_declaration:$('#ai-confirm').checked});close();await refresh();return}
 if(action==='publish'){if(!$('#publish-confirm').checked)throw new Error('请先确认本次公开投稿');const d=data.drafts.find(x=>x.id===key);modal('确认公开投稿',`<p>${esc(d.title)}<br>${accountIdentity(acct(d.account_id))}</p><p>执行后将上传并向平台提交此稿件。</p>`,button('确认投稿','confirm-publish:'+key,true));return}
 if(action==='confirm-publish'){await api('drafts/'+key+'/publish','POST',{confirmed:true});close();await refresh(false);location.hash='publish/queue';render();return}
 if(action==='collect'){await api('collections','POST',{keywords:$('#keywords').value.split(/\n/).map(s=>s.trim()).filter(Boolean),limit:Number($('#collection-limit').value)});await refresh();return}
 if(action==='cancel-collection'){await api('collections/'+key+'/cancel','POST');close();await refresh();return}
 if(action==='collection'){const r=await api('collections/'+key);modal('采集进度',`<p>状态：${esc(r.job.status)}<br>${esc(r.job.message||r.job.error_message||'')}</p>`+table(['内容','状态'],(Array.isArray(r.contents)?r.contents:r.contents.items||[]).map(x=>[esc(x.title||x.description),esc(x.download_status||x.status||'')])),button('暂停采集','cancel-collection:'+key)+button('导入已下载素材','import-collection:'+key,true));return}
 if(action==='import-collection'){const r=await api('collections/'+key+'/import','POST');close();await refresh();toast('已导入 '+r.imported+' 条素材');return}
 if(action==='download'){const r=await api('download','POST',{url:$('#share-url').value});$('#download-result').innerHTML=`<p>已导入 ${r.imported} 条素材</p>${(r.errors||[]).map(e=>`<p class="status-error">${esc(e)}</p>`).join('')}${link('查看素材','assets/library')}`;await refresh(false);return}
 if(action==='monitor-toggle'||action==='monitor-run'){await api('monitors/'+key+'/'+(action==='monitor-toggle'?'toggle':'run-now'),'POST');await refresh();return}
 if(action==='monitor-contents'){const result=await api('monitors/'+key+'/contents');modal('监控作品',table(['内容','下载状态'],result.map(x=>[esc(x.desc),esc(x.download_status)])));return}
 if(action==='monitor'){await api('monitors','POST',{url:$('#monitor-url').value,name:$('#monitor-name').value});await refresh();return}
 if(action==='sync'){const r=await api('operations/'+$('#operation-account').value+'/sync','POST',{view:key==='messages'?'dm':key});toast(r.message||'同步已安排');await loadOperations();return}
 if(action==='publication'){const p=await api('publications/'+key);modal('执行任务',`<p>${esc(p.message)}</p>${publicationRecovery(p,key)}${p.evidence_url?`<details><summary>查看上次失败现场</summary><img style="max-width:100%" src="${esc(p.evidence_url)}" alt="平台失败现场截图"></details>`:''}${p.qrcode?`<img class="login-qr" src="${esc(p.qrcode)}" alt="账号验证二维码">`:''}${p.verification?`<div data-nonce="${esc(p.verification)}" id="verification">${field('平台验证码','','verification-code')}${button('发送验证码','send-code:'+key)}${button('提交验证码','verify-code:'+key,true)}</div>`:''}`);return}
 if(action==='send-code'||action==='verify-code'){await api('publications/'+key+'/verification','POST',{nonce:$('#verification').dataset.nonce,action:action==='send-code'?'send':'verify',code:$('#verification-code').value});toast('验证已交给原任务处理');return}
 if(action==='backup'){const r=await api('backup','POST');toast('备份已保存：'+r.file);return}
 }catch(error){if($('#dialog').open&&$('#dialog-error'))$('#dialog-error').textContent=error.message;else toast(error.message)}
});
window.addEventListener('hashchange',()=>{close();refresh().catch(e=>{render();toast(e.message)})});
refresh().catch(e=>{$('#main').innerHTML=empty('服务暂未就绪',esc(e.message));});

function platformLogo(platform){
 const files={douyin:'tiktok',bilibili:'bilibili',kuaishou:'kuaishou',xiaohongshu:'xiaohongshu',wechat_channels:'wechat-channels'};
 if(files[platform])return `<span class="platform-logo platform-${platform}" role="img" aria-label="${esc(labels[platform])}" style="--logo:url('/static/platform-logos/${files[platform]}.svg')"></span>`;
 return '<svg viewBox="0 0 24 24" aria-label="YouTube" role="img"><rect x="2" y="5" width="20" height="14" rx="5" fill="#ff0033"/><path d="M10 8l6 4-6 4z" fill="white"/></svg>';
}

function parseAccountTags(value){return [...new Set(value.split(/[,，;；\n]/).map(t=>t.trim()).filter(Boolean))]}
function accountTagEditor(tags){
 const choices=[...new Set(['主号','小号','搬运',...data.accounts.flatMap(a=>a.tags||[])])];
 return field('账号标签',tags.join('，'),'account-tags-input')+`<small>用逗号分隔；最多 8 个标签，每个最多 20 字。清空可移除全部标签。</small><div class="account-tag-presets">${choices.map(t=>button(esc(t),'tag-preset:'+encodeURIComponent(t))).join('')}</div>`;
}

function accountIdentity(a){
 if(!a)return '<span class="mini">账号已移除</span>';
 const name=titleAccount(a),url=String(a.avatar_url||'');
 return `<span class="account-identity">${platformLogo(a.platform)}<span class="account-face">${/^https:\/\//.test(url)?`<img src="${esc(url)}" alt="" referrerpolicy="no-referrer" loading="lazy">`:''}<span aria-hidden="true">${esc(Array.from(name)[0]||'人')}</span></span><span class="account-nickname">${esc(name)}</span></span>`;
}
document.addEventListener('error',e=>{if(e.target.matches?.('.account-face img'))e.target.remove()},true);

function domesticPicker(){return `<div class="source-account-picker"><span class="metriclabel">国内采集账号</span><button class="btn source-picker" data-action="choose-source">${data.context.source?accountIdentity(acct(data.context.source)):'选择国内账号'}<span>⌄</span></button><small>关键词采集支持抖音、小红书；只检查此采集账号。</small></div>`}
function youtubeConnection(){const y=data.youtube_connection||{},c=y.check||{};return `<span title="${esc(c.at?'最近检测：'+time(c.at):'YouTube 独立连接状态；不依赖国内账号')}">${pill(c.ok?'已验证':c.ok===false?'检测失败':y.session?.login_fields_present?'待检测':'未连接',c.ok?'good':'warn')}</span>`}

function localFilePath(path){return `<p class="mini" style="overflow-wrap:anywhere;user-select:text">本地文件：${esc(path||'尚未生成')}</p>`}
