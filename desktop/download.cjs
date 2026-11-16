const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
async function downloadInstaller(release,directory,fetcher=fetch,progress=()=>{}){
 if(!release||!(/^(DreamStudio-Setup-x64|DreamRoom-Setup-\d+\.\d+\.\d+-x64)\.exe$/.test(release.asset))||!/^sha256:[a-f0-9]{64}$/i.test(release.digest)||!release.download.startsWith('https://github.com/zhizhiniao/DreamStudio/releases/download/'))throw Error('发布包缺少有效 SHA256 或下载来源不匹配');
 fs.mkdirSync(directory,{recursive:true});const file=path.join(directory,release.asset),part=file+'.part';
 const valid=()=>fs.existsSync(file)&&crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')===release.digest.slice(7).toLowerCase();
 if(valid())return file;
 const response=await fetcher(release.download,{credentials:'omit',signal:AbortSignal.timeout(30*60*1000)});
 if(!response.ok)throw Error('下载失败（HTTP '+response.status+'）');
 const handle=fs.openSync(part,'w'),hash=crypto.createHash('sha256');let size=0,last=-1;
 try{for await(const bytes of response.body){size+=bytes.length;if(size>release.size||size>2147483648)throw Error('下载大小超出发布信息');hash.update(bytes);fs.writeSync(handle,bytes);const pct=Math.floor(size/release.size*100);if(pct!==last){last=pct;progress('正在下载：'+pct+'%');}}}
 finally{fs.closeSync(handle);}
 if(size!==release.size||hash.digest('hex')!==release.digest.slice(7).toLowerCase()){fs.rmSync(part,{force:true});throw Error('下载校验失败，未运行安装包');}
 fs.renameSync(part,file);progress('下载完成，SHA256 校验通过');return file;
}
module.exports={downloadInstaller};
