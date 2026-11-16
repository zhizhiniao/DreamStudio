function draftCoverUrl(id,name,revision=''){return `/api/drafts/${encodeURIComponent(id)}/covers/${name}?v=${encodeURIComponent(revision)}`}
let manualCover=null;
async function openDraftCover(id,name='portrait',returnBatch=false){
 clearTimeout(batchPollTimer);
 const draft=data.drafts.find(x=>x.id===id);
 const douyin=draft?.platform==='douyin',wide=douyin?'4:3':'16:9';
 manualCover={id,name,returnBatch,image:null,loading:0};
 modal('人工调整封面',`<p>仅修改当前账号稿件。横版 ${wide}、竖版 3:4 分别保存，发布前再次确认。</p><div class="form-actions">${button('竖版 3:4','cover-tab:portrait')}${button('横版 '+wide,'cover-tab:landscape')}</div><p><strong>${name==='portrait'?'正在编辑竖版封面':'正在编辑横版封面'}</strong></p><label class="field"><span>选择底图</span><select id="manual-cover-source"><option value="current">此稿件当前封面</option value="portrait">制作任务竖版封面</option><option value="landscape">制作任务横版封面</option></select></label><label class="field"><span>或上传自己的图片（JPG / PNG / WebP，最多 15 MB）</span><input id="manual-cover-file" type="file" accept="image/jpeg,image/png,image/webp"></label><label class="field"><span>画面适配</span><select id="manual-cover-fit"><option value="contain">完整保留图片，空白补底色</option><option value="cover">填满画面，可调整裁剪位置</option></select></label><label class="field"><span>放大</span><input id="manual-cover-zoom" type="range" min="1" max="3" step="0.01" value="1"></label><label class="field"><span>左右位置</span><input id="manual-cover-x" type="range" min="0" max="1" step="0.01" value="0.5"></label><label class="field"><span>上下位置</span><input id="manual-cover-y" type="range" min="0" max="1" step="0.01" value="0.5"></label><p id="manual-cover-status" role="status">正在读取封面…</p><canvas id="manual-cover-canvas" width="${name==='portrait'?1080:(douyin?1440:1280)}" height="${name==='portrait'?1440:(douyin?1080:720)}" style="display:block;max-width:100%;max-height:360px;margin:auto" aria-label="实际保存的封面预览"></canvas>`,button('返回（放弃未保存调整）','cover-return')+button('保存当前'+(name==='portrait'?'竖版':'横版')+'封面','cover-save',true,'disabled'));
 await loadManualCover(draftCoverUrl(id,name,draft?.updated_at||Date.now()));
}
async function loadManualCover(url){
 const state=manualCover,seq=++state.loading;state.image=null;
 const save=document.querySelector('[data-action="cover-save"]');if(save)save.disabled=true;
 try{
  const img=new Image();img.src=url;await img.decode();
  if(manualCover!==state||state.loading!==seq||!$('#manual-cover-canvas'))return;
  state.image=img;drawManualCover();$('#manual-cover-status').textContent='预览即保存效果；切换横竖版前请先保存。';save.disabled=false;
 }catch(e){if(manualCover===state&&state.loading===seq&&$('#manual-cover-status'))$('#manual-cover-status').textContent='图片读取失败，请选择其他底图或上传图片。'}
}
function drawManualCover(){
 const canvas=$('#manual-cover-canvas'),img=manualCover?.image;if(!canvas||!img)return;
 const ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height;
 const scale=($('#manual-cover-fit').value==='cover'?Math.max:Math.min)(w/img.naturalWidth,h/img.naturalHeight)*Number($('#manual-cover-zoom').value);
 const dw=img.naturalWidth*scale,dh=img.naturalHeight*scale;
 ctx.fillStyle='#211c18';ctx.fillRect(0,0,w,h);ctx.drawImage(img,(w-dw)*Number($('#manual-cover-x').value),(h-dh)*Number($('#manual-cover-y').value),dw,dh);
}
document.addEventListener('input',e=>{if(/^manual-cover-(fit|zoom|x|y)$/.test(e.target.id))drawManualCover()});
document.addEventListener('change',async e=>{
 if(e.target.id==='manual-cover-source'){
  const value=e.target.value,state=manualCover;
  await loadManualCover(draftCoverUrl(state.id,value==='current'?state.name:value,Date.now())+(value==='current'?'':'&source=task'));
 }
 if(e.target.id==='manual-cover-file'){
  const file=e.target.files[0];if(!file)return;
  if(file.size>15*1024*1024){toast('图片不能超过 15 MB');return}
  const url=URL.createObjectURL(file);await loadManualCover(url);URL.revokeObjectURL(url);
 }
});
document.addEventListener('click',async e=>{
 const el=e.target.closest('[data-action]'),action=el?.dataset.action||'';
 if(!action.startsWith('draft-cover:')&&!action.startsWith('cover-tab:')&&!['cover-return','cover-save'].includes(action))return;
 try{
  if(action.startsWith('draft-cover:')){await openDraftCover(action.split(':')[1],'portrait',!!$('#batch-confirm'));return}
  const state=manualCover;if(!state)return;
  if(action.startsWith('cover-tab:')){await openDraftCover(state.id,action.split(':')[1],state.returnBatch);return}
  if(action==='cover-return'){manualCover=null;if(state.returnBatch)await resumeBatchReview();else close();return}
  if(action==='cover-save'){
   if(!state.image)throw new Error('请先选择有效图片');el.disabled=true;
   const blob=await new Promise(resolve=>$('#manual-cover-canvas').toBlob(resolve,'image/jpeg',0.95));
   if(!blob)throw new Error('图片导出失败');
   const form=new FormData();form.append('file',blob,state.name+'.jpg');
   const saved=await api(`drafts/${state.id}/covers/${state.name}`,'POST',form);
   data.drafts=data.drafts.map(x=>x.id===state.id?saved:x);
   if(manualCover===state&&$('#manual-cover-status'))$('#manual-cover-status').textContent='已保存'+(state.name==='portrait'?'竖版':'横版')+'封面。可继续调整另一比例，或返回确认。';
  }
 }catch(error){toast(error.message)}finally{if(el)el.disabled=false}
});
