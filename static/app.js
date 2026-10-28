'use strict';

const $ = (selector) => document.querySelector(selector);
const state = {user:null,csrf:'',mode:'login',view:'gallery',page:1,pages:1,keyword:'',q:'',sort:'newest',images:[],selected:new Set(),selection:false,activeJobs:0,detail:null,deleteIds:[],refreshBusy:false,epoch:0};
let toastTimer;
const sources = {auto:'自动选择',baidu:'百度',bing:'必应',pixabay:'Pixabay'};
const statuses = {queued:'排队中',running:'采集中',completed:'已完成',failed:'未完成',cancelled:'已停止',interrupted:'已中断'};

$('#source').add(new Option('Pixabay \u7167\u7247','pixabay'));

const story={images:[],index:0,timer:null};
function showStorySlide(index){
  if(!story.images.length)return;
  story.index=((index%story.images.length)+story.images.length)%story.images.length;
  document.querySelectorAll('.story-slide').forEach((slide,i)=>slide.classList.toggle('active',i===story.index));
  document.querySelectorAll('.story-dot').forEach((dot,i)=>dot.classList.toggle('active',i===story.index));
}
function startStoryTimer(){clearInterval(story.timer);if(story.images.length)story.timer=setInterval(()=>showStorySlide(story.index+1),7000);}
async function loadShowcase(){
  try{
    const data=await api('/api/showcase');
    if(!data.images||!data.images.length)return;
    story.images=data.images;
    const slides=$('#story-slides'),dots=$('#story-dots');
    slides.replaceChildren();dots.replaceChildren();
    data.images.forEach((image,index)=>{
      const slide=el('div','story-slide'),img=el('img');
      img.src='/api/showcase/'+image.id+'/file';img.alt='';img.decoding='async';img.loading=index? 'lazy':'eager';if(image.category==='PIXEL')img.classList.add('pixel-cover');slide.append(img);slides.append(slide);
      const dot=el('button','story-dot');dot.type='button';dot.setAttribute('aria-label','\u67e5\u770b\u7b2c '+(index+1)+' \u5f20\u5c01\u9762');
      dot.addEventListener('click',()=>{showStorySlide(index);startStoryTimer();});dots.append(dot);
    });
    $('#landscape-art').hidden=true;$('#story-nav').hidden=false;
    showStorySlide(0);startStoryTimer();
  }catch(_error){/* \u65e0\u5c55\u793a\u56fe\u65f6\u4fdd\u7559 CSS \u98ce\u666f\u753b */}
}

function el(tag, className, text) { const node=document.createElement(tag); if(className)node.className=className; if(text!==undefined)node.textContent=text; return node; }
function showToast(message, isError=false) { const node=$('#toast');node.textContent=message;node.classList.toggle('error',isError);node.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>node.hidden=true,5000); }
function bytes(value) { if(value<1024)return value+' B';if(value<1048576)return (value/1024).toFixed(1)+' KB';return (value/1048576).toFixed(1)+' MB'; }
function date(value) { return new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); }

async function api(path, options={}) {
  const headers={...(options.headers||{})};
  const settings={...options,headers};
  if(options.body!==undefined){headers['Content-Type']='application/json';settings.body=JSON.stringify(options.body);}
  if(options.method && options.method!=='GET') headers['X-CSRF-Token']=state.csrf;
  const response=await fetch(path,settings);
  if(!response.ok){let message='请求失败，请稍后重试';try{const body=await response.json();message=body.error||message;}catch{}
    if(response.status===401 && !path.includes('/auth/login') && !path.includes('/auth/register')) showAuth();
    throw new Error(message);
  }
  return options.blob ? response.blob() : response.json();
}

function showAuth() {
  state.user=null;state.csrf='';state.epoch++;state.selected.clear();state.images=[];
  document.querySelectorAll('dialog[open]').forEach(x=>x.close());
  $('#app-view').hidden=true;$('#auth-view').hidden=false;$('#boot').hidden=true;
  $('#gallery-grid').replaceChildren();$('#jobs-list').replaceChildren();
  $('#password').value='';
  if(story.images.length)startStoryTimer();
}

async function showApp(data) {
  state.user=data.user;state.csrf=data.csrf_token;state.epoch++;state.page=1;state.keyword='';state.q='';state.selected.clear();state.selection=false;
  $('#account-name').textContent=data.user.username;$('#avatar').textContent=data.user.username.slice(0,1);
  $('#auth-view').hidden=true;$('#app-view').hidden=false;$('#boot').hidden=true;$('#gallery-search').value='';
  $('#auth-error').hidden=true;$('#password').value='';clearInterval(story.timer);setView('gallery');updateSelection();
  await refresh();
}

function authMode(mode) {
  state.mode=mode;$('#login-tab').setAttribute('aria-selected',String(mode==='login'));$('#register-tab').setAttribute('aria-selected',String(mode==='register'));
  $('#auth-title').textContent=mode==='login'?'欢迎回来':'给灵感，一个新家';
  $('#auth-subtitle').textContent=mode==='login'?'登录图仓，继续收集你的灵感。':'创建自己的账户，开始第一份收藏。';
  $('#auth-submit').replaceChildren(document.createTextNode(mode==='login'?'登录我的图仓 ':'创建我的图仓 '),el('span','','↗'));
  $('#password').autocomplete=mode==='login'?'current-password':'new-password';$('#auth-error').hidden=true;
}

$('#auth-form').addEventListener('submit',async(event)=>{event.preventDefault();const button=$('#auth-submit');button.disabled=true;$('#auth-error').hidden=true;
  try { const data=await api('/api/auth/'+state.mode,{method:'POST',body:{username:$('#username').value.trim(),password:$('#password').value}});await showApp(data); }
  catch(error){$('#auth-error').textContent=error.message;$('#auth-error').hidden=false;} finally{button.disabled=false;}
});
$('#login-tab').addEventListener('click',()=>authMode('login'));$('#register-tab').addEventListener('click',()=>authMode('register'));
$('#logout').addEventListener('click',async()=>{try{await api('/api/auth/logout',{method:'POST'});showAuth();}catch(error){showToast(error.message,true);}});

function setView(view) {
  state.view=view;$('#nav-gallery').classList.toggle('active',view==='gallery');$('#nav-tasks').classList.toggle('active',view==='tasks');
  $('#breadcrumb-root').hidden=view==='gallery';$('#crumb-sep').hidden=view==='gallery';
  $('#breadcrumb-current').textContent=view==='gallery'?'我的图库':'采集任务';
  $('#page-title').replaceChildren(document.createTextNode(view==='gallery'?'你的灵感，尽收于此':'让好图片，自动入仓'),el('span','','。'));
  $('#page-description').textContent=view==='gallery'?'搜索、收集、整理。为下一次创作，存下好图片。':'查看采集进度与结果，已保存的图片会留在你的账户。';
  $('#workspace-view').hidden=view!=='tasks';$('#gallery-section').hidden=view!=='gallery';
}
$('#nav-gallery').addEventListener('click',()=>setView('gallery'));$('#nav-tasks').addEventListener('click',()=>setView('tasks'));

$('#collect-form').addEventListener('submit',async(event)=>{event.preventDefault();const button=$('#collect-submit');button.disabled=true;
  try{await api('/api/jobs',{method:'POST',body:{keyword:$('#keyword').value.trim(),limit:Number($('#limit').value),source:$('#source').value}});showToast('采集任务已创建，图片会自动保存到图库');await refresh();}
  catch(error){showToast(error.message,true);}finally{button.disabled=state.activeJobs>0;}
});

async function loadStats(epoch) {
  const data=await api('/api/stats');if(epoch!==state.epoch)return;
  state.activeJobs=data.active_jobs;
  $('#stat-images').replaceChildren(document.createTextNode(data.images.toLocaleString()),el('small','','张'));
  $('#stat-keywords').replaceChildren(document.createTextNode(String(data.keywords.length)),el('small','','个'));
  $('#stat-active').replaceChildren(document.createTextNode(String(data.active_jobs)),el('small','','个'));
  $('#stat-storage').replaceChildren(document.createTextNode((data.bytes/1048576).toFixed(1)),el('small','','MB'));
  $('#nav-image-count').textContent=data.images;$('#nav-active-dot').hidden=!data.active_jobs;
  const percentage=Math.min(100,data.bytes/data.quota_bytes*100);
  $('#storage-percent').textContent=percentage.toFixed(1)+'%';$('#usage-fill').style.width=percentage+'%';$('#storage-detail').textContent=bytes(data.bytes)+' / '+Math.round(data.quota_bytes/1048576)+' MB';
  $('#collect-submit').disabled=data.active_jobs>0;
  $('#collect-submit').replaceChildren(el('span','',data.active_jobs?'◎':'＋'),document.createTextNode(data.active_jobs?' 采集中…':' 开始采集'));
  const filters=$('#keyword-filters');filters.replaceChildren();
  for(const keyword of ['',...data.keywords]){const node=el('button','filter-chip'+(keyword===state.keyword?' active':''),keyword||'全部图片');node.type='button';node.addEventListener('click',()=>{state.keyword=keyword;state.page=1;refresh();});filters.append(node);}
  setView(state.view);
}

async function loadJobs(epoch) {
  const data=await api('/api/jobs');if(epoch!==state.epoch)return;
  $('#task-count').textContent=data.jobs.length;$('#jobs-empty').hidden=data.jobs.length>0;
  const list=$('#jobs-list');list.replaceChildren();
  for(const job of data.jobs){const row=el('article','job-row');const top=el('div','job-top');
    top.append(el('strong','job-keyword',job.keyword),el('span','job-source',sources[job.actual_source||job.source]+' · 目标 '+job.requested+' 张'),el('span','job-date',date(job.created_at)),el('span','job-badge '+job.status,statuses[job.status]||job.status));
    const bottom=el('div','job-bottom');bottom.append(el('p','job-message',job.message));
    if(['queued','running'].includes(job.status)){const button=el('button','text-button','停止采集');button.addEventListener('click',async()=>{button.disabled=true;try{await api('/api/jobs/'+job.id+'/cancel',{method:'POST'});await refresh();}catch(error){showToast(error.message,true);button.disabled=false;}});bottom.append(button);}
    const track=el('div','job-progress'),fill=el('i');fill.style.width=Math.min(100,job.saved/job.requested*100)+'%';track.append(fill);row.append(top,bottom,track);list.append(row);
  }
}

async function loadGallery(epoch) {
  const params=new URLSearchParams({page:state.page,q:state.q,keyword:state.keyword,sort:state.sort});
  const data=await api('/api/images?'+params);if(epoch!==state.epoch)return;
  if(state.page>data.pages){state.page=data.pages;return loadGallery(epoch);}
  state.images=data.images;state.pages=data.pages;$('#gallery-total').textContent=data.total;
  const grid=$('#gallery-grid');grid.replaceChildren();$('#gallery-empty').hidden=data.images.length>0;
  const searching=!!state.q||!!state.keyword;
  $('#empty-title').textContent=searching?'还没有找到匹配的图片':'你的第一份灵感，即将入仓';
  $('#empty-description').textContent=searching?'试试其他关键词，或为这个主题创建一次新的采集。':'在上方输入关键词，喜欢的图片会自动保存到这里。每一张，都只属于你的私人图库。';
  $('#empty-start').textContent=searching?'＋ 采集更多图片':'＋ 开始第一份收藏';
  for(const image of data.images){const card=el('article','image-card'+(state.selected.has(image.id)?' selected':''));
    const button=el('button','image-button');button.setAttribute('aria-label','查看图片：'+image.title);const img=el('img');img.src='/api/images/'+image.id+'/file';img.alt=image.title;img.loading='lazy';img.decoding='async';button.append(img);
    button.addEventListener('click',()=>state.selection?toggleImage(image.id):openImage(image));
    const check=el('input','card-check');check.type='checkbox';check.checked=state.selected.has(image.id);check.hidden=!state.selection;check.setAttribute('aria-label','选择图片：'+image.title);check.dataset.imageId=image.id;check.addEventListener('change',()=>toggleImage(image.id));
    const overlay=el('span','image-overlay');overlay.append(el('span','overlay-zoom','查看 ↗'));button.append(overlay);
    const info=el('div','image-info');info.append(el('h3','',image.title));const meta=el('div','image-meta');meta.append(el('span','keyword-tag',image.keyword),el('span','',image.width+' × '+image.height));info.append(meta);card.append(button,check,info);grid.append(card);
  }
  $('#pagination').hidden=data.pages<=1;$('#page-indicator').textContent=state.page+' / '+data.pages;$('#prev-page').disabled=state.page<=1;$('#next-page').disabled=state.page>=data.pages;updateSelection();
}

async function refresh() {
  if(!state.user||state.refreshBusy)return;state.refreshBusy=true;const epoch=state.epoch;
  try{await Promise.all([loadStats(epoch),loadJobs(epoch),loadGallery(epoch)]);}catch(error){if(state.user)showToast(error.message,true);}finally{state.refreshBusy=false;}
}
$('#refresh-tasks').addEventListener('click',refresh);
let searchTimer;$('#gallery-search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.q=$('#gallery-search').value.trim();state.page=1;refresh();},300);});
$('#sort').addEventListener('change',()=>{state.sort=$('#sort').value;state.page=1;refresh();});
$('#prev-page').addEventListener('click',()=>{if(state.page>1){state.page--;refresh();}});$('#next-page').addEventListener('click',()=>{if(state.page<state.pages){state.page++;refresh();}});
$('#empty-start').addEventListener('click',()=>{setView('tasks');$('#keyword').focus();});

function toggleImage(id) { state.selected.has(id)?state.selected.delete(id):state.selected.add(id);updateSelection(); }
function updateSelection() {
  $('#selection-bar').hidden=!state.selection;$('#selected-count').textContent=state.selected.size;$('#select-button').textContent=state.selection?'退出管理':'批量管理';
  $('#export-selected').disabled=state.selected.size===0;$('#delete-selected').disabled=state.selected.size===0;
  document.querySelectorAll('.card-check').forEach(check=>{check.hidden=!state.selection;check.checked=state.selected.has(check.dataset.imageId);check.closest('.image-card').classList.toggle('selected',check.checked);});
}
function stopSelection(){state.selection=false;state.selected.clear();updateSelection();}
$('#select-button').addEventListener('click',()=>{state.selection=!state.selection;if(!state.selection)state.selected.clear();updateSelection();});$('#cancel-selection').addEventListener('click',stopSelection);
$('#select-all').addEventListener('click',()=>{const all=state.images.every(x=>state.selected.has(x.id));state.images.forEach(x=>all?state.selected.delete(x.id):state.selected.add(x.id));updateSelection();});

$('#export-selected').addEventListener('click',async()=>{const button=$('#export-selected');button.disabled=true;try{const blob=await api('/api/images/export',{method:'POST',body:{ids:[...state.selected]},blob:true});const url=URL.createObjectURL(blob);const link=el('a');link.href=url;link.download='图仓收藏.zip';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);showToast('图片已打包，下载即将开始');}catch(error){showToast(error.message,true);}finally{button.disabled=false;}});

function openImage(image) {
  state.detail=image;$('#detail-image').src='/api/images/'+image.id+'/file';$('#detail-image').alt=image.title;$('#detail-title').textContent=image.title;$('#detail-keyword').textContent=image.keyword;
  $('#detail-meta').textContent=image.width+' × '+image.height+' · '+bytes(image.size)+' · '+(sources[image.source]||image.source);
  $('#detail-date').textContent='收藏于 '+new Date(image.created_at).toLocaleString('zh-CN');$('#detail-download').href='/api/images/'+image.id+'/file?download=1';
  const source=$('#detail-source');try{const url=new URL(image.page_url);if(!['http:','https:'].includes(url.protocol))throw new Error();source.href=url.href;source.hidden=false;}catch{source.hidden=true;source.removeAttribute('href');}
  $('#image-dialog').showModal();
}
$('#close-image').addEventListener('click',()=>$('#image-dialog').close());
function requestDeletion(ids){state.deleteIds=ids;$('#confirm-message').textContent='将永久删除 '+ids.length+' 张图片及其服务端文件。此操作无法撤销。';$('#confirm-dialog').showModal();}
$('#delete-selected').addEventListener('click',()=>requestDeletion([...state.selected]));$('#detail-delete').addEventListener('click',()=>{const id=state.detail.id;$('#image-dialog').close();requestDeletion([id]);});
$('#confirm-cancel').addEventListener('click',()=>$('#confirm-dialog').close());$('#confirm-delete').addEventListener('click',async()=>{const button=$('#confirm-delete');button.disabled=true;try{const data=await api('/api/images/delete',{method:'POST',body:{ids:state.deleteIds}});state.deleteIds.forEach(id=>state.selected.delete(id));$('#confirm-dialog').close();showToast('已删除 '+data.deleted+' 张图片');await refresh();}catch(error){showToast(error.message,true);}finally{button.disabled=false;}});
$('#help-button').addEventListener('click',()=>$('#help-dialog').showModal());$('#close-help').addEventListener('click',()=>$('#help-dialog').close());
document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}}));
document.addEventListener('visibilitychange',()=>{
  if(document.hidden){clearInterval(story.timer);return;}
  if(state.user)refresh(); else if(story.images.length)startStoryTimer();
});
setInterval(()=>{if(state.user&&!document.hidden)refresh();},4000);
$('#story-prev').addEventListener('click',()=>{showStorySlide(story.index-1);startStoryTimer();});
$('#story-next').addEventListener('click',()=>{showStorySlide(story.index+1);startStoryTimer();});
(async()=>{loadShowcase();try{const data=await api('/api/auth/me');await showApp(data);}catch{showAuth();}})();