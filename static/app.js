import mediumZoom from './vendor/zoom.js';

const $=s=>document.querySelector(s);
const username=location.pathname.replace(/^\//,'').replace(/\/$/,'');
let zoom;
function ensureZoom(){
  const images=[...document.querySelectorAll('.product-photo')];
  if(!zoom){
    zoom=mediumZoom({background:'rgba(0, 0, 0, 0.7)',margin:innerWidth<868?25:50,scrollOffset:50});
    zoom.on('open',()=>{document.body.dataset.hcZoomActive='';});
    zoom.on('closed',()=>{delete document.body.dataset.hcZoomActive;});
    addEventListener('resize',()=>zoom.update({margin:innerWidth<868?25:50}));
  }
  zoom.detach(); images.forEach(img=>img.classList.add('hc-zoom-image')); if(images.length)zoom.attach(images);
}
function el(tag,text,cls){const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;}
function service(url){
  let host; try{host=new URL(url).hostname.toLowerCase();}catch{return null;}
  const matches=d=>host===d||host.endsWith('.'+d);
  if(matches('market.yandex.ru'))return {url,label:'Yandex Market',kind:'market'};
  if(matches('wildberries.ru'))return {url,label:'Wildberries',kind:'wb'};
  if(matches('ozon.ru'))return {url,label:'Ozon',kind:'ozon'};
  return {url,label:host,kind:'web'};
}
function card(item){
  const node=el('article',null,'gift');
  const figure=el('div',null,'drawing');
  if(item.image){const img=el('img');img.className='product-photo';img.src=item.image;img.alt=item.title;figure.append(img);}
  const content=el('div',null,'gift-content');
  const heading=el('div',undefined,'gift-heading'); heading.append(el('h2',item.title));
  if(item.mine){const edit=el('button','Edit','edit-item'); edit.type='button'; edit.onclick=()=>startEdit(item); heading.append(edit);}
  content.append(heading);
  if(item.description)content.append(el('p',item.description,'gift-description'));
  const links=el('div',null,'gift-links');
  for(const url of item.links){
    const info=service(url); if(!info)continue;
    const a=el('a',null,'link-badge '+info.kind); a.href=info.url; a.target='_blank'; a.rel='noopener noreferrer'; a.title=info.label;
    if(info.kind!=='web'){const img=el('img');img.src='/static/assets/'+info.kind+'.webp';img.alt='';img.width=34;img.height=34;a.append(img);}
    else a.textContent=info.label;
    links.append(a);
  }
  if(links.childElementCount)content.append(links);
  const control=el('div',null,'reservation-control');
  const toggle=el('button',null,'reservation-switch'); toggle.type='button'; toggle.setAttribute('role','switch');
  toggle.setAttribute('aria-checked',String(item.reserved)); toggle.setAttribute('aria-label','Reservation: '+item.title);
  toggle.append(el('span',null,'switch-thumb'));
  toggle.onclick=()=>openReservation(item,toggle);
  control.append(toggle,el('span',item.reserved?'Reserved':'Available','reservation-status'));
  node.append(figure,content,control); return node;
}
async function load(){
  const me=await fetch('/api/me').then(r=>r.json());
  if(me.user){
    const menu=el('div',undefined,'account-menu');
    const logout=el('button','Log out','account-logout'); logout.type='button'; logout.hidden=true;
    logout.onclick=async()=>{await fetch('/logout',{method:'POST'}); location.href='/login';};
    const avatar=el('button',undefined,'account-avatar'); avatar.type='button'; avatar.setAttribute('aria-label',me.user.username); avatar.setAttribute('aria-expanded','false');
    if(me.user.avatar){const img=el('img'); img.src=me.user.avatar; img.alt=''; avatar.append(img);}
    else avatar.textContent=(me.user.username||'?').slice(0,1).toUpperCase();
    avatar.onclick=()=>{logout.hidden=!logout.hidden; avatar.setAttribute('aria-expanded',String(!logout.hidden));};
    menu.append(logout,avatar); $('#account-slot').replaceChildren(menu);
  }
  if(!username){$('#empty').hidden=false;$('#empty').textContent='Open a list at /username, or sign in.';return;}
  const response=await fetch('/api/wishlists/'+encodeURIComponent(username));
  if(response.status===404){$('#empty').hidden=false;$('#empty').textContent='This person has not signed in yet.';return;}
  const data=await response.json();
  document.title=data.owner.username+' / Wishlist';
  $('#composer').hidden=!data.mine;
  const root=$('#items'); root.replaceChildren(); data.items.forEach(item=>root.append(card(item)));
  if(!data.items.length){$('#empty').hidden=false;$('#empty').textContent=data.mine?'The list is empty.':'Nothing here yet.';}
  ensureZoom();
}
let current=null;
function openReservation(item,toggle){
  current={item,toggle}; $('#reserve-form').reset(); $('#name-error').textContent='';
  $('#reservation-heading').textContent=item.reserved?'Release gift':'Reserve gift';
  $('#name-hint').textContent=item.reserved?'Use the same name.':'Names are unique. Use a name that is yours.';
  $('#reserve').hidden=false; $('#name').focus();
}
$('#reserve-form').onsubmit=async event=>{
  event.preventDefault();
  const name=$('#name').value.trim();
  const path=current.item.reserved?'release':'reserve';
  const response=await fetch('/api/items/'+current.item.id+'/'+path,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name})});
  if(!response.ok){$('#name-error').textContent=(await response.json()).detail||'Could not save';return;}
  $('#reserve').hidden=true; load();
};
document.addEventListener('pointerdown',event=>{if(!$('#reserve').hidden&&!$('#reserve').contains(event.target)){$('#reserve').hidden=true;}});
$('#composer').onsubmit=async event=>{
  event.preventDefault(); const form=event.target; $('#composer-error').textContent='';
  const body={title:form.title.value,description:form.description.value,price:form.price.value,links:form.links.value.split(/\n+/).filter(Boolean)};
  const editing=form.dataset.id;
  const response=await fetch(editing?'/api/items/'+editing:'/api/wishlists/'+encodeURIComponent(username)+'/items',{method:editing?'PATCH':'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok){$('#composer-error').textContent='Could not add';return;}
  const item=await response.json();
  if(form.image.files[0]){
    const data=new FormData(); data.append('file',form.image.files[0]);
    await fetch('/api/items/'+item.id+'/image',{method:'POST',body:data});
  }
  form.reset(); delete form.dataset.id; form.querySelector('button').textContent='Add'; load();
};
function startEdit(item){
  const form=$('#composer'); form.hidden=false; form.dataset.id=item.id;
  form.title.value=item.title; form.description.value=item.description||''; form.price.value=item.price||''; form.links.value=(item.links||[]).join('\n');
  form.querySelector('button').textContent='Save'; form.scrollIntoView({block:'start'});
}
load();
