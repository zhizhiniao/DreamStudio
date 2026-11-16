const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),pkg=require('./package.json'),out=path.join(root,'desktop-output',`DreamStudio-${pkg.version}`),engine=path.join(out,'resources/engine'),appDir=path.join(out,'resources/app');
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const skip=new Set(['__pycache__','.git','.temp','node_modules','tests','test']);
function copyTree(from,to,{link=false,filter=()=>true}={}){
 fs.mkdirSync(to,{recursive:true});for(const d of fs.readdirSync(from,{withFileTypes:true})){if(skip.has(d.name))continue;const a=path.join(from,d.name),b=path.join(to,d.name);if(!filter(a,d))continue;if(d.isSymbolicLink())throw Error('不打包符号链接 '+a);if(d.isDirectory())copyTree(a,b,{link,filter});else if(!/\.pyc$/.test(d.name)){if(link&&!/\.(cfg|pth)$/i.test(d.name))fs.linkSync(a,b);else fs.copyFileSync(a,b);}}
}
function files(folder,prefix=''){let rows=[];for(const d of fs.readdirSync(folder,{withFileTypes:true})){const rel=prefix+d.name;if(d.isDirectory())rows.push(...files(path.join(folder,d.name),rel+'/'));else rows.push({path:rel,sha256:hash(path.join(folder,d.name))});}return rows;}
async function build(){
 if(process.argv.includes('--resume'))return finish();
 if(fs.existsSync(out))throw Error('当前版本构建目录已存在，请核对后再重建');
 fs.mkdirSync(out,{recursive:true});
 const electron=path.join(root,'program');copyTree(electron,out,{filter:(_p,d)=>d.name!=='resources'});
 const {rcedit}=await import(require('node:url').pathToFileURL(require.resolve('rcedit',{paths:[__dirname,process.env.DREAMSTUDIO_BUILD_TOOLS||root]})).href);
 await rcedit(path.join(out,'DreamRoom.exe'),{icon:path.join(__dirname,'brand/dreamroom.ico'),'file-version':pkg.version,'product-version':pkg.version,'version-string':{ProductName:'造梦室 DreamStudio',FileDescription:'造梦室 DreamStudio',OriginalFilename:'DreamRoom.exe',LegalCopyright:'GPL-3.0-or-later; includes separately licensed components'}});
 fs.mkdirSync(appDir,{recursive:true});for(const f of ['main.cjs','bootstrap.cjs','download.cjs','updates.cjs','preload.cjs','updates-ui.js','updates.html','startup.html','desktop.css','brand']){const a=path.join(__dirname,f),b=path.join(appDir,f);if(fs.statSync(a).isDirectory())copyTree(a,b);else fs.copyFileSync(a,b);}
 fs.writeFileSync(path.join(appDir,'package.json'),JSON.stringify({name:pkg.name,version:pkg.version,main:'main.cjs',license:'GPL-3.0-or-later'}));
 fs.writeFileSync(path.join(appDir,'deployment.json'),JSON.stringify({mode:'installed',engine:'dreamstudio',backendRoot:'../engine'}));
 fs.writeFileSync(path.join(appDir,'release-config.json'),JSON.stringify({repository:'zhizhiniao/DreamStudio'}));
 for(const folder of ['studio','static'])copyTree(path.join(root,folder),path.join(engine,folder));
 for(const folder of ['modules','publisher','fonts'])copyTree(path.join(root,'vendor',folder),path.join(engine,'vendor',folder),{filter:(_p,d)=>!['cookies','logs','config','downloads','db','publisher-state','publisher-secrets'].includes(d.name)});
 fs.copyFileSync(path.join(root,'vendor/Y2A-Auto-LICENSE'),path.join(engine,'vendor/Y2A-Auto-LICENSE'));
 fs.mkdirSync(path.join(engine,'tools'),{recursive:true});for(const name of ['launcher.py','hub_server.py','external_hub_stub.py'])fs.copyFileSync(path.join(root,'tools',name),path.join(engine,'tools',name));
 const manifest={version:pkg.version,files:files(engine)};fs.writeFileSync(path.join(engine,'source-manifest.json'),JSON.stringify(manifest));
 for(const name of ['python','env','packages','browsers','models','bin','tesseract']){console.log('Staging runtime',name);copyTree(path.join(root,'runtime',name),path.join(engine,'runtime',name),{link:true});}
 fs.copyFileSync(path.join(root,'vendor/Y2A-Auto-LICENSE'),path.join(out,'LICENSE'));
 const publicSource=path.join(root,'desktop-output/public-source');fs.mkdirSync(publicSource,{recursive:true});
 for(const folder of ['studio','static','vendor','tools'])copyTree(path.join(engine,folder),path.join(publicSource,folder));
 copyTree(__dirname,path.join(publicSource,'desktop'),{filter:(_p,d)=>!['deployment.json','test'].includes(d.name)});
 fs.copyFileSync(path.join(root,'vendor/Y2A-Auto-LICENSE'),path.join(publicSource,'LICENSE'));
 fs.copyFileSync(path.join(__dirname,'PUBLIC-README.md'),path.join(publicSource,'README.md'));
 fs.writeFileSync(path.join(publicSource,'.gitignore'),'runtime/\nprogram/\ndata/\nlogs/\n项目素材/\ndesktop-output/\nnode_modules/\n');
 fs.writeFileSync(path.join(publicSource,'NOTICE.md'),'Y2A-Auto: GPL-3.0-or-later; vendored publisher adapters: MIT (see vendor/publisher/vendor/LICENSE). Smiley Sans and other fonts retain their bundled licenses. CreatorHub is an external optional component and is not redistributed.\n');
 return finish();
}
function finish(){
 const publicSource=path.join(root,'desktop-output/public-source');
 for(const f of files(publicSource)){if(/^(data|runtime|logs|cookies|项目素材)\//.test(f.path)||f.path.startsWith('vendor/config/')||f.path.startsWith('vendor/creatorhub/'))throw Error('公开源码包含禁止目录 '+f.path);}
 fs.copyFileSync(__filename,path.join(publicSource,'desktop/build-release.cjs'));
 fs.writeFileSync(path.join(engine,'runtime/env/pyvenv.cfg'),'include-system-site-packages = false\nversion_info = 3.11.13\n');
 const manifest=JSON.parse(fs.readFileSync(path.join(engine,'source-manifest.json'),'utf8'));
 fs.writeFileSync(path.join(root,'desktop-output/build-report.json'),JSON.stringify({version:pkg.version,engineFiles:manifest.files.length,creatorHubIncluded:false,personalDataIncluded:false},null,2));
 console.log('Compiling Windows installer');
 execFileSync(path.join(process.env['ProgramFiles(x86)'],'Inno Setup 6/ISCC.exe'),['/Qp',`/DProductDir=${out}`,`/DReleaseDir=${path.join(root,'desktop-output')}`,`/DDreamVersion=${pkg.version}`,path.join(__dirname,'windows.iss')],{stdio:'inherit',windowsHide:true});
 const installer=path.join(root,'desktop-output/DreamStudio-Setup-x64.exe');
 fs.writeFileSync(path.join(root,'desktop-output/SHA256SUMS.txt'),hash(installer)+'  DreamStudio-Setup-x64.exe\n');
 console.log('COMPLETE',JSON.stringify({installer,size:fs.statSync(installer).size,sha256:hash(installer)}));
}
build().catch(e=>{console.error(e);process.exitCode=1;});
