'use strict';
const discoverySelections={search:sessionStorage.getItem('discovery-source-search')||'',channel:sessionStorage.getItem('discovery-source-channel')||''};
const discoveryDrafts=new Map();
function discoveryKind(){return route[1]==='monitor'?'channel':'search'}
function discoverySources(kind){return (data.youtube_discovery?.sources||[]).filter(s=>s.kind===kind&&!s.deleted_at)}
function discoverySelected(kind){const sources=discoverySources(kind);return sources.find(s=>s.id===discoverySelections[kind])||sources[0]||null}
function selectDiscovery(kind,id){discoverySelections[kind]=id;sessionStorage.setItem('discovery-source-'+kind,id);discoveryDrafts.delete(kind)}
function discoveryValues(kind){
 if(!discoveryDrafts.has(kind)){const s=discoverySelected(kind);discoveryDrafts.set(kind,{query:s?.query||'',label:s?.label===s?.query?'':s?.label||'',limit:s?.limit||10,interval_hours:s?.interval_hours||6,enabled:s?.enabled??(kind==='channel'),sort:s?.sort||'date',period:s?.period||'all',min_views:s?.min_views||0})}
 return discoveryDrafts.get(kind);
}
function discoveryItems(source){
 if(!source)return [];
 const visible=Array.isArray(source.visible_ids)?new Set(source.visible_ids):null;
 return (data.youtube_discovery?.items||[]).filter(i=>(i.source_ids||[]).includes(source.id)&&(!visible||visible.has(i.id))&&(!source.min_views||i.view_count!=null&&i.view_count>=source.min_views)).sort((a,b)=>{
  if(source.kind==='search'&&source.sort==='views')return (b.view_count||0)-(a.view_count||0);
  if(source.kind==='search'&&source.sort==='relevance')return (a.source_ranks?.[source.id]??1e6)-(b.source_ranks?.[source.id]??1e6)||b.discovered_at-a.discovered_at;
  return (b.published_at||0)-(a.published_at||0)||(a.source_ranks?.[source.id]??1e6)-(b.source_ranks?.[source.id]??1e6)||b.discovered_at-a.discovered_at;
 });
}
function discoverySelect(label,id,value,options){return `<label class="field"><span>${label}</span><select id="${id}">${options.map(([key,name])=>`<option value="${key}" ${String(value)===String(key)?'selected':''}>${name}</option>`).join('')}</select></label>`}
function discoveryVideo(i,index,source){
 const latest=source.kind==='channel'&&index===0;
 return `<article class="asset discovery-video" data-discovery-item="${esc(i.id)}"><a class="discovery-video-cover" target="_blank" rel="noopener" href="${esc(i.url)}"><img class="thumb" loading="lazy" src="${esc(i.thumbnail)}" alt="${esc(i.title)}">${latest?'<span class="discovery-latest">最近一期</span>':source.new_item_ids?.includes(i.id)?'<span class="discovery-latest">新发现</span>':''}</a><div class="body"><h3 title="${esc(i.title)}">${esc(i.title)}</h3><small>${esc(i.channel||'频道信息待更新')} · ${i.duration?Math.round(i.duration)+' 秒':'时长待更新'}</small><small>${i.view_count!=null?Number(i.view_count).toLocaleString()+' 次观看':''}${i.published_at?' · '+(i.published_at_estimated?'约 ':'')+time(i.published_at):' · 发布时间待更新'}</small>${i.description?`<p class="discovery-video-description">${esc(i.description)}</p>`:''}<div class="foot">${i.asset_id?button('已入库','asset:'+i.asset_id):i.download_status?link(i.download_status==='failed'?'处理下载':'下载进度','assets/download'):button('下载原片','discovery-download:'+i.id,true)}<a class="textlink" target="_blank" rel="noopener" href="${esc(i.url)}">原视频 ↗</a></div></div></article>`;
}
function youtubeDiscoveryPage(sub){
 const kind=sub==='monitor'?'channel':'search',sources=discoverySources(kind),selected=discoverySelected(kind),v=discoveryValues(kind),items=discoveryItems(selected),channel=kind==='channel';
 const options=field('每次发现',v.limit,'discovery-limit','number');
 const filters=channel?'':discoverySelect('排序','discovery-sort',v.sort,[['relevance','相关度'],['date','最新发布'],['views','播放量最高']])+discoverySelect('发布时间','discovery-period',v.period,[['all','不限'],['day','近 24 小时'],['week','近 7 天'],['month','近 30 天'],['year','近 1 年']])+field('最低播放量',v.min_views,'discovery-min-views','number');
 const name=s=>s.kind==='channel'?(s.label!==s.query?s.label:s.channel_info?.name||s.query.replace('https://www.youtube.com/','').replace('/videos','')):s.query;
 const canDelete=data.youtube_discovery?.features?.delete_source;
 return heading('素材库',link('YouTube 连接设置','accounts/youtube'))+tabs('assets',sub)+`<div class="discovery-workspace"><aside class="panel discovery-controls"><h2>${channel?'添加频道':'搜索视频'}</h2>${field(channel?'频道链接或 @频道名':'关键词',v.query,'discovery-query')}${channel?field('名称（可选）',v.label,'discovery-label'):''}${filters}${options}<details class="discovery-tracking" ${channel?'open':''}><summary>定时跟踪</summary>${discoverySelect('检查间隔','discovery-interval',v.interval_hours,[[1,'每小时'],[3,'每 3 小时'],[6,'每 6 小时'],[12,'每 12 小时'],[24,'每天']])}<label class="confirm"><input type="checkbox" id="discovery-enabled" ${v.enabled?'checked':''}>持续跟踪</label></details>${button(channel?'保存并检查':'搜索并保存','discovery-create:'+kind,true)}<p id="discovery-error" role="alert"></p></aside><section class="block discovery-source-list"><h2>${channel?'已跟踪 UP 主':'我的关键词'}<small>${sources.length}</small></h2><div class="scroll-region">${sources.map(s=>`<article class="discovery-source-card ${selected?.id===s.id?'selected':''}" data-discovery-source="${esc(s.id)}"><button class="discovery-source-select" data-action="discovery-filter:${s.id}" aria-pressed="${selected?.id===s.id}"><strong>${esc(name(s))}</strong><small>${channel?esc(s.channel_info?.url||s.query):({date:'最新发布',relevance:'相关度',views:'播放量最高'}[s.sort||'date'])+' · '+({all:'不限时间',day:'近24小时',week:'近7天',month:'近30天',year:'近1年'}[s.period||'all'])}</small></button><small>${['queued','running'].includes(s.status)?'正在检查…':s.status==='failed'?esc(s.message):s.last_success_at?'检查于 '+time(s.last_success_at):'尚未检查'}${s.new_count?' · 新发现 '+s.new_count+' 条':''}</small><div class="actions">${button('更新','discovery-check:'+s.id,false,['queued','running'].includes(s.status)?'disabled':'')}${button(s.enabled?'暂停':'跟踪','discovery-toggle:'+s.id)}${button('删除','discovery-delete:'+s.id,false,canDelete?'':'disabled title="后台更新后启用"')}</div></article>`).join('')||`<p class="workbench-empty">${channel?'尚未添加频道':'尚未保存关键词'}</p>`}</div></section><section class="block discovery-results"><h2><span>${selected?esc(name(selected)):channel?'选择 UP 主':'选择关键词'}</span><small>${items.length} 条视频</small></h2>${selected?`<div class="discovery-result-meta">${selected.status==='failed'?`<span class="status-warn">${esc(selected.message)}</span>`:channel&&selected.new_count?`本次新发现 ${selected.new_count} 条`:selected.last_success_at?'上次更新 '+time(selected.last_success_at):'等待检查'}</div>`:''}<div class="discovery-grid scroll-region">${items.map((i,index)=>discoveryVideo(i,index,selected)).join('')||`<div class="empty"><h2>${selected?['queued','running'].includes(selected.status)?'正在读取视频':'暂无对应视频':channel?'先添加或选择 UP 主':'先添加或选择关键词'}</h2></div>`}</div></section></div>`;
}
function captureDiscoveryForm(){
 if(!document.querySelector('.discovery-workspace'))return;
 const kind=discoveryKind(),v=discoveryValues(kind);
 for(const [id,key] of [['discovery-query','query'],['discovery-label','label'],['discovery-limit','limit'],['discovery-interval','interval_hours'],['discovery-sort','sort'],['discovery-period','period'],['discovery-min-views','min_views']]){const input=$('#'+id);if(input)v[key]=['limit','interval_hours','min_views'].includes(key)?Number(input.value):input.value}
 v.enabled=$('#discovery-enabled')?.checked||false;
}
document.addEventListener('input',e=>{if(e.target.id?.startsWith('discovery-'))captureDiscoveryForm()});
document.addEventListener('change',e=>{if(e.target.id?.startsWith('discovery-'))captureDiscoveryForm()});
async function discoveryAction(action,key,target){
 if(!action.startsWith('discovery-'))return false;
 const source=data.youtube_discovery?.sources.find(s=>s.id===key),kind=source?.kind||discoveryKind();
 if(action==='discovery-filter'){selectDiscovery(kind,key);render();return true}
 if(action==='discovery-delete'){modal(kind==='channel'?'删除频道跟踪':'删除关键词','<p>停止跟踪，保留已下载素材。</p>',button('删除','discovery-delete-confirm:'+key,true));return true}
 if(action==='discovery-delete-confirm'){target.disabled=true;try{await api('youtube/sources/'+key,'DELETE');close();selectDiscovery(kind,'');await refresh()}catch(e){$('#dialog-error').textContent=e.message}finally{target.disabled=false}return true}
 target.disabled=true;
 try{
  if(action==='discovery-create'){
   captureDiscoveryForm();const values={...discoveryValues(key),kind:key};
   const s=await api('youtube/sources','POST',values);selectDiscovery(key,s.id);
  }
  if(action==='discovery-check'){selectDiscovery(kind,key);await api('youtube/sources/'+key+'/check','POST')}
  if(action==='discovery-toggle')await api('youtube/sources/'+key,'PATCH',{enabled:!source.enabled});
  if(action==='discovery-download'){await api('youtube/discovery/'+key+'/download','POST');toast('已加入原片下载队列')}
  await refresh();
 }catch(e){const error=$('#discovery-error');if(error)error.textContent=e.message;else toast(e.message)}finally{target.disabled=false}
 return true;
}
