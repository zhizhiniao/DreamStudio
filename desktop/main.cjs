const {app,BrowserWindow,Menu,Tray,Notification,dialog,shell,ipcMain,nativeImage,net}=require('electron');
const fs=require('node:fs'),path=require('node:path'),{execFile}=require('node:child_process'),{pathToFileURL}=require('node:url');
const {Updater}=require('./updates.cjs');
const pkg=require('./package.json');
const ROOT=path.resolve(__dirname,'..');
let deployment={mode:'local',backendRoot:ROOT};
try{deployment=JSON.parse(fs.readFileSync(path.join(__dirname,'deployment.json'),'utf8'));}catch{}
const studio=deployment.engine==='dreamstudio';
const localData=process.env.LOCALAPPDATA||app.getPath('appData');
const engine=path.resolve(__dirname,deployment.backendRoot||'../engine');
const prepared=studio&&deployment.mode==='installed'?require('./bootstrap.cjs').prepareWorkspace(engine,process.env.DREAMROOM_HOME||path.join(localData,'DreamRoom')):null;
const backend=prepared?.backend||engine;
const runtime=prepared?.runtime||deployment.runtimeRoot||path.join(backend,'runtime');
const profile=process.env.DREAMROOM_PROFILE||path.join(localData,'DreamRoom','desktop');
app.setPath('userData',profile);app.setName('造梦室');app.setAppUserModelId('com.jiangajiang.dreamroom');
const port=Number(process.env.DREAM_ROOM_PORT||(studio?8020:5000));
if(!Number.isInteger(port)||port<1024||port>65534)throw Error('无效的本机端口');
const origin=`http://127.0.0.1:${port}`;
const data=studio?path.join(backend,'data'):process.env.DREAM_ROOM_DATA_DIR||(deployment.mode==='local'?path.join(backend,'app'):path.join(localData,'DreamRoom','data'));
const control=process.env.DREAM_ROOM_CONTROL_DIR||(deployment.mode==='local'?path.join(backend,'runtime'):path.join(localData,'DreamRoom','service'));
const python=prepared?.python||deployment.python||path.join(runtime,'python/cpython-3.11.13-windows-x86_64-none/python.exe');
const env={...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8',PYTHONPATH:[backend,path.join(backend,'tools'),path.join(runtime,'env/Lib/site-packages'),path.join(runtime,'packages')].join(path.delimiter),DREAM_ROOM_PORT:String(port),DREAM_ROOM_DATA_DIR:data,DREAM_ROOM_CONTROL_DIR:control};
if(studio){delete env.DREAM_ROOM_DATA_DIR;delete env.DREAM_ROOM_CONTROL_DIR;}
delete env.ELECTRON_RUN_AS_NODE;
let mainWindow,updateWindow,tray,updater,owned=false,quitting=false,timer,firstCheck,lastNotified='';
function launcher(action){return new Promise((resolve,reject)=>execFile(python,[path.join(backend,'tools/launcher.py'),action,'--no-browser',...(!studio&&process.argv.includes('--safe')?['--safe']:[])],{cwd:backend,env,windowsHide:true,timeout:action==='start'?120000:40000,maxBuffer:64*1024,encoding:'utf8'},(error,out)=>error?reject(Error(action==='start'?'本机服务启动失败，请查看运行日志。':'本机服务操作未完成。')):resolve(out.trim())));}
function safeLocal(url){try{return new URL(url).origin===origin;}catch{return false;}}
function external(url){try{if(['https:','http:'].includes(new URL(url).protocol))return shell.openExternal(url);}catch{}return Promise.resolve();}
function secure(win,localFile){
  win.webContents.setWindowOpenHandler(({url})=>{
    if(safeLocal(url)){const child=new BrowserWindow({width:1100,height:800,parent:mainWindow,icon:path.join(__dirname,'brand/dreamroom.ico'),webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true}});secure(child);child.loadURL(url);}
    else external(url);return {action:'deny'};
  });
  win.webContents.on('will-navigate',(event,url)=>{if(!safeLocal(url)&&url!==localFile){event.preventDefault();external(url);}});
  win.webContents.session.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
  win.webContents.on('page-title-updated',event=>event.preventDefault());
}
function menu(){
  const available=updater?.info().available;
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {label:'造梦室',submenu:[{label:`关于造梦室 ${pkg.version}`,click:()=>dialog.showMessageBox(mainWindow,{type:'info',title:'关于造梦室',message:`造梦室 ${pkg.version}`,detail:'本机桌面版\n基于 Y2A-Auto · GPL-3.0-or-later'})},{type:'separator'},{label:available?'发现新版本…':'检查更新…',click:showUpdates},{label:'自动检查更新',type:'checkbox',checked:updater?.info().automatic!==false,click:item=>updater.automatic(item.checked)},{type:'separator'},{label:'打开数据目录',click:()=>shell.openPath(data)},{label:'退出造梦室',click:()=>app.quit()}]},
    {label:'编辑',submenu:[{role:'undo'},{role:'redo'},{type:'separator'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},
    {label:'视图',submenu:[{label:'返回首页',click:()=>mainWindow.loadURL(origin)},{role:'reload'},{role:'resetZoom'},{role:'zoomIn'},{role:'zoomOut'},{role:'togglefullscreen'}]}
  ]));
  tray?.setContextMenu(Menu.buildFromTemplate([{label:'打开造梦室',click:showMain},{label:available?'发现新版本…':'检查更新…',click:showUpdates},{type:'separator'},{label:'退出造梦室',click:()=>app.quit()}]));
}
function showMain(){if(mainWindow&&!mainWindow.isDestroyed()){if(mainWindow.isMinimized())mainWindow.restore();mainWindow.show();mainWindow.focus();}}
function showUpdates(){
  if(updateWindow&&!updateWindow.isDestroyed()){updateWindow.show();updateWindow.focus();return;}
  updateWindow=new BrowserWindow({width:690,height:740,minWidth:420,minHeight:550,title:'造梦室 · 软件更新',parent:mainWindow,icon:path.join(__dirname,'brand/dreamroom.ico'),backgroundColor:'#171819',webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true}});
  updateWindow.setMenu(null);secure(updateWindow,pathToFileURL(path.join(__dirname,'updates.html')).href);
  updateWindow.on('closed',()=>{updateWindow=null;});updateWindow.loadFile(path.join(__dirname,'updates.html'));
}
function changed(info){
  if(updateWindow&&!updateWindow.isDestroyed())updateWindow.webContents.send('updates:changed',info);
  menu();
  if(info.status==='available'&&info.shouldNotify&&info.latest.tag!==lastNotified){
    lastNotified=info.latest.tag;
    if(Notification.isSupported()){const notice=new Notification({title:'造梦室有新版本',body:`${info.currentVersion} → ${info.latest.version}，点击查看更新。`,icon:path.join(__dirname,'brand/dreamroom-128.png')});notice.on('click',showUpdates);notice.show();}
    else showUpdates();
  }
}
function ipc(){
  function handle(channel,fn){ipcMain.handle(channel,(event,...args)=>{if(!updateWindow||event.sender!==updateWindow.webContents||event.senderFrame!==event.sender.mainFrame||event.senderFrame.url!==pathToFileURL(path.join(__dirname,'updates.html')).href)throw Error('无效的更新窗口');return fn(...args);});}
  handle('updates:info',()=>updater.info());handle('updates:check',()=>updater.check(true));
  handle('updates:configure',value=>{if(typeof value!=='string'||value.length>220)throw Error('无效仓库地址');return updater.configure(value);});
  handle('updates:automatic',enabled=>updater.automatic(enabled));handle('updates:skip',()=>updater.skip());
  handle('updates:open',async kind=>{await shell.openExternal(updater.target(kind));return updater.info();});
  handle('updates:install',async()=>{
    const installer=await require('./download.cjs').downloadInstaller(updater.info().latest,path.join(profile,'downloads'),(...args)=>net.fetch(...args),message=>{if(updateWindow&&!updateWindow.isDestroyed())updateWindow.webContents.send('updates:progress',message);});
    const answer=await dialog.showMessageBox(updateWindow,{type:'question',title:'安装新版',message:'新版已下载并校验。现在退出造梦室并安装？',detail:'安装文件与账号、任务、素材分开保存。正在进行的后台任务会停止。',buttons:['退出并安装','稍后'],defaultId:0,cancelId:1});
    if(answer.response===0){await launcher('stop');const child=require('node:child_process').spawn(installer,['/SP-','/SILENT','/NORESTART'],{detached:true,stdio:'ignore',windowsHide:true});child.unref();app.quit();}
    return updater.info();
  });
}
async function boot(){
  fs.mkdirSync(profile,{recursive:true});fs.mkdirSync(data,{recursive:true});
  mainWindow=new BrowserWindow({width:1440,height:980,minWidth:980,minHeight:680,title:'造梦室',backgroundColor:'#171819',icon:path.join(__dirname,'brand/dreamroom.ico'),webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true}});
  mainWindow.on('close',event=>{if(!quitting){event.preventDefault();mainWindow.hide();}});
  secure(mainWindow,pathToFileURL(path.join(__dirname,'startup.html')).href);await mainWindow.loadFile(path.join(__dirname,'startup.html'));
  tray=new Tray(nativeImage.createFromPath(path.join(__dirname,'brand/dreamroom-128.png')).resize({width:32,height:32}));tray.setToolTip('造梦室');tray.on('double-click',showMain);
  const config=require('./release-config.json');updater=new Updater({directory:profile,currentVersion:pkg.version,defaultRepository:config.repository,fetcher:(...args)=>net.fetch(...args),notify:changed});ipc();menu();
  try{
    const state=JSON.parse(await launcher('status'));owned=!state.running;
    await launcher('start');await mainWindow.loadURL(origin);
    firstCheck=setTimeout(()=>updater.check().catch(()=>{}),5000);timer=setInterval(()=>updater.check().catch(()=>{}),60*60*1000);timer.unref();
  }catch(error){await dialog.showMessageBox(mainWindow,{type:'error',title:'造梦室启动失败',message:error.message,detail:'请检查本机 5000/5001 端口，以及数据目录的 logs 和 service 日志。'});app.quit();}
}
if(!app.requestSingleInstanceLock())app.quit();
else{
  app.on('second-instance',showMain);app.on('activate',showMain);
  app.on('before-quit',event=>{if(quitting)return;event.preventDefault();quitting=true;updater?.cancel();clearInterval(timer);clearTimeout(firstCheck);(owned?launcher('stop'):Promise.resolve()).catch(()=>{}).finally(()=>{tray?.destroy();app.quit();});});
  app.whenReady().then(boot).catch(error=>{dialog.showErrorBox('造梦室',error.message);app.quit();});
}
