'use strict';

const productionNames=['中文标题','内容简介','Tag 标签','封面排版','选图与裁剪','封面确认','字幕与讲稿','选择音色','发布待命'];
const speechDrafts=new Map();
const speechTimingReports=new Map();
const taskVoiceChoices=new Map();
let cachedTaskVoiceCatalogue=null;
let voicePreviewAudio=null;
let voicePreviewId='';
let voicePreviewGeneration=0;
const taskVoices=[["Chinese_casual_instructor_nv1","活力讲师"],["Chinese_deep_voiced_male_nv1","浑厚男声"],["Chinese (Mandarin)_Cute_Spirit","憨憨萌兽"],["Chinese (Mandarin)_Sweet_Lady","甜美女声"],["Chinese (Mandarin)_Mature_Woman","傲娇御姐"],["Chinese_patitent_teacher","温柔女教师"],["Chinese_huolishaonv","元气少女"]];
const productionModes={dub:'中文配音＋中文字幕',subtitles:'保留原声＋中文字幕',original:'保留原片'};
const flowValues=new Map();
const descriptionUndo=new Map();
let flowBusy=false;
let flowPending='';
function flowTask(id){return data.tasks.find(t=>t.id===id)}
function productionSteps(t){return t.mode!=='original'?[0,1,2,3,4,5,6,7,8]:[0,1,2,3,4,5,8]}
function flowStepKey(t){
 return (t.mode==='subtitles'?'subtitle-step-v1:':'production-step-v3:')+t.id;
}
function setFlowStep(t,step){
 sessionStorage.setItem(flowStepKey(t),String(step));
}
function flowIndex(t){
 const saved=sessionStorage.getItem(flowStepKey(t));
 if(t.mode==='subtitles'){
  if(saved!==null)return Math.max(0,Math.min(Number(saved),8));
  if(flowRendered(t)||t.workflow_stage==='subtitle_render_review')return 8;
  if(t.workflow_stage==='subtitle_packaging')return 7;
  if(t.cover_review_hash)return 6;
  return t.workflow_stage==='cover_review'?5:0;
 }
 if(t.mode!=='dub'&&(['6','7'].includes(saved)||['script','script_review','voice'].includes(t.workflow_stage)))return 8;
 if(saved!==null)return Math.max(-1,Math.min(Number(saved),8));
 const stages={cover_review:5,script:6,script_review:6,voice:7,ready_to_generate:8,generating:8,generation_failed:8,ready:8};
 return flowReady(t)?8:stages[t.workflow_stage]??(t.mode?0:-1)
}
function productionStepName(t,i){return t.mode==='subtitles'?({6:'字幕翻译与审核',7:'独立封装',8:'审片与发布'}[i]||productionNames[i]):productionNames[i]}

function flowButton(label,action,primary=false,disabled=false){return `<button class="btn ${primary?'primary':''}" data-flow="${action}" ${disabled?'disabled':''}>${label}</button>`}
function flowValuesFor(t){const draft=flowValues.get(t.id);return draft?.version===t.version?draft:{version:t.version}}
function flowCandidate(t,kind){const c=t.suggestions?.[kind];if(!c)return '';const values=Array.isArray(c.value)?c.value:[c.value];return `<div class="flow-candidates"><p class="mini">建议依据：${esc(c.basis)}${c.version!==t.version?' · 来自此前版本，请核对后采用':''}</p>${values.map((v,i)=>`<div class="flow-candidate"><div>${esc(v)}</div>${flowButton(kind==='tags'?'加入标签':'采用此候选','adopt:'+kind+':'+i)}</div>`).join('')}</div>`}
function emphasisOptions(text,selected){const lines=String(text||'').split('\n').filter(x=>x.trim());const count=Math.max(1,Math.min(3,lines.length));const active=Math.min(selected??1,count-1);return Array.from({length:count},(_,i)=>`<option value="${i}" ${i===active?'selected':''}>第 ${i+1} 行</option>`).join('')}
function flowDirty(t){return Object.entries(flowValuesFor(t)).some(([key,value])=>key!=='version'&&JSON.stringify(value)!==JSON.stringify(t[key]??(key==='cover_color'?'#E52620':key==='cover_emphasis'?Math.min(1,Math.max(0,(t.cover_title||'').split('\n').filter(x=>x.trim()).length-1)):undefined)))}
function flowRendered(t){return !flowPending&&!flowDirty(t)&&t.prepared_version===t.version&&['review','approved'].includes(t.status)&&t.production_job?.status!=='running'}
function flowReady(t){return flowRendered(t)&&t.status==='approved'&&t.reviewed_version===t.version}
function coverUrl(t,profile){return `/api/media/tasks/${t.id}/${profile}?result=${encodeURIComponent(t['cover_'+profile+'_path_hash']||t.production_job?.id||t.version)}`}
function flowStatus(t,step){const job=t.production_job||{},running=job.status==='running'||t.status==='rendering',failed=job.status==='failed'||t.status==='failed',ready=flowReady(t);if([6,7].includes(step)&&!flowPending&&!running&&!failed)return '';const message=flowPending||(running?(job.message||t.progress||'正在处理…'):flowDirty(t)?'修改尚未保存':failed?(t.mode==='subtitles'&&job.error_code==='translation_incomplete'?'字幕翻译已停止。请在下方中英对照表筛选问题字幕，修改或确认保留原文，再保存并继续。':job.message||t.error):ready?'待发布，可进入发布中心':job.status==='paused'?job.message:t.workflow_stage==='script_review'?'讲稿已整理，等待你审核':'按步骤确认后继续');return `<div class="production-status ${failed?'status-error':''}" role="status" aria-live="polite"><strong>${flowPending?'正在提交':running?'处理中':failed?'处理未完成':ready?'待发布':job.status==='paused'?'已暂停，等待人工检查':'等待确认'}</strong><span>${esc(message)}</span>${job.details?.failures?.length?`<details><summary>查看失败字幕（${job.details.failures.length} 段）</summary>${job.details.failures.map(f=>`<p>第 ${f.id+1} 段 · ${esc(f.source)}<br>${esc(f.reason)} · 已请求 ${f.attempts??0}/${job.details.max_attempts||3} 次</p>`).join('')}</details>`:''}${running&&job.percent!=null?`<progress max="100" value="${Math.min(99,job.percent)}" aria-label="当前制作进度"></progress><b>${Math.floor(job.percent)}%</b>`:''}</div>`}
function updateFlowStatus(){const panel=document.querySelector('.production');if(!panel)return;const t=flowTask(panel.dataset.task),slot=panel.querySelector('.production-status');if(slot)slot.outerHTML=flowStatus(t,flowIndex(t));const ready=flowReady(t),rendered=flowRendered(t);const review=panel.querySelector('[data-flow="review"]');if(review){review.disabled=!rendered||flowBusy;review.classList.toggle('primary',rendered)}if(!ready){panel.querySelector('.flow-review-covers')?.remove();panel.querySelector('[data-action^="drafts:"]')?.remove()}}
function productionPage(t){
 const a=data.assets.find(a=>a.id===t.asset_id)||{};const draft=flowValuesFor(t);const v={...t,...draft};const step=flowIndex(t);const job=t.production_job||{};const locked=data.publications.some(p=>p.task_id===t.id&&['queued','running','succeeded','needs_check'].includes(p.status));const busy=locked||t.status==='rendering'||job.status==='running'||flowBusy;
 const btn=(label,action,primary=false)=>flowButton(label,action,primary,busy&&!action.startsWith('step:'));
 const generate=(kind,label)=>btn(label||'重新生成候选','generate:'+kind);
 const upload=(kind,label)=>btn(label,'upload:'+kind);
 let content='';
 if(step===-1)content=`<div class="eyebrow">开始前 · 视频已下载</div><h2>这条视频，想怎样处理？</h2><p class="muted">先准备中文文案和封面，再审核讲稿、选择音色，最后生成成片。</p><div class="flow-modes">${Object.entries(productionModes).map(([m,label],i)=>`<button class="flow-mode ${v.mode===m?'selected':''}" data-flow="mode:${m}" ${busy?'disabled':''} aria-pressed="${v.mode===m}"><small>0${i+1}</small><strong>${label}</strong><span>${['翻译台词，制作中文配音与字幕。','保留台词、音乐和音效，只增加中文字幕。','保留原视频，不重编码；继续准备发布文案。'][i]}</span></button>`).join('')}</div><div class="flow-note">选好方式后继续。中文建议使用已配置的 ${esc(data.production_ai?.model||'AI')}，按服务商规则计费。</div>${btn('开始自动准备','auto',true)} `;
 if(step===0)content=`<div class="eyebrow">STEP 01 / 标题建议</div><h2>先确定一个中文标题</h2><p class="muted">根据抓取资料给出 1–3 个备选。AI 自动填入首选，你可以保留或切换备选。</p>${generate('title',t.suggestions?.title?'重新建议标题':'生成中文标题建议')}${flowCandidate(t,'title')}${field('中文标题 · 与封面共用',draft.title??v.cover_title?.replace(/\n/g,'')??v.title,'flow-title')}${field('封面栏目标签 · 1–6 字',v.cover_category??'影像创作','flow-cover_category')}<p class="mini">如果只有标题，建议会明确标记未核对全文；不会声称已经分析完整视频。</p>`;
 if(step===1)content=`<div class="eyebrow">STEP 02 / 内容简介</div><div class="description-heading"><h2>对照候选，编辑最终简介</h2><small>只有右侧内容会用于发布稿件</small></div><div class="description-columns"><section class="description-column"><header><h3>AI 生成候选</h3>${generate('description',t.suggestions?.description?'重新生成':'生成候选')}</header><p class="mini">生成候选不会覆盖右侧内容。</p>${descriptionCandidate(t)}<footer>${t.suggestions?.description?btn('采用到右侧 →','adopt:description:0'):''}</footer></section><section class="description-column description-adopted"><header><h3>${locked?'已采用 · 只读':'已采用 · 可自由编辑'}</h3><span class="mini" id="description-count">${Array.from(v.description||'').length} / 5000</span></header><p class="mini">采用后仍可修改；保存后才更新任务。</p><label class="description-editor"><span class="sr-only">已采用的内容简介</span><textarea id="flow-description" maxlength="5000" placeholder="采用左侧候选，或直接在这里填写简介">${esc(v.description||'')}</textarea></label><footer>${descriptionUndo.get(t.id)?.version===t.version?btn('撤销本次采用','undo-description'):''}<span class="mini">${flowDirty(t)?'有未保存修改':'已保存到任务'}</span></footer></section></div>`;
 if(step===2)content=`<div class="eyebrow">STEP 03 / TAG 标签</div><h2>确认内容标签</h2><p class="muted">任务标签持续保存并传入平台草稿。内容建议不等于实时热门话题。</p><p class="mini">原始标签：${esc((a.tags||[]).join('、')||'来源未提供或旧下载未保留')}</p>${generate('tags','建议相关标签')}${flowCandidate(t,'tags')}${textarea('已选标签（用逗号分隔，可修改、删除）',(v.tags||[]).join('，'),'flow-tags')}<div class="flow-note">快手最多 3 个话题；超出时在平台草稿中挑选。其他平台不统一截成 3 个。</div>`;
 if(step===3)content=`<div class="eyebrow">STEP 04 / 封面排版</div><h2>调整文字与断句</h2><div class="form-actions">${btn('保留原始封面','cover:original',v.cover_style==='original')}${btn('撕纸拼贴封面','cover:collage',v.cover_style!=='original')}</div>${v.cover_style==='original'?`<p class="muted">保留原始图片，完整效果在封面确认中查看。</p>${upload('original_cover','上传原始封面')}`:`<p class="mini">与第一步共用中文标题；这里的手动修改为最终文案。回车调整分行，不另生成标题候选。</p><div class="flow-title-editor">${textarea('中文标题 · 最多 30 字，1–3 行',draft.cover_title??v.cover_title??v.title,'flow-cover_title')}<div class="cover-type-controls"><label class="field"><span>着色行</span><select id="flow-cover_emphasis">${emphasisOptions(v.cover_title||v.title,v.cover_emphasis)}</select></label><label class="field"><span>强调色</span><div class="cover-color-control"><input type="color" data-cover-color aria-label="选择强调色" value="${esc(v.cover_color||'#E52620')}"><input id="flow-cover_color" aria-label="强调色十六进制" value="${esc(v.cover_color||'#E52620')}" maxlength="7" spellcheck="false"></div></label>${btn('恢复默认红色','color-reset')}</div></div><p class="mini">沿用得意黑与撕纸拼贴样式。下一步选择画面并裁剪。</p>`}`;
 if(step===4)content=`<div class="eyebrow">STEP 05 / 选图与裁剪</div><h2>选择拼贴画面，调整裁剪</h2>${v.cover_style==='original'?'<p class="muted">当前保留原始封面，无需选择拼贴素材，可直接生成并确认封面。</p>':framePicker(t,v,busy)}`;
 if(step===5)content=`<div class="eyebrow">STEP 06 / 封面确认</div><h2>完整检查横竖封面</h2><p>左侧可以切换横版和竖版，完整显示图片，不裁掉边缘。点击图片可放大。</p><div class="form-actions">${btn(t.cover_landscape_path?'按当前设置重新生成封面':'生成封面','auto')}</div><div class="flow-note">${t.mode==='dub'?'确认封面后，下一步整理中文字幕和配音讲稿。此时不会合成语音。':t.mode==='subtitles'?'确认封面后先翻译全文，异常句留待人工处理；审核字幕后再独立封装。':'确认封面后，保留原片生成制作版本。'}</div><h3>${esc(t.title)}</h3><p class="flow-description">${esc(t.description||'')}</p><p>${(t.tags||[]).map(x=>'#'+esc(x)).join('　')}</p>`;
 if(step===6&&t.mode!=='subtitles')content=speechEditor(t,busy,generate,upload,locked);
 if(step===7&&t.mode!=='subtitles')content=voiceEditor(t,busy);
 if(step===8){const ready=flowReady(t);content=`<div class="eyebrow">STEP ${t.mode==='dub'?'09':'07'} / 发布待命</div><h2>${ready?'待发布':busy?'正在制作，请稍候':'生成前的最后确认'}</h2><p>${ready?'成片与字幕封装完成，可进入发布中心。':busy?'可以离开本页，后台继续处理。进度完成并通过文件校验后显示待发布。':t.mode==='dub'?'请确认文案、封面和讲稿。生成时会核对原意、实测全稿，并自动修订超长段；保留音色和原片时长。':t.mode==='subtitles'?'复用已有中文字幕，缺少时翻译原字幕；保留原声和时间轴，直接生成字幕成片。':'确认文案和封面后保留原片。'}</p><div class="release-summary"><h3>${esc(t.title)}</h3><p>${esc(t.description||'')}</p><p>${(t.tags||[]).map(x=>'#'+esc(x)).join('　')}</p><p>处理方式：${productionModes[t.mode]}</p>${t.mode==='dub'?`<p>音色：${esc(taskVoices.find(x=>x[0]===t.voice_id)?.[1]||'尚未选择')}</p><p>已审核句块：${(t.speech_plan||[]).reduce((n,g)=>n+g.speech.split('\n').length,0)} 句 · 每行独立朗读</p>`:''}</div>${ready?button('进入发布中心','drafts:'+t.id,true):speechTimingFailure(t)?'<p>自动修订仍未通过的段落已集中列出，未改句子的音频会复用。</p>':''}`}
 const nav=productionSteps(t).map((i,n)=>{const label=productionStepName(t,i);return flowButton(`<b>${n+1}</b><span>${label}<small>${i===8&&t.reviewed_version===t.version?'已审核':i===step?'当前步骤':i<=(t.confirmed??-1)?'已确认':t.prepared_version===t.version?'待检查':'待准备'}</small></span>`,'step:'+i,i===step,flowBusy||!t.mode)}).join('');
 let footer=step>=0?btn('上一步','step:'+(step===8&&t.mode==='original'?5:step-1)):'';
 if(step>=0&&step<5)footer+=btn('保存当前内容','save')+btn(step===4?'保存并生成封面':'保存并查看'+productionNames[step+1],step===4?'auto':'step:'+(step+1),true);
 if(step===5)footer+=btn('确认封面，下一步','approve-cover',true);
 if(step===6&&t.mode==='dub'&&t.speech_plan?.length)footer+=flowButton(t.mode==='dub'&&!taskVoices.some(v=>v[0]===t.voice_id)?'审核通过，选择音色':'审核通过，进入生成','approve-speech',true,busy||!speechComplete(t));
 if(step===6&&t.mode==='dub'&&t.speech_plan?.length)footer+=btn('按原文重整讲稿','realign-script');
 if(step===7&&t.mode==='dub')footer+=btn('确认音色并生成','voice-finish',true);
 if(t.mode==='subtitles'){
  if(step===6)content='';
  if(step===7){const approved=subtitleView(t.id).state?.approved;
   content=`<h2>独立封装成片</h2><p>只使用已审核字幕和原视频进行本机封装。失败后只重试封装，已完成译文保持不变。</p><p data-subtitle-packaging="${t.id}">${approved?'字幕审核已确认，可以开始封装。':'请先在上一步完成全文翻译并确认字幕审核。'}</p>`;
   footer+=btn('返回字幕审核','step:6')+(flowRendered(t)&&!chaptersDirty(t)?btn('前往审片','step:8',true):flowButton('开始封装成片','finish',true,busy||!approved));
  }
  if(step===8){content=`<h2>审片与发布</h2><p>封装完成后，检查画面、字幕与声音。审片通过后进入发布中心确认投稿。</p>${flowRendered(t)?`<video controls preload="metadata" style="width:100%;max-height:50vh" src="/api/media/tasks/${t.id}/video?v=${t.version}"></video>`:'<p>当前版本尚未封装完成，请回到独立封装。</p>'}`;
   footer+=flowRendered(t)?btn(flowReady(t)?'查看已审核成片':'审核当前成片','review',true):btn('前往独立封装','step:7');
   if(flowReady(t))footer+=button('进入发布中心','drafts:'+t.id,true);
  }
 }

 if((step===7&&['subtitles','dub'].includes(t.mode))||(step===8&&t.mode==='dub'&&!flowRendered(t)))content+=chapterPanel(t,busy);
 if(step===8&&t.mode!=='subtitles'&&!flowReady(t)&&!busy&&!(t.mode==='subtitles'&&['paused','failed'].includes(t.production_job?.status))){const issue=speechTimingFailure(t);footer+=(t.mode==='dub'?btn('检查讲稿时长','inspect-timing'):'')+(issue?btn('修改第 '+issue+' 段讲稿','timing-fix:'+issue,true):btn(t.production_job?.details?.retry_requires_confirmation?'重新尝试失败字幕（最多3次）':t.production_job?.status==='failed'?'从断点继续生成':'确认并生成成片',t.production_job?.details?.retry_requires_confirmation?'retry-subtitles':'finish',true));}
 if(step===8&&!busy&&t.mode==='dub'&&speechTimingFailure(t))footer+=btn('按原文重整讲稿','realign-script');
 return heading('制作任务',(!busy||locked?button('重新制作','redo-task:'+t.id):'')+(locked?link('查看投稿记录','publish/drafts/'+t.id):button('取消制作','cancel-task:'+t.id,false,busy?'disabled':''))+link('返回列表','tasks/list'))+`<div class="production production-stage-${step} ${step===1?'production-description':''}" data-task="${t.id}"><div class="form-actions"><label>处理方式 <select id="production-mode" aria-label="处理方式" ${busy?'disabled':''}>${Object.entries(productionModes).map(([mode,label])=>`<option value="${mode}" ${t.mode===mode?'selected':''}>${label}</option>`).join('')}</select></label>${busy?'<small>处理中或已有投稿记录时不可切换</small>':''}</div><nav class="production-steps" style="grid-template-columns:repeat(${productionSteps(t).length},minmax(0,1fr))" aria-label="制作步骤">${nav}</nav><div class="production-layout">${([6,7].includes(step)||(step===8&&t.mode==='subtitles'))?'':`<aside class="production-source">${contextPanel(t,a,step,busy)}</aside>`}<section class="production-work">${locked?'<div class="flow-note">已有投稿记录，当前内容只读。</div>':flowStatus(t,step)}<div class="production-body"><fieldset class="flow-fields" ${busy&&!(step===6&&locked)?'disabled':''}>${content}</fieldset>${step===6&&t.mode==='subtitles'?subtitleEditor(t):''}</div><footer class="production-footer"><small id="flow-save-state">${step===7?'':`${flowDirty(t)||speechDirty(t)?'有未保存修改':'已保存'} · v${t.version}`}</small>${locked?'':footer}</footer></section></div></div>`;
}
function collectFlow(t){const changes={...flowValuesFor(t)};document.querySelectorAll('[id^="flow-"]').forEach(el=>{if(!el.matches('input,textarea,select'))return;const key=el.id.slice(5);changes[key]=key==='tags'?el.value.split(/[,，\n]/).map(t=>t.trim()).filter(Boolean):['start','duration','cover_emphasis'].includes(key)?Number(el.value):el.value});flowValues.set(t.id,changes);return changes}
async function persistFlow(t){const values=collectFlow(t);if(!flowDirty(t)){flowValues.delete(t.id);return t;}if(Object.hasOwn(values,'cover_title'))values.title=values.cover_title.replace(/\n/g,'').trim();else if(Object.hasOwn(values,'title')&&values.title!==t.title)values.cover_title=values.title;if(Object.keys(values).length<=1)return t;const updated=await api('tasks/'+t.id,'PUT',values);flowValues.delete(t.id);data.tasks=data.tasks.map(x=>x.id===t.id?updated:x);return updated}
document.addEventListener('input',e=>{if(e.target.closest('[data-subtitle-task],[data-chapter-task]'))return;if(e.target.name==='task-voice'){const t=flowTask(document.querySelector('.production').dataset.task);taskVoiceChoices.set(t.id,{version:t.version,id:e.target.value});return}const panel=e.target.closest('.production');if(!panel)return;if(e.target.id==='flow-cover_title'){const select=panel.querySelector('#flow-cover_emphasis');if(select)select.innerHTML=emphasisOptions(e.target.value,Number(select.value))}if(e.target.matches('[data-cover-color]'))panel.querySelector('#flow-cover_color').value=e.target.value.toUpperCase();if(e.target.id==='flow-cover_color'&&/^#[0-9a-f]{6}$/i.test(e.target.value))panel.querySelector('[data-cover-color]').value=e.target.value;collectFlow(flowTask(panel.dataset.task));const count=panel.querySelector('#description-count');if(count)count.textContent=Array.from(panel.querySelector('#flow-description')?.value||'').length+' / 5000';updateFlowStatus();const label=document.querySelector('#flow-save-state');if(label)label.textContent='有未保存修改 · 下一步会保存'});
document.addEventListener('click',async e=>{
 const buttonEl=e.target.closest('[data-flow]');if(!buttonEl||flowBusy)return;e.preventDefault();const panel=document.querySelector('.production');if(!panel)return;let t=flowTask(panel.dataset.task);const [action,value,index]=buttonEl.dataset.flow.split(':');
 try{
  if(action==='timing-fix'){setFlowStep(t,'6');sessionStorage.setItem('speech-filter:'+t.id,'all');sessionStorage.setItem('speech-page:'+t.id,String(Math.floor((Number(value)-1)/5)));render();return}
  if(action==='inspect-timing'){buttonEl.disabled=true;buttonEl.textContent='正在检查本地音频…';try{const report=await api('tasks/'+t.id+'/speech-timing');speechTimingReports.set(t.id,report);setFlowStep(t,'6');sessionStorage.setItem('speech-filter:'+t.id,report.risks?'timing':'all');sessionStorage.setItem('speech-page:'+t.id,'0');render()}finally{buttonEl.disabled=false}return}
  if(action==='color-reset'){collectFlow(t);flowValues.set(t.id,{...flowValuesFor(t),cover_color:'#E52620'});render();return}
  if(action==='mode'||action==='cover'){collectFlow(t);flowValues.set(t.id,{...flowValuesFor(t),[action==='mode'?'mode':'cover_style']:value});render();return}
  if(action==='undo-description'){collectFlow(t);const undo=descriptionUndo.get(t.id);if(undo?.version===t.version){flowValues.set(t.id,{...flowValuesFor(t),description:undo.value});descriptionUndo.delete(t.id)}render();return}
  if(action==='adopt'){const candidate=t.suggestions[value].value;const selected=Array.isArray(candidate)?candidate[Number(index)]:candidate;const field={title:'title',description:'description',tags:'tags',cover:'cover_title'}[value];const draft=collectFlow(t);if(value==='description')descriptionUndo.set(t.id,{version:t.version,value:draft.description??t.description??''});draft[field]=value==='tags'?[...new Set([...(draft.tags||t.tags||[]),selected])]:selected;flowValues.set(t.id,draft);render();return}
  if(action==='upload'){modal('导入制作文件',`<label class="field"><span>${value==='original_cover'?'原始封面图片':'UTF-8 SRT / VTT 字幕文件'}</span><input id="production-file" type="file" accept="${value==='original_cover'?'image/*':'.srt,.vtt'}"></label><p class="mini">上传后保存到此任务；当前审核会失效。</p>`,flowButton('上传并保存','file-save:'+value,true));return}
  if(document.querySelector('[data-speech-text]'))speechDrafts.set(t.id,{version:t.version,rows:collectSpeech(t)});
  if(action==='voice-finish'){const choice=document.querySelector('[name="task-voice"]:checked')?.value;if(!choice)throw new Error('请选择本任务音色');t._chosenVoice=choice;}
  collectFlow(t);flowBusy=true;buttonEl.disabled=true;flowPending=action==='auto'?'正在保存并生成横竖封面…':'正在保存并提交…';render();
  const chosenVoice=t._chosenVoice;t=await persistFlow(t);if(chosenVoice)t._chosenVoice=chosenVoice;
  if(action!=='approve-speech'&&speechDirty(t)){t=await api('tasks/'+t.id+'/speech-draft','POST',{version:t.version,rows:speechRows(t)});speechDrafts.delete(t.id);data.tasks=data.tasks.map(x=>x.id===t.id?t:x)}
  if(action==='file-save'){const f=document.querySelector('#production-file').files[0];if(!f)throw new Error('请选择文件');const form=new FormData();form.append('file',f);form.append('kind',value);form.append('version',t.version);await api('tasks/'+t.id+'/production-file','POST',form);close()}
  if(action==='mode-next'||action==='mode-ai'){if(!t.mode)throw new Error('请先选择一种处理方式');setFlowStep(t,'0')}
  if(['subtitles','dub'].includes(t.mode)&&(action==='finish'||action==='voice-finish'||(action==='step'&&flowIndex(t)===7&&value==='8'&&chaptersDirty(t)))){t=await saveChapterChoice(t);if(chosenVoice)t._chosenVoice=chosenVoice;}
  if(action==='step')setFlowStep(t,value);
  if(action==='confirm'){await api('tasks/'+t.id+'/confirm','POST',{step:Number(value),version:t.version});setFlowStep(t,Number(value)+1)}
  if(action==='review'){if(t.prepared_version!==t.version&&t.confirmed<3)throw new Error('修改已保存，请先继续自动处理以更新成片与封面');location.hash='tasks/review/'+t.id}
  if(action==='auto'){await api('tasks/'+t.id+'/production','POST',{kind:'auto',version:t.version});setFlowStep(t,'5')}
  if(action==='approve-cover'){await api('tasks/'+t.id+'/cover-review','POST',{version:t.version});setFlowStep(t,t.mode==='original'?'8':'6')}
  if(action==='script'){await api('tasks/'+t.id+'/speech-plan','POST',{version:t.version});setFlowStep(t,'6')}
  if(action==='realign-script'){await api('tasks/'+t.id+'/speech-plan','POST',{version:t.version,rebuild_alignment:true});speechDrafts.delete(t.id);speechTimingReports.delete(t.id);setFlowStep(t,'6');sessionStorage.setItem('speech-page:'+t.id,'0');sessionStorage.setItem('speech-filter:'+t.id,'all')}
  if(action==='approve-speech'){const rows=speechRows(t);t=await api('tasks/'+t.id+'/speech-review','POST',{version:t.version,rows});speechDrafts.delete(t.id);setFlowStep(t,t.mode==='dub'&&!taskVoices.some(v=>v[0]===t.voice_id)?'7':'8')}
  if(action==='voice-finish'){const id=t._chosenVoice;if(!id)throw new Error('请选择本任务音色');t=await api('tasks/'+t.id+'/voice-choice','POST',{version:t.version,voice_id:id});await api('tasks/'+t.id+'/finish','POST',{version:t.version,confirmed:true});setFlowStep(t,'8')}
  if(action==='finish'||action==='retry-subtitles'){await api('tasks/'+t.id+'/finish','POST',{version:t.version,confirmed:true,...(action==='retry-subtitles'?{reset_translation_attempts:true}:{})});setFlowStep(t,t.mode==='subtitles'?'7':'8')}

  if(action==='generate'||action==='generate-retry'||action==='mode-ai'){
   const kind=action==='mode-ai'?'title':value;
   await api('tasks/'+t.id+'/production','POST',{kind,version:t.version,ai_confirmed:true,...(action==='generate-retry'?{reset_translation_attempts:true}:{})});
  }
  if(action==='ai-start'){await api('tasks/'+t.id+'/production','POST',{kind:value,version:t.version,ai_confirmed:true});close()}
  flowBusy=false;flowPending='';await refresh();if(action==='save')toast('当前内容已保存；相关后续步骤需重新确认');if(action==='save-speech')toast('草稿已保存');
 }catch(error){flowBusy=false;flowPending='';render();toast(error.message)}
});

function productionRecovery(t){const j=t.production_job||{};return `<div class="actions">${j.error_code?.startsWith('ai_')?link('修改制作服务','settings/production'):''}${j.kind==='translate'||j.kind==='retranslate'?flowButton(j.details?.retry_requires_confirmation?'重新尝试失败字幕（最多3次）':'继续未完成字幕',(j.details?.retry_requires_confirmation?'generate-retry:':'generate:')+j.kind,false):j.kind==='auto'?flowButton('从断点继续制作','auto',false):''}${j.details?.total?`<small>已保存 ${j.details.completed}/${j.details.total} 段，继续时复用</small>`:''}</div>`}

document.addEventListener('click',e=>{
 const target=e.target.closest('[data-cover-preview]');if(!target)return;
 const task=data.tasks.find(t=>t.id===target.dataset.taskId);if(!task)return;
 const profile=target.dataset.coverPreview;if(!['landscape','portrait'].includes(profile))return;
 modal(profile==='landscape'?'横版封面 · 单独查看':'竖版封面 · 单独查看',`<img class="cover-full-preview" src="${coverUrl(task,profile)}" alt="完整封面">`);
 document.querySelector('#dialog').classList.add('media-screen');
});

function descriptionCandidate(t){const c=t.suggestions?.description;if(!c)return '<div class="description-candidate-empty">还没有 AI 候选<br><small>点击“生成候选”，也可直接编辑右侧。</small></div>';const value=Array.isArray(c.value)?c.value[0]:c.value;return `<div class="description-candidate-text" tabindex="0" aria-label="AI 生成的简介候选">${esc(value||'')}</div><p class="mini">${esc(c.basis||'根据素材资料生成')}${c.version!==t.version?' · 较早版本，请核对内容':''}</p>`}
window.addEventListener('beforeunload',e=>{if(data.tasks.some(t=>flowDirty(t)||speechDirty(t))){e.preventDefault();e.returnValue=''}});


function speechTimingFailure(t){const j=t.production_job||{};if(j.status!=='failed')return null;return j.error_code==='narration_timing'?j.details?.segments?.[0]?.id:(j.message||'').match(/第 (\d+) 段配音超时/)?.[1]}
function speechTimingRow(t,g){const report=speechTimingReports.get(t.id);const measured=report&&report.voice_id===t.voice_id?report.segments?.find(x=>x.id===g.id&&x.speech===g.speech&&x.pause_ms===g.pause_ms):null;if(measured)return measured;const old=t.speech_timing;return old&&old.voice_id===t.voice_id?old.segments?.find(x=>x.id===g.id&&x.speech===g.speech&&x.pause_ms===g.pause_ms):null}
function speechTimingMarkup(t,g){const row=speechTimingRow(t,g);if(!row)return '';const measured=row.measured!==false;return `<p class="${row.risk||row.tempo>1.08?'status-error':'mini'}">${measured?'实测配音':'估算朗读'} ${row.audio_seconds.toFixed(2)} 秒 / 可用 ${row.slot_seconds.toFixed(2)} 秒${row.risk||row.tempo>1.08?measured?' · 配音过长，请精简本段或调整停顿':' · 可能偏长，建议先检查讲稿':''}${measured?'（已清理首尾多余静音，含句间停顿）':'（仅筛查，不代表实际音色时长）'}</p>`}
function speechTimingSummary(t){const report=speechTimingReports.get(t.id);if(!report||report.version!==t.version||report.voice_id!==t.voice_id)return '';return `<div class="speech-timing-summary"><span>已实测 ${report.measured}/${report.total} 段，其余仅估算；本次检查未生成新配音。</span><button class="btn" data-speech-filter="all">全部</button><button class="btn" data-speech-filter="timing">只看时长风险（${report.risks}）</button></div>`}
function speechRows(t){const saved=speechDrafts.get(t.id);return saved?.version===t.version?saved.rows:(t.speech_plan||[])}
function speechDirty(t){return JSON.stringify(speechRows(t))!==JSON.stringify(t.speech_plan||[])}
function speechComplete(t){const rows=speechRows(t);return !!rows.length&&rows.every(g=>g.speech?.trim())&&(!t.speech_preparation?.total||rows.length===t.speech_preparation.total)}
function collectSpeech(t){return speechRows(t).map((g,i)=>({...g,speech:document.querySelector('[data-speech-text="'+i+'"]')?.value??g.speech,pause_ms:Number(document.querySelector('[data-speech-pause="'+i+'"]')?.value??g.pause_ms)}))}
function cleanSpeechCaption(text){let s=String(text).replace(/<[^>]*>|\{\\[^}]*\}/g,'').normalize('NFKC');s=s.replace(/(\d+(?:\.\d+)?)%/g,'百分之$1').replace(/(?<=\d)\.(?=\d)/g,'点').replace(/(?<![0-9A-Za-z])-(?=\d)/g,'负');for(const [a,b] of Object.entries({'+':'加','=':'等于','/':'斜杠','#':'井号','&':'与','°C':'摄氏度'}))s=s.split(a).join(b);return s.replace(/[^\p{L}\p{N}]/gu,' ').replace(/ +/g,' ').trim()}
function speechPage(t){
 const rows=speechRows(t),missing=rows.filter(g=>!g.speech?.trim()).length,onlyMissing=missing>0&&sessionStorage.getItem('speech-filter:'+t.id)==='missing';
 const timingReport=speechTimingReports.get(t.id),onlyTiming=sessionStorage.getItem('speech-filter:'+t.id)==='timing'&&timingReport?.version===t.version&&timingReport?.voice_id===t.voice_id;
 const indices=rows.map((_,i)=>i).filter(i=>(!onlyMissing||!rows[i].speech?.trim())&&(!onlyTiming||speechTimingRow(t,rows[i])?.risk)),pages=Math.max(1,Math.ceil(indices.length/5));
 const requested=Number(sessionStorage.getItem('speech-page:'+t.id)||0),page=Math.max(0,Math.min(Number.isInteger(requested)?requested:0,pages-1));
 return {rows,missing,onlyMissing,indices,pages,page,shown:indices.slice(page*5,page*5+5)};
}
function speechCaptionMarkup(g){return g.speech.split('\n').filter(x=>x.trim()).map(line=>`<p>${esc(cleanSpeechCaption(line))}</p>`).join('')||'<span class="muted">待填写</span>'}
function speechEditor(t,busy,generate,upload,locked=false){
 const view=speechPage(t),{rows,missing,onlyMissing,page,pages,indices,shown}=view,pagingBusy=busy&&!locked;
 return `<div class="speech-toolbar"><div class="speech-toolbar-info"><strong>${rows.length?`${rows.length} 段${missing?` · 待补全 ${missing}`:''}`:'暂无讲稿'}</strong><span class="mini">回车分句，字幕不带标点。</span></div><div class="form-actions">${!rows.length||missing?flowButton(rows.length||t.production_job?.status==='failed'?'继续未完成部分':'整理讲稿','script',true,busy):''}${rows.length?(t.mode==='dub'?flowButton('检查讲稿时长','inspect-timing',false,busy||speechDirty(t)):'')+flowButton('保存草稿','save-speech',false,busy):''}${upload('translated_subtitles','导入 SRT')}</div>${missing?`<div class="speech-filters"><button class="btn ${!onlyMissing?'primary':''}" data-speech-filter="all">全部</button><button class="btn ${onlyMissing?'primary':''}" data-speech-filter="missing">只看待补全（${missing}）</button></div>`:''}</div>
 ${speechTimingSummary(t)}<div class="speech-list">${shown.map(i=>{const g=rows[i];return `<section class="speech-row"><header><strong>第 ${i+1} 段</strong><small>${g.start.toFixed(1)}–${g.end.toFixed(1)} 秒${g.speech?.trim()?'':' · 待补全'}</small></header>${speechTimingMarkup(t,g)}<textarea data-speech-text="${i}" aria-label="第 ${i+1} 段配音讲稿" ${locked?'disabled':''} rows="3" placeholder="填写讲稿">${esc(g.speech)}</textarea><details class="speech-reference"><summary>对照</summary><div class="speech-reference-columns"><div><small>原文</small><p>${esc(g.source||'未提供')}</p><small>参考译文</small><p>${esc(g.translation||'未提供')}</p></div><div><small>字幕预览</small><div data-speech-caption="${i}">${speechCaptionMarkup(g)}</div></div></div><label class="speech-pause"><span>停顿</span><input type="number" min="80" max="1200" step="20" data-speech-pause="${i}" aria-label="第 ${i+1} 段停顿" ${locked?'disabled':''} value="${g.pause_ms}"><span>毫秒</span></label></details></section>`}).join('')}</div>
 ${rows.length?`<nav class="speech-pager" aria-label="讲稿分页"><button class="btn" data-speech-page="${page-1}" ${pagingBusy||page===0?'disabled':''}>上一页</button><button class="btn" data-speech-page="${page+1}" ${pagingBusy||page===pages-1?'disabled':''}>下一页</button><label><span class="sr-only">跳到页</span><select data-speech-page-select aria-label="跳到页">${Array.from({length:pages},(_,i)=>`<option value="${i}" ${i===page?'selected':''}>第 ${i+1} / ${pages} 页</option>`).join('')}</select></label><small>${page*5+1}–${Math.min((page+1)*5,indices.length)} / ${indices.length} 段</small></nav>`:''}`;
}
function voiceCatalogue(){return data.narration_voices||taskVoices.map(([id,label])=>({id,label}))}
async function attachVoiceCatalogue(){
 if(data.narration_voices)return;
 try{
  if(!cachedTaskVoiceCatalogue){
   const response=await fetch('/static/voice-catalogue.json?v=20261017-voice-preview4');
   if(!response.ok)return;
   const catalogue=await response.json(),allowed=new Set(taskVoices.map(v=>v[0]));
   const voices=catalogue.voices?.filter(v=>allowed.has(v.id));
   if(voices?.length===7&&new Set(voices.map(v=>v.id)).size===7)cachedTaskVoiceCatalogue=voices;
  }
  if(cachedTaskVoiceCatalogue)data.narration_voices=cachedTaskVoiceCatalogue;
 }catch(error){/* Keep the rest of the workspace usable if static samples are unavailable. */}
}
function voiceEditor(t,busy){
 const saved=taskVoiceChoices.get(t.id),selected=saved?.version===t.version?saved.id:t.voice_id;
 return `<div class="voice-grid">${voiceCatalogue().map(v=>`<div class="voice-option"><label class="voice-card"><input type="radio" name="task-voice" value="${esc(v.id)}" ${selected===v.id?'checked':''}><strong>${esc(v.label)}</strong></label><button class="btn voice-listen" data-voice-preview="${esc(v.id)}" aria-label="试听 ${esc(v.label)}" aria-pressed="false" ${busy||!v.preview_url?'disabled':''}>试听</button></div>`).join('')}</div><audio id="voice-preview-player" preload="none" hidden></audio>`;
}
function updateVoicePreviewButtons(loading=false){
 document.querySelectorAll('[data-voice-preview]').forEach(button=>{const active=button.dataset.voicePreview===voicePreviewId;button.textContent=active?(loading?'加载中…':'停止'):'试听';button.setAttribute('aria-pressed',String(active))});
}
function stopVoicePreview(){
 const audio=voicePreviewAudio;voicePreviewAudio=null;voicePreviewId='';voicePreviewGeneration++;
 if(audio){audio.pause();audio.removeAttribute('src');audio.load();updateVoicePreviewButtons()}
}
async function playVoicePreview(id){
 if(voicePreviewId===id){stopVoicePreview();return}
 stopVoicePreview();
 const voice=voiceCatalogue().find(v=>v.id===id),audio=document.querySelector('#voice-preview-player');
 if(!voice?.preview_url||!audio)return;
 const generation=voicePreviewGeneration;
 voicePreviewAudio=audio;voicePreviewId=id;audio.src=voice.preview_url;
 audio.onended=()=>{if(voicePreviewGeneration===generation)stopVoicePreview()};
 audio.onerror=()=>{if(voicePreviewGeneration===generation){stopVoicePreview();toast('试听加载失败，请重试')}};
 updateVoicePreviewButtons(true);
 try{await audio.play();if(voicePreviewGeneration===generation)updateVoicePreviewButtons()}
 catch(error){if(voicePreviewGeneration===generation){stopVoicePreview();toast('试听加载失败，请重试')}}
}
document.addEventListener('click',e=>{const button=e.target.closest('[data-voice-preview]');if(!button||button.disabled)return;e.preventDefault();playVoicePreview(button.dataset.voicePreview)});
window.addEventListener('hashchange',stopVoicePreview);

function contextPanel(t,a,step,busy){
 if(step===6)return '';
 if(step===7)return '';
 if(step===8)return flowReady(t)?`<small>已完成成片</small><video controls preload="metadata" src="/api/media/tasks/${t.id}/video?v=${t.version}"></video><p class="ready-label">待发布</p><p class="mini">如需检查可直接播放；发布仍由你确认。</p>`:`<small>发布待命</small><h3>${t.production_job?.status==='running'?'正在生成成片':'等待生成'}</h3><p>生成与封装在后台执行，关闭页面不会取消。</p><p class="mini">只有文件生成并校验完成，才显示待发布。</p>`;
 if(step>=3){const profile=sessionStorage.getItem('cover-profile:'+t.id)||'landscape',has=t.cover_landscape_path;return `<small>封面预览 · ${profile==='landscape'?'横版':'竖版'}</small><div class="form-actions"><button class="btn" data-context-cover="landscape">横版</button><button class="btn" data-context-cover="portrait">竖版</button></div>${has?`<button class="context-cover cover-enlarge" data-cover-preview="${profile}" data-task-id="${t.id}" aria-label="放大封面"><img src="${coverUrl(t,profile)}" alt="完整${profile==='landscape'?'横版':'竖版'}封面"></button>`:'<p class="muted">尚未生成封面。完成排版和选图后生成。</p>'}<h3>${esc(t.cover_title||t.title)}</h3><p class="mini">图片完整显示；点击可放大查看。</p>`}
 return `<small>原始${step===1?'简介':step===2?'标签':'标题'}</small><h3>${esc(step===2?(a.tags||[]).join('、'):a.title||t.title)}</h3>${step===1?`<div class="source-copy">${esc(a.description||'未提供原始简介')}</div>`:''}<p class="mini">${Math.round(a.duration||0)} 秒 · 原始来源保留</p><details><summary>查看原视频与来源</summary><video controls preload="none" src="/api/media/assets/${t.asset_id}/video"></video><p class="mini">${esc(a.source_url||'本机素材')}</p></details><button class="btn" data-flow="step:-1">修改处理方式</button>`;
}
document.addEventListener('input',e=>{
 if(!e.target.matches('[data-speech-text],[data-speech-pause]'))return;
 const t=flowTask(document.querySelector('.production').dataset.task);speechDrafts.set(t.id,{version:t.version,rows:collectSpeech(t)});
 const index=e.target.dataset.speechText??e.target.dataset.speechPause,caption=document.querySelector('[data-speech-caption="'+index+'"]');if(caption)caption.innerHTML=speechCaptionMarkup(speechRows(t)[Number(index)]);
 const approve=document.querySelector('[data-flow="approve-speech"]');if(approve)approve.disabled=!speechComplete(t);
 const label=document.querySelector('#flow-save-state');if(label)label.textContent='未保存';
});
function changeSpeechPage(t,page){speechDrafts.set(t.id,{version:t.version,rows:collectSpeech(t)});sessionStorage.setItem('speech-page:'+t.id,String(page));render()}
document.addEventListener('change',e=>{if(!e.target.matches('[data-speech-page-select]'))return;const t=flowTask(document.querySelector('.production').dataset.task);changeSpeechPage(t,Number(e.target.value))});
document.addEventListener('click',e=>{
 const filter=e.target.closest('[data-speech-filter]'),page=e.target.closest('[data-speech-page]');
 if(filter||page){const t=flowTask(document.querySelector('.production').dataset.task);if(filter)sessionStorage.setItem('speech-filter:'+t.id,filter.dataset.speechFilter);changeSpeechPage(t,filter?0:Number(page.dataset.speechPage));return}
 const profile=e.target.closest('[data-context-cover]');if(profile){const t=flowTask(document.querySelector('.production').dataset.task);collectFlow(t);sessionStorage.setItem('cover-profile:'+t.id,profile.dataset.contextCover);render()}
});


document.addEventListener('change',async e=>{
 if(e.target.id!=='production-mode'||flowBusy)return;
 let t=flowTask(document.querySelector('.production').dataset.task);const mode=e.target.value;
 if(mode===t.mode)return;
 const draft=collectFlow(t);flowBusy=true;e.target.disabled=true;
 try{
  if(speechDirty(t)){t=await api('tasks/'+t.id+'/speech-draft','POST',{version:t.version,rows:speechRows(t)});speechDrafts.delete(t.id);}
  flowValues.set(t.id,{...draft,version:t.version,mode});t=await persistFlow(t);
  speechTimingReports.delete(t.id);setFlowStep(t,t.cover_review_hash?(mode==='original'?'8':'6'):'0');
  flowBusy=false;await refresh();toast('处理方式已更换，文案、封面和音色保留');
 }catch(error){flowBusy=false;await refresh();toast(error.message);}
});
