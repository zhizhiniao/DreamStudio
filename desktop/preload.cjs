const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('dreamRoom',{
  info:()=>ipcRenderer.invoke('updates:info'),
  check:()=>ipcRenderer.invoke('updates:check'),
  configure:repo=>ipcRenderer.invoke('updates:configure',repo),
  automatic:enabled=>ipcRenderer.invoke('updates:automatic',enabled),
  skip:()=>ipcRenderer.invoke('updates:skip'),
  install:()=>ipcRenderer.invoke('updates:install'),
  onProgress:callback=>ipcRenderer.on('updates:progress',(_event,message)=>callback(message)),
  open:kind=>ipcRenderer.invoke('updates:open',kind),
  onChange:callback=>ipcRenderer.on('updates:changed',(_event,data)=>callback(data))
});
