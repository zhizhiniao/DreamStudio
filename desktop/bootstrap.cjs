const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
function prepareWorkspace(engine,home){
 const manifest=JSON.parse(fs.readFileSync(path.join(engine,'source-manifest.json'),'utf8'));
 const workspace=path.join(home,'workspace');fs.mkdirSync(workspace,{recursive:true});
 for(const entry of manifest.files){
  if(!/^(studio|static|vendor|tools)\//.test(entry.path)||entry.path.includes('..')||entry.path.includes('creatorhub')||entry.path.startsWith('vendor/config/'))throw Error('安装包代码清单无效');
  const source=path.join(engine,entry.path),dest=path.join(workspace,entry.path);
  if(crypto.createHash('sha256').update(fs.readFileSync(source)).digest('hex')!==entry.sha256)throw Error('安装包文件校验失败');
  fs.mkdirSync(path.dirname(dest),{recursive:true});fs.copyFileSync(source,dest);
 }
 for(const folder of ['data','logs','项目素材','vendor/config'])fs.mkdirSync(path.join(workspace,folder),{recursive:true});
 const token=path.join(workspace,'data/hub-token');if(!fs.existsSync(token))fs.writeFileSync(token,crypto.randomBytes(32).toString('hex'));
 const vendorConfig=path.join(workspace,'vendor/config/config.json');if(!fs.existsSync(vendorConfig))fs.writeFileSync(vendorConfig,'{}');
 const runtime=path.join(engine,'runtime'),pythonHome=path.join(runtime,'python/cpython-3.11.13-windows-x86_64-none');
 fs.writeFileSync(path.join(runtime,'env/pyvenv.cfg'),`home = ${pythonHome}\ninclude-system-site-packages = false\nversion_info = 3.11.13\n`);
 const python=path.join(runtime,'env/Scripts/python.exe');
 fs.writeFileSync(path.join(workspace,'runtime.json'),JSON.stringify({python,runtime,hub_python:python},null,2));
 return {backend:workspace,runtime,python};
}
module.exports={prepareWorkspace};
