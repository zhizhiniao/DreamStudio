function assetFilterState(parts){
 const use=['unused','used'].includes(parts[2])?parts[2]:'all';
 const publish=['unpublished','submitted','needs_check'].includes(parts[3])?parts[3]:['unpublished','submitted','needs_check'].includes(parts[2])?parts[2]:'all';
 return {use,publish};
}
function assetMatches(a,filter){
 const pending=Boolean(a.has_pending_publication||a.publication_state==='needs_check');
 const published=Boolean(a.has_submitted||a.publication_state==='submitted');
 const matches=filter.publish==='all'||(filter.publish==='needs_check'?pending:filter.publish==='submitted'?published:!pending&&!published);
 return (filter.use==='all'||filter.use==='used'&&a.used||filter.use==='unused'&&!a.used)&&matches;
}
function assetFilterBar(filter){
 const group=(label,name,options)=>`<label class="asset-filter-group"><span>${label}状态</span><select data-asset-filter="${name}" aria-label="${label}状态"${name==='publish'&&filter.publish==='needs_check'?' aria-describedby="asset-publication-help"':''}>${options.map(([key,title])=>`<option value="${key}"${filter[name]===key?' selected':''}>${title}</option>`).join('')}</select></label>`;
 const filtered=filter.use!=='all'||filter.publish!=='all';
 return `<nav class="asset-filters" aria-label="筛选素材">${group('使用','use',[['all','全部'],['unused','未使用'],['used','已使用']])}${group('投稿','publish',[['all','全部'],['unpublished','未投稿'],['submitted','已投稿'],['needs_check','投稿结果待核实']])}${filtered?'<a class="asset-filter-reset" href="#assets/library">清除筛选</a>':''}${filter.publish==='needs_check'?'<p id="asset-publication-help" class="asset-filter-help">程序未能确认投稿是否成功。请到发布记录核对平台作品，再确认结果，避免重复投稿。</p>':''}</nav>`;
}
document.addEventListener('change',e=>{
 const name=e.target.dataset.assetFilter;
 if(!['use','publish'].includes(name))return;
 const filter=assetFilterState(location.hash.slice(1).split('/'));
 filter[name]=e.target.value;
 location.hash=`assets/library/${filter.use}/${filter.publish}`;
});
