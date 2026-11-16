'use strict';
function productionSettingsPage(){return '<section class="panel" id="production-settings"><p role="status">正在读取制作服务设置…</p></section>'}
async function loadProductionSettings(){
 const host=$('#production-settings');if(!host)return;
 try{const cfg=await api('production/settings');if(!host.isConnected)return;
 host.innerHTML=`<h2>AI 制作服务</h2><p class="muted">中文标题、简介、标签和字幕翻译使用此连接。保存设置不会发起付费调用。</p>${field('接口地址（可填写 /v1 或完整 /responses 地址）',cfg.base_url,'production-base')}${field('模型名称',cfg.model,'production-model')}${field(cfg.key_saved?'API 密钥（已保存，留空保留）':'API 密钥','','production-key','password')}${field('单次请求超时（30–120 秒）',Math.min(Number(cfg.timeout)||120,120),'production-timeout','number')}<h3>调用限额</h3><p class="muted">最近 24 小时已尝试 ${cfg.usage.calls_24h} 次，配音 ${cfg.usage.speech_chars_24h} 字。最多同时处理 2 个请求；失败和重试也计入上限，缓存命中不计入。</p>${field('24 小时调用上限',cfg.budget.AI_CALLS_24H,'budget-daily','number')}${field('每个任务累计调用上限',cfg.budget.AI_CALLS_PER_TASK,'budget-task','number')}${field('24 小时配音字数上限',cfg.budget.AI_SPEECH_CHARS_24H,'budget-speech','number')}<p class="mini">达到上限会暂停并保留进度；可调整限额后继续。这里限制调用量，实际金额以服务商账单为准。</p><div class="actions">${button('保存制作服务','production-settings-save',true)}</div><p id="production-settings-result" role="status"></p><div class="youtube-help"><h3>出现故障时</h3><p>拒绝访问：检查密钥及模型权限。限流或额度不足：先在服务商处理，再回到任务继续。网络中断：程序会有限重试，已完成字幕逐句保存。</p><p>有完整的简体中文字幕时优先复用，并注明来源。AI 翻译只补缺失句子，不因一段失败重做全文。</p></div>`;
 }catch(e){host.textContent=e.message}
}
async function saveProductionSettings(){
 const button=$('[data-action="production-settings-save"]');button.disabled=true;
 try{await api('production/settings','POST',{base_url:$('#production-base').value,model:$('#production-model').value,api_key:$('#production-key').value,timeout:Number($('#production-timeout').value),AI_CALLS_24H:Number($('#budget-daily').value),AI_CALLS_PER_TASK:Number($('#budget-task').value),AI_SPEECH_CHARS_24H:Number($('#budget-speech').value)});$('#production-key').value='';$('#production-settings-result').textContent='设置已保存，可回到原任务继续。未发起付费调用。'}
 catch(e){$('#production-settings-result').textContent=e.message}
 finally{button.disabled=false}
}
