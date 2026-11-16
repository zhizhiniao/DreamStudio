'use strict';
let cropFrame=null;
function frameName(f){return f.kind==='original-cover'?'原始封面':f.seconds.toFixed(1)+' 秒'}
function framePicker(t,v,busy){
 const rows=t.cover_frames||[], selected=v.cover_selection||[],crops=v.cover_crops||{};
 return `<section class="frame-picker"><h3>原始封面与视频画面 · 多选拼贴</h3><p class="mini">保留原始封面供你选择，可与视频截图一起拼贴，最多选 10 张。点击裁切可拖动框选；原始帧始终保留。已选 ${selected.length} 张。</p>${flowButton(rows.length?'检查 / 补齐候选帧':'提取差异帧','generate:frames',false,busy)}${rows.length?`<div class="frame-grid">${rows.map(f=>`<article class="frame-card ${selected.includes(f.id)?'selected':''}"><label><input type="checkbox" data-frame-select="${f.id}" ${selected.includes(f.id)?'checked':''} ${busy?'disabled':''}><span>${frameName(f)}${crops[f.id]?' · 已裁切':''}</span><img src="/api/tasks/${t.id}/cover-frames/${f.id}" alt="${frameName(f)}"></label><button class="btn" data-frame-crop="${f.id}" ${busy?'disabled':''}>裁切此帧</button></article>`).join('')}</div><p class="mini">${rows.length} 个可选素材。画面相似的视频可能较少；不会用重复帧凑数。</p>`:''}</section>`;
}
function cropBox(){return ['left','top','right','bottom'].map(k=>Number(document.querySelector('#crop-'+k).value)/100)}
function drawCrop(){
 const box=cropBox(), overlay=document.querySelector('#crop-box'),image=document.querySelector('#crop-source'),canvas=document.querySelector('#crop-preview');
 if(!image||!image.naturalWidth)return;
 const [x,y,r,b]=box;if(!(x>=0&&y>=0&&r<=1&&b<=1&&r>x&&b>y))return;
 overlay.style.cssText=`left:${x*100}%;top:${y*100}%;width:${(r-x)*100}%;height:${(b-y)*100}%`;
 canvas.width=320;canvas.height=Math.max(1,Math.round(320*(b-y)*image.naturalHeight/((r-x)*image.naturalWidth)));
 canvas.getContext('2d').drawImage(image,x*image.naturalWidth,y*image.naturalHeight,(r-x)*image.naturalWidth,(b-y)*image.naturalHeight,0,0,canvas.width,canvas.height);
}
document.addEventListener('change',e=>{
 if(!e.target.dataset.frameSelect)return;
 const t=flowTask(document.querySelector('.production').dataset.task),v=collectFlow(t),ids=[...(v.cover_selection||t.cover_selection||[])],id=e.target.dataset.frameSelect;
 if(e.target.checked&&ids.length>=10){e.target.checked=false;toast('最多勾选 10 张画面');return}
 v.cover_selection=e.target.checked?[...ids,id]:ids.filter(x=>x!==id);flowValues.set(t.id,v);const scroll=document.querySelector('.production-body').scrollTop;render();document.querySelector('.production-body').scrollTop=scroll;
});
document.addEventListener('input',e=>{if(e.target.id.startsWith('crop-'))drawCrop()});
document.addEventListener('click',e=>{
 const crop=e.target.closest('[data-frame-crop]');
 if(crop){
  const t=flowTask(document.querySelector('.production').dataset.task),v=collectFlow(t);cropFrame={task:t.id,id:crop.dataset.frameCrop,scroll:document.querySelector('.production-body').scrollTop};
  const box=(v.cover_crops||t.cover_crops||{})[cropFrame.id]||[0,0,1,1];
  modal('裁切视频帧',`<p class="mini">拖动框选保留区域，或调整下方百分比。原图不会被修改。</p><div class="crop-stage" id="crop-stage"><img id="crop-source" draggable="false" src="/api/tasks/${t.id}/cover-frames/${cropFrame.id}" alt="待裁切原始帧"><div id="crop-box"></div></div><div class="crop-controls">${['left','top','right','bottom'].map((k,i)=>field(['左边界 %','上边界 %','右边界 %','下边界 %'][i],Math.round(box[i]*100),'crop-'+k,'number')).join('')}</div><p class="mini">裁切预览</p><canvas id="crop-preview"></canvas>`,`<button class="btn" data-crop-reset>恢复完整画面</button><button class="btn primary" data-crop-save>应用裁切</button>`);
  const img=document.querySelector('#crop-source');img.onload=drawCrop;if(img.complete)drawCrop();
  const stage=document.querySelector('#crop-stage');let start=null;
  const position=event=>{const r=stage.getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))]};
  stage.onpointerdown=event=>{start=position(event);stage.setPointerCapture(event.pointerId);event.preventDefault()};
  stage.onpointermove=event=>{if(!start)return;const end=position(event),b=[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.max(start[0],end[0]),Math.max(start[1],end[1])];['left','top','right','bottom'].forEach((k,i)=>document.querySelector('#crop-'+k).value=Math.round(b[i]*100));drawCrop()};
  stage.onpointerup=()=>{start=null};stage.onpointercancel=()=>{start=null};return;
 }
 if(e.target.closest('[data-crop-reset]')){['left','top','right','bottom'].forEach((k,i)=>document.querySelector('#crop-'+k).value=i<2?0:100);drawCrop()}
 if(e.target.closest('[data-crop-save]')){
  const box=cropBox(),[x,y,r,b]=box;
  if(!box.every(Number.isFinite)||x<0||y<0||r>1||b>1||r-x<.01||b-y<.01){toast('请框选有效区域，宽高至少保留 1%');return}
  const t=flowTask(cropFrame.task),v=flowValuesFor(t);v.cover_crops={...(v.cover_crops||t.cover_crops||{}),[cropFrame.id]:box};flowValues.set(t.id,v);close();render();document.querySelector('.production-body').scrollTop=cropFrame.scroll;toast('裁剪已暂存；下一步保存，在生成与审片中查看完整封面');
 }
});
