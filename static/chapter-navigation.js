const chapterDrafts = new Map();
const chapterStyles = [
 ['dash','A · 极简章节线','全部章名常显，细线推进'],
 ['ribbon','B · 暖色分段带','暖色色带随章节填充'],
 ['focus','C · 当前章节浮签','仅显示当前章名与序号'],
 ['off','不显示','保留原画面顶部']
];

function chapterTime(value){
 const total=Math.round(value*100);
 return Math.floor(total/6000)+':'+String(Math.floor(total/100)%60).padStart(2,'0')+
        (total%100?'.'+String(total%100).padStart(2,'0'):'');
}
function chapterText(rows){return (rows||[]).map(r=>chapterTime(r.start)+' '+r.title).join('\n')}
function chapterDraft(t){
 let d=chapterDrafts.get(t.id);
 if(!d||d.version!==t.version){
  if(d?.url)URL.revokeObjectURL(d.url);
  const config=t.chapter_navigation||{style:'off'};
  d={version:t.version,style:config.style,text:chapterText(config.chapters),time:0,busy:false,url:'',message:''};
  chapterDrafts.set(t.id,d);
 }
 return d;
}
function chaptersDirty(t){
 const d=chapterDraft(t), saved=t.chapter_navigation||{style:'off'};
 return d.style!==saved.style||(d.style!=='off'&&d.text.trim()!==chapterText(saved.chapters));
}
function chapterValues(t){
 const d=chapterDraft(t);
 const chapters=d.style==='off'?[]:d.text.trim().split(/\n/).map((line,i)=>{
  const row=line.trim().match(/^(\S+)\s+(.+)$/);
  if(!row)throw new Error('第 '+(i+1)+' 行请输入“起点 章名”，例如 0:00 开场。');
  return {start:row[1],title:row[2].trim()};
 });
 return {style:d.style,chapters,version:d.version};
}
function chapterPanel(t,locked){
 const d=chapterDraft(t);
 const cards=chapterStyles.map(([key,label,hint])=>'<label class="chapter-card"><input type="radio" name="chapter-style" value="'+key+'" '+(key===d.style?'checked':'')+'>'+
  '<span class="chapter-mark mark-'+key+'" aria-hidden="true"><i></i><i></i><i></i></span><strong>'+label+'</strong><small>'+hint+'</small></label>').join('');
 return '<section class="chapter-panel" data-chapter-task="'+esc(t.id)+'"><h3>顶部章节导航</h3><fieldset '+(locked||d.busy?'disabled':'')+'><div class="chapter-options">'+cards+'</div>'+
  (d.style==='off'?'':'<label class="chapter-lines">章节起点与标题<textarea rows="4" placeholder="0:00 开场&#10;1:25 方法演示" data-chapter-text>'+esc(d.text)+'</textarea></label>'+
  '<p class="mini">一行一章，从 0:00 起，时间相对于本次成片。</p><div class="chapter-actions"><button type="button" class="btn" data-chapter-act="suggest">AI 拟章（可能计费）</button>'+
  '<label>预览时刻（秒） <input type="number" min="0" max="'+t.duration+'" step="any" data-chapter-time value="'+esc(String(d.time))+'"></label>'+
  '<button type="button" class="btn" data-chapter-act="preview">预览实际画面</button></div>')+
  '<div class="chapter-actions"><button type="button" class="btn" data-chapter-act="save">保存章节选择</button><span role="status" class="mini" data-chapter-status>'+esc(d.message||(chaptersDirty(t)?'待保存':'已选：'+chapterStyles.find(s=>s[0]===d.style)[1]))+'</span></div></fieldset>'+
  (d.url?'<img class="chapter-preview" alt="所选时刻的顶部章节预览" src="'+esc(d.url)+'">':'')+'</section>';
}
function clearChapterPreview(d){
 if(d.url){URL.revokeObjectURL(d.url);d.url='';}
}
async function saveChapterChoice(t,explicit=false){
 const d=chapterDraft(t);
 if(d.busy)throw new Error('章节预览或草案正在处理，请稍候。');
 const config=t.chapter_navigation;
 const matchesBasis=config?.basis&&JSON.stringify(config.basis)===JSON.stringify([t.asset_id,t.mode,t.start||0,t.duration,
  (t.mode==='dub'?t.speech_review_hash:t.subtitle_review_key)||null]);
 if(!chaptersDirty(t)&&(d.style==='off'||matchesBasis))return t;
 if(config?.basis&&!matchesBasis&&!explicit&&!chaptersDirty(t))throw new Error('片段或审核内容已变化，请核对章节起点并点击“保存章节选择”。');
 const next=await api('tasks/'+t.id+'/chapters','PUT',chapterValues(t));
 const voice=taskVoiceChoices.get(t.id);
 if(voice?.version===t.version)taskVoiceChoices.set(t.id,{...voice,version:next.version});
 data.tasks=data.tasks.map(item=>item.id===t.id?next:item);
 return next;
}
document.addEventListener('change',event=>{
 const panel=event.target.closest('[data-chapter-task]');
 if(!panel||event.target.name!=='chapter-style')return;
 const d=chapterDraft(flowTask(panel.dataset.chapterTask));
 clearChapterPreview(d);d.style=event.target.value;d.message='';render();
});
document.addEventListener('input',event=>{
 const panel=event.target.closest('[data-chapter-task]');
 if(!panel)return;
 const d=chapterDraft(flowTask(panel.dataset.chapterTask));
 if(event.target.hasAttribute('data-chapter-text'))d.text=event.target.value;
 else if(event.target.hasAttribute('data-chapter-time'))d.time=event.target.value;
 else return;
 clearChapterPreview(d);d.message='';
 panel.querySelector('.chapter-preview')?.remove();
 panel.querySelector('[data-chapter-status]').textContent='修改后可重新预览或保存';
});
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-chapter-act]');
 if(!button||button.disabled)return;
 const t=flowTask(button.closest('[data-chapter-task]').dataset.chapterTask),d=chapterDraft(t);
 const action=button.dataset.chapterAct;
 if(d.busy)return;
 try{
  if(action==='save'){
   button.disabled=true;
   const next=await saveChapterChoice(t,true);chapterDraft(next).message='已保存';render();return;
  }
  const payload=action==='preview'?chapterValues(t):{version:t.version,ai_confirmed:true};
  d.busy=true;d.message=action==='preview'?'正在生成预览帧…':'正在读取字幕并拟定章节…';render();
  if(action==='suggest'){
   const result=await api('tasks/'+t.id+'/chapters/suggest','POST',payload);
   clearChapterPreview(d);d.text=chapterText(result.chapters);d.message='草案已生成，请核对起点和章名后保存。';
  }else{
   const response=await fetch('/api/tasks/'+t.id+'/chapters/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,time:d.time})});
   if(!response.ok){const error=await response.json();throw new Error(error.error||'预览失败');}
   clearChapterPreview(d);d.url=URL.createObjectURL(await response.blob());d.message='预览时刻：'+chapterTime(Number(d.time));
  }
 }catch(error){d.message=error.message;toast(error.message)}
 finally{d.busy=false;render()}
});
