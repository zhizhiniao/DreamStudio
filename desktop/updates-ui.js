const $=id=>document.getElementById(id);
const titles={unconfigured:'尚未设置更新来源',unchecked:'检查是否有新版本',checking:'正在检查更新…',current:'当前已是最新版本',available:'发现新版本',empty:'尚无可安装的正式版本',error:'暂时无法检查更新'};
function render(s){
  $('current').textContent=`造梦室 ${s.currentVersion}`;
  $('status').textContent=titles[s.status]||s.status;
  $('message').textContent=s.message||(s.available?`可更新至 ${s.latest.version}`:s.status==='unconfigured'?'发布 GitHub 首版后，在此设置仓库地址。':'');
  $('last').textContent=s.lastChecked?`上次成功检查：${new Date(s.lastChecked).toLocaleString()}`:'';
  $('automatic').checked=s.automatic;
  $('check').disabled=s.status==='checking'||!s.repository;
  $('save').disabled=s.status==='checking';
  if(document.activeElement!==$('repository'))$('repository').value=s.repository;
  for(const id of ['download','notes-link','skip'])$(id).hidden=!s.available;
  $('notes').hidden=!s.available;$('notes').textContent=s.latest?.notes||'此版本未填写发行说明。';
  if(!s.repository)$('source').open=true;
}
async function run(action){try{render(await action());}catch(e){$('message').textContent=e.message;}}
$('check').onclick=()=>run(()=>dreamRoom.check());
$('save').onclick=()=>run(()=>dreamRoom.configure($('repository').value));
$('automatic').onchange=()=>run(()=>dreamRoom.automatic($('automatic').checked));
$('skip').onclick=()=>run(()=>dreamRoom.skip());
$('download').textContent='下载并安装新版';
$('download').onclick=async()=>{ $('download').disabled=true;await run(()=>dreamRoom.install());$('download').disabled=false; };
dreamRoom.onProgress(message=>{$('message').textContent=message;});
$('notes-link').onclick=()=>run(()=>dreamRoom.open('release'));
dreamRoom.onChange(render);run(()=>dreamRoom.info());
