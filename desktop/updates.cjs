const fs = require('node:fs');
const path = require('node:path');
const INTERVAL = 6 * 60 * 60 * 1000;
const RETRY = 15 * 60 * 1000;
function repository(value) {
  const text = String(value || '').trim().replace(/^https:\/\/github\.com\//i, '').replace(/\/$/, '').replace(/\.git$/, '');
  if (!text) return '';
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\/[A-Za-z0-9_.-]{1,100}$/.test(text)) throw Error('请输入 GitHub 仓库地址，格式为 用户名/仓库名');
  return text;
}
function version(value) {
  const match = /^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z.-]+)?$/.exec(String(value));
  if (!match) return null;
  const core = match.slice(1, 4).map(Number), pre = match[4]?.split('.') || [];
  if (!core.every(Number.isSafeInteger) || pre.some(s => /^\d+$/.test(s) && (s.length > 1 && s[0] === '0' || !Number.isSafeInteger(Number(s))))) return null;
  return {core, pre};
}
function compare(a, b) {
  const x = version(a), y = version(b);
  if (!x || !y) throw Error('版本号应为 1.2.3');
  for (let i=0; i<3; i++) if (x.core[i] !== y.core[i]) return x.core[i] > y.core[i] ? 1 : -1;
  if (!x.pre.length || !y.pre.length) return Number(!x.pre.length) - Number(!y.pre.length);
  for (let i=0; i<Math.max(x.pre.length,y.pre.length); i++) {
    if (x.pre[i] === undefined) return -1;
    if (y.pre[i] === undefined) return 1;
    if (x.pre[i] === y.pre[i]) continue;
    const nx=/^\d+$/.test(x.pre[i]), ny=/^\d+$/.test(y.pre[i]);
    if (nx && ny) return Number(x.pre[i]) > Number(y.pre[i]) ? 1 : -1;
    if (nx !== ny) return nx ? -1 : 1;
    return x.pre[i] > y.pre[i] ? 1 : -1;
  }
  return 0;
}
function release(row, repo) {
  repo = repository(repo);
  const v = version(row?.tag_name);
  if (!repo || !v || v.pre.length || row.draft || row.prerelease || !row.published_at) return null;
  const base=`https://github.com/${repo}`, tag=String(row.tag_name), number=v.core.join('.');
  const url=`${base}/releases/tag/${encodeURIComponent(tag)}`;
  if (row.html_url !== url) return null;
  const names=['DreamStudio-Setup-x64.exe',`DreamRoom-Setup-${number}-x64.exe`,`DreamRoom-Portable-${number}-Windows-x64.zip`];
  const asset=names.map(name=>row.assets?.find(a=>a.name===name && a.state==='uploaded' && a.size>0 && a.browser_download_url===`${base}/releases/download/${encodeURIComponent(tag)}/${name}`)).find(Boolean);
  if (!asset) return null;
  return {tag, version:number, url, download:asset.browser_download_url, asset:asset.name, size:asset.size, digest:asset.digest||'', title:String(row.name||tag).slice(0,200), notes:String(row.body||'').slice(0,6000), publishedAt:String(row.published_at).slice(0,40)};
}
function restore(saved, repo) {
  if (!saved) return null;
  return release({tag_name:saved.tag, html_url:saved.url, name:saved.title, body:saved.notes, published_at:saved.publishedAt, assets:[{name:saved.asset,state:'uploaded',size:saved.size||1,digest:saved.digest,browser_download_url:saved.download}]},repo);
}
async function readJson(response) {
  const reader=response.body.getReader(), chunks=[];let size=0;
  try {while(true){const part=await reader.read();if(part.done)break;size+=part.value.length;if(size>1024*1024)throw Error('版本服务返回内容过大');chunks.push(Buffer.from(part.value));}}
  finally {await reader.cancel().catch(()=>{});}
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}
class Updater {
  constructor({directory,currentVersion,defaultRepository='',fetcher=fetch,notify=()=>{},now=Date.now}) {
    if (!version(currentVersion)) throw Error('无效的应用版本');
    this.file=path.join(directory,'updates.json');this.currentVersion=currentVersion;this.fetcher=fetcher;this.notify=notify;this.now=now;
    this.state={repository:repository(defaultRepository),automatic:true,lastAttempt:0,lastChecked:0,skippedTag:'',latest:null};
    try {const s=JSON.parse(fs.readFileSync(this.file,'utf8')), repo=repository(defaultRepository||s.repository);this.state={repository:repo,automatic:s.automatic!==false,lastAttempt:Number(s.lastAttempt)||0,lastChecked:Number(s.lastChecked)||0,skippedTag:String(s.skippedTag||'').slice(0,100),latest:restore(s.latest,repo)};}catch{}
    this.status='unchecked';this.message='';
  }
  info(){const available=!!this.state.latest&&compare(this.state.latest.version,this.currentVersion)>0;return {...this.state,currentVersion:this.currentVersion,available,shouldNotify:available&&this.state.skippedTag!==this.state.latest.tag,status:!this.state.repository?'unconfigured':this.status==='error'||this.status==='checking'?this.status:available?'available':this.status,message:this.message};}
  emit(){this.notify(this.info());}
  save(patch){this.state={...this.state,...patch};fs.mkdirSync(path.dirname(this.file),{recursive:true});fs.writeFileSync(this.file+'.tmp',JSON.stringify(this.state,null,2));fs.renameSync(this.file+'.tmp',this.file);}
  configure(repo){if(this.pending)throw Error('正在检查更新，请稍后修改更新源');this.save({repository:repository(repo),latest:null,lastAttempt:0,lastChecked:0,skippedTag:''});this.status='unchecked';this.message='';this.emit();return this.info();}
  automatic(enabled){if(typeof enabled!=='boolean')throw Error('无效的设置');this.save({automatic:enabled});this.emit();return this.info();}
  skip(){if(!this.info().available)throw Error('没有可跳过的新版');this.save({skippedTag:this.state.latest.tag});this.emit();return this.info();}
  target(kind){const latest=restore(this.state.latest,this.state.repository);if(!latest||!this.info().available||!['download','release'].includes(kind))throw Error('没有可打开的新版本');return latest[kind==='download'?'download':'url'];}
  async check(force=false){
    if(this.pending)return this.pending;
    if(!this.state.repository)return this.info();
    const now=this.now();
    if(!force&&(!this.state.automatic||(this.state.lastChecked&&now-this.state.lastChecked<INTERVAL)||(this.state.lastAttempt&&now-this.state.lastAttempt<RETRY)))return this.info();
    this.pending=this.load().finally(()=>{this.pending=null;});return this.pending;
  }
  async load(){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);this.controller=controller;
    this.status='checking';this.message='';this.emit();
    try{
      this.save({lastAttempt:this.now()});
      const response=await this.fetcher(`https://api.github.com/repos/${this.state.repository}/releases?per_page=20`,{headers:{Accept:'application/vnd.github+json','User-Agent':`DreamRoom/${this.currentVersion}`,'X-GitHub-Api-Version':'2026-03-10'},credentials:'omit',redirect:'error',signal:controller.signal});
      if(!response.ok){await response.body?.cancel();throw Error(response.status===404?'仓库或公开 Release 尚不存在；发布首版后再检查':[403,429].includes(response.status)?'GitHub 暂时限流，请稍后重试':`检查更新失败（HTTP ${response.status}）`);}
      const rows=await readJson(response);if(!Array.isArray(rows))throw Error('版本服务返回格式错误');
      const latest=rows.map(r=>release(r,this.state.repository)).filter(Boolean).sort((a,b)=>compare(b.version,a.version))[0]||null;
      this.save({latest,lastChecked:this.now()});this.status=latest?'current':'empty';
    }catch(e){this.status='error';this.message=/GitHub|HTTP|仓库|版本服务/.test(e.message)?e.message:'暂时连不上更新服务，请检查网络后重试。现有任务和账号仍可使用。';}
    finally{clearTimeout(timer);this.controller=null;}
    this.emit();return this.info();
  }
  cancel(){this.controller?.abort();}
}
module.exports={Updater,repository,version,compare,release,INTERVAL};
