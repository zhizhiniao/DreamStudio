'use strict';
function youtubeSettingsPage(){return `<section class="panel youtube-settings" id="youtube-settings"><p role="status">正在读取 YouTube 设置…</p></section>`}
function ytSessionLabel(s){return !s.session.login_fields_present?'请登录 YouTube':s.check?.ok?'视频读取正常':'已有保存的会话'}
async function loadYouTubeSettings(){
 const host=$('#youtube-settings');if(!host)return;
 try{
 const s=await api('youtube/settings');if(!host.isConnected)return;
 const failed=(data.downloads||[]).find(j=>j.status==='failed');
 const status=s.session;
 host.innerHTML=`<div class="youtube-settings-head"><div><h2>YouTube 账号</h2><p class="muted">登录后自动保存和更新，供视频导入与字幕下载使用。</p></div>${pill(ytSessionLabel(s),status.login_fields_present?'':'warn')}</div>
 <p class="youtube-session-note">${status.login_fields_present?'已有会话已保留。首次使用专用窗口时，请在窗口中登录一次。':'首次使用请点击下方登录按钮。'}</p>
 <div class="youtube-settings-grid"><section><h3>登录与基本设置</h3><p class="muted">点击登录，在专用窗口完成登录后自动保存。无需 Docker，也无需安装扩展。</p>
 <div class="actions">${button(s.browser?.running?'登录窗口已打开':'登录 YouTube','yt-login',true)}</div>
 <div id="yt-browser-result" role="status" class="youtube-result">${esc(s.browser?.message||'首次使用只需登录一次；以后下载前自动尝试刷新。')}</div>
 ${field('网络代理（可选）',s.proxy_url,'yt-proxy')}<small>打不开 YouTube 时可填写本机代理，例如 http://127.0.0.1:7890。保存后重新打开登录窗口。</small>
 <details class="youtube-manual"><summary>高级设置：同步来源与 CookieCloud</summary>
 <label class="field"><span>会话来源</span><select id="yt-source">${[['browser','专用登录窗口（推荐）'],['local_cloud','内置 CookieCloud 扩展同步'],['external','外部 CookieCloud'],['manual','手动导入']].map(([v,l])=>`<option value="${v}" ${s.session_source===v?'selected':''}>${l}</option>`).join('')}</select></label>
 <p>内置服务随造梦室运行，仅本机可访问。普通登录不需要填写下方参数。</p>
 ${button('显示扩展连接参数','yt-setup')}<div id="yt-setup-result" role="status"></div>
 <p class="muted">以下仅用于已有的外部 CookieCloud 服务。</p>
 <label class="youtube-toggle"><input type="checkbox" id="yt-enabled" ${s.enabled?'checked':''}>启用 CookieCloud，下载前自动刷新会话</label>
 ${field('CookieCloud 服务地址',s.server_url,'yt-server')}
 <div class="youtube-settings-grid">${field(s.uuid_saved?'UUID（已保存，留空保留）':'UUID','','yt-uuid','password')}${field(s.password_saved?'密码（已保存，留空保留）':'密码','','yt-password','password')}</div>
 <label class="field"><span>加密算法</span><select id="yt-crypto">${[['auto','自动识别'],['legacy','Legacy'],['aes-128-cbc-fixed','AES-128-CBC Fixed']].map(([v,l])=>`<option value="${v}" ${s.crypto_type===v?'selected':''}>${l}</option>`).join('')}</select></label>
 
 <small>外部密码与 Cookie 不会回显；内置连接参数仅在主动点击后显示。</small>
 <div class="actions">${button('保存并立即同步','yt-sync')}</div></details><div class="actions">${button('保存设置','yt-save')}</div>
 <div id="yt-sync-result" class="youtube-result" role="status">${ytResult(s.sync)}</div>
 </section><section><details class="youtube-manual"><summary>连接诊断（遇到下载问题时展开）</summary><h3>检测视频读取</h3><p class="muted">使用已保存设置读取一小段真实视频数据；不生成文案、不调用付费 AI。</p><small>下载组件 ${esc(s.component?.downloader||'未知')} · ${s.component?.javascript_ready?'JavaScript 运行时已连接':'缺少 JavaScript 运行时'}</small>
 ${field('YouTube 视频链接',s.check?.url||failed?.url||'','yt-test-url')}
 <div class="actions">${button('检测视频读取','yt-check',true)}<a class="btn" id="yt-open-video" href="https://www.youtube.com/" target="_blank" rel="noopener noreferrer">在浏览器打开 YouTube</a></div>
 <div id="yt-check-result" class="youtube-result" role="status">${ytResult(s.check)}</div>
 <div class="youtube-help"><h3>要求本人验证时</h3><ol><li>点击“登录 YouTube”，在专用窗口完成页面要求的验证。</li><li>等待显示“登录会话已自动保存”，再返回本页。</li><li>“检测视频读取”通过后，重试下方失败任务。</li></ol><p>同步成功只代表会话已保存。网络错误、限流和视频访问限制分别处理。</p></div>
 <details class="youtube-manual"><summary>手动导入 cookies.txt（不使用自动同步时）</summary><p>选择从已登录浏览器导出的 YouTube Netscape cookies.txt。导入后关闭自动同步，避免手动会话被覆盖。</p><label class="field"><span>YouTube Cookie 文件</span><input type="file" id="yt-cookie-file" accept=".txt"></label>${button('导入下载会话','yt-import')}<div id="yt-import-result" role="status"></div></details>
 </details></section></div><section class="youtube-failures"><h3>待重试的下载</h3><p class="muted">先修复上方设置，再重试；原任务和已下载文件保留。</p><div class="task-list">${(data.downloads||[]).some(j=>j.status==='failed')?downloadJobs(true):'<p>当前没有失败的 YouTube 导入任务。</p>'}</div></section>`;
 const updateLink=()=>{const input=$('#yt-test-url');const anchor=$('#yt-open-video');try{const u=new URL(input.value);anchor.href=u.protocol==='https:'&&['www.youtube.com','youtube.com','youtu.be','m.youtube.com'].includes(u.hostname)?u.href:'https://www.youtube.com/'}catch{anchor.href='https://www.youtube.com/'}};
 $('#yt-test-url').addEventListener('input',updateLink);updateLink();if(s.browser?.running)ytWatchBrowser(host);
 }catch(e){host.innerHTML=`<p role="alert">${esc(e.message)}</p>${button('重新加载设置','yt-reload')}`}
}
function ytResult(r){return r?.message?`<p class="${r.ok?'status-good':'status-warn'}">${esc(r.message)}</p><small>${time(r.at)}</small>`:''}
function youtubeSettingsValues(){return {session_source:$('#yt-source').value,enabled:$('#yt-enabled').checked,server_url:$('#yt-server').value,uuid:$('#yt-uuid').value,password:$('#yt-password').value,crypto_type:$('#yt-crypto').value,proxy_url:$('#yt-proxy').value}}
async function youtubeSettingsAction(action){
 if(!action.startsWith('yt-'))return false;
 const host=$('#youtube-settings');const controls=host?[...host.querySelectorAll('button,input,select')]:[];
 controls.forEach(x=>x.disabled=true);
 try{
 if(action==='yt-login'){
 const values=youtubeSettingsValues();values.session_source='browser';values.enabled=false;
 await api('youtube/settings','POST',values);
 const r=await api('youtube/session/login','POST');$('#yt-browser-result').textContent=r.message;
 $('#yt-source').value='browser';$('#yt-enabled').checked=false;ytWatchBrowser(host);return true;
 }
 if(action==='yt-setup'){
 const r=await api('youtube/cookiecloud/setup','POST');
 $('#yt-setup-result').innerHTML=`<p>只在本机扩展中填写；请勿分享这些参数。扩展仅同步 youtube.com。</p>${field('服务地址',r.server_url,'yt-local-url')}${field('UUID',r.uuid,'yt-local-uuid','password')}${field('密码',r.password,'yt-local-password','password')}`;return true;
 }
 if(action==='yt-reload'){await loadYouTubeSettings();return true}
 if(action==='yt-save'||action==='yt-sync'){
 const cfg=await api('youtube/settings','POST',youtubeSettingsValues());
 $('#yt-uuid').value='';$('#yt-password').value='';
 $('#yt-uuid').closest('label').querySelector('span').textContent=cfg.uuid_saved?'UUID（已保存，留空保留）':'UUID';
 $('#yt-password').closest('label').querySelector('span').textContent=cfg.password_saved?'密码（已保存，留空保留）':'密码';
 $('#yt-sync-result').textContent=action==='yt-sync'?'正在更新下载会话…':'设置已保存。';
 if(action==='yt-sync'){const result=await api('youtube/session/sync','POST');$('#yt-sync-result').innerHTML=ytResult(result);toast(result.message)}
 else toast('YouTube 设置已保存');
 }
 if(action==='yt-check'){
 $('#yt-check-result').textContent='正在检测真实视频读取，最多等待 90 秒…';
 const result=await api('youtube/session/check','POST',{url:$('#yt-test-url').value});
 $('#yt-check-result').innerHTML=ytResult(result);toast(result.message);
 }
 if(action==='yt-import'){
 const file=$('#yt-cookie-file').files[0];if(!file)throw new Error('请选择 YouTube cookies.txt 文件');
 const fd=new FormData();fd.append('file',file);
 const result=await api('youtube/session/import','POST',fd);$('#yt-cookie-file').value='';$('#yt-enabled').checked=false;$('#yt-source').value='manual';
 $('#yt-import-result').textContent=result.message;toast(result.message);
 }
 const latest=await api('youtube/settings');
 $('#yt-check-result').innerHTML=ytResult(latest.check)||(action==='yt-check'?'':'设置或会话已更新，请重新检测视频读取。');
 $('.youtube-session-note').textContent=latest.session.login_fields_present?'会话已保存在本机。下载时会自动使用。':'请点击登录 YouTube，完成后会自动保存。';
 const badge=$('.youtube-settings-head .pill');badge.textContent=ytSessionLabel(latest);
 }catch(e){const target=$('#'+({'yt-check':'yt-check-result','yt-import':'yt-import-result'}[action]||'yt-sync-result'));if(target)target.textContent=e.message;toast(e.message)}
 finally{controls.forEach(x=>x.disabled=false)}
 return true;
}

let ytBrowserTimer;
function ytWatchBrowser(host){
 clearTimeout(ytBrowserTimer);
 const tick=async()=>{
 if(!host.isConnected)return;
 try{const r=await api('youtube/session/browser');const target=$('#yt-browser-result');if(target)target.textContent=r.message||'等待登录';
 if(r.state==='saved'){const s=await api('youtube/settings');$('.youtube-session-note').textContent='登录会话已自动保存，可以关闭登录窗口。';const badge=$('.youtube-settings-head .pill');badge.textContent=ytSessionLabel(s);}
 if(r.running)ytBrowserTimer=setTimeout(tick,2500);
 }catch(e){const target=$('#yt-browser-result');if(target)target.textContent='状态读取失败，请刷新页面重试。'}
 };ytBrowserTimer=setTimeout(tick,1500);
}
