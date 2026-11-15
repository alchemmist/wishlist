import mediumZoom from './vendor/zoom.js';

const $ = selector => document.querySelector(selector);
const form = $('#composer');
const field = name => form.elements.namedItem(name);
let username;
try { username = decodeURIComponent(location.pathname.replace(/^\//, '').replace(/\/$/, '')); }
catch { username = ''; }
let zoom, current;
let composerPhotos = [];
let draggedPhoto = null;
let zoomOpening = false, closeZoomWhenReady = false;
let composerRequestId;
let composerBusy = false;
let reservationBusy = false;
let accountRequest = Promise.resolve(undefined);
let loadVersion = 0;

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function service(url) {
  let parsed;
  try { parsed = new URL(url); } catch { return null; }
  if (!['http:', 'https:'].includes(parsed.protocol)) return null;
  const host = parsed.hostname.toLowerCase();
  const matches = domain => host === domain || host.endsWith('.' + domain);
  if (matches('market.yandex.ru')) return {label: 'Yandex Market', kind: 'market'};
  if (matches('wildberries.ru')) return {label: 'Wildberries', kind: 'wb'};
  if (matches('ozon.ru')) return {label: 'Ozon', kind: 'ozon'};
  return {label: host, kind: 'web'};
}
function ensureZoom() {
  if (!zoom) {
    zoom = mediumZoom({background: 'rgba(0,0,0,.7)', margin: innerWidth < 868 ? 25 : 50, scrollOffset: 50});
    zoom.on('open', () => { zoomOpening = true; closeZoomWhenReady = false; document.body.dataset.hcZoomActive = ''; });
    zoom.on('opened', () => { zoomOpening = false; if (closeZoomWhenReady) zoom.close(); });
    zoom.on('closed', event => { delete document.body.dataset.hcZoomActive; closeZoomWhenReady = false; event.target.focus({preventScroll: true}); });
    addEventListener('resize', () => zoom.update({margin: innerWidth < 868 ? 25 : 50}));
  }
  zoom.detach();
  const images = [...document.querySelectorAll('.product-photo')];
  for (const image of images) {
    image.classList.add('hc-zoom-image');
    image.tabIndex = 0;
    image.setAttribute('role', 'button');
    image.setAttribute('aria-label', 'Enlarge photo: ' + image.alt);
    image.onkeydown = event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); zoom.open(image); }
    };
  }
  if (images.length) zoom.attach(images);
}
function card(item) {
  const node = el('article', null, 'gift');
  node.dataset.id = item.id;
  const figure = el('div', null, 'drawing');
  const photos = item.photos?.length ? item.photos : item.image ? [{url: item.image}] : [];
  if (photos.length) {
    photos.forEach((photo, index) => {
      const image = el('img', null, 'product-photo');
      image.src = photo.url; image.alt = index ? item.title + ' — photo ' + (index + 1) : item.title;
      image.loading = 'lazy'; image.draggable = false;
      image.onerror = () => { image.hidden = true; };
      figure.append(image);
    });
    if (photos.length > 1) figure.classList.add('photo-gallery');
  } else node.classList.add('no-photo');
  const content = el('div', null, 'gift-content');
  const heading = el('h2', item.title);
  const titleRow = el('div', null, 'gift-title'); titleRow.append(heading);
  if (item.mine) {
    const edit = el('button', null, 'edit-item');
    edit.type = 'button'; edit.setAttribute('aria-label', 'Edit ' + item.title);
    edit.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4l10-10-4-4L4 16z"/><path d="m12.5 6.5 4 4"/></svg>';
    edit.onclick = () => { if (!composerBusy) startEdit(item); };
    titleRow.append(edit);
  }
  content.append(titleRow);
  if (item.price) content.append(el('p', item.price, 'gift-price'));
  if (item.description) content.append(el('p', item.description, 'gift-description'));
  const links = el('div', null, 'gift-links');
  for (const url of item.links) {
    const info = service(url);
    if (!info) continue;
    const link = el('a', null, 'link-badge ' + info.kind);
    link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.title = info.label; link.setAttribute('aria-label', info.label);
    if (info.kind === 'web') link.textContent = info.label;
    else {
      const image = el('img'); image.src = '/static/assets/' + info.kind + '.webp'; image.alt = ''; link.append(image);
    }
    links.append(link);
  }
  if (links.childElementCount) content.append(links);
  const control = el('div', null, 'reservation-control');
  const toggle = el('button', null, 'reservation-switch');
  toggle.type = 'button'; toggle.setAttribute('role', 'switch');
  toggle.setAttribute('aria-checked', String(item.reserved));
  toggle.setAttribute('aria-label', 'Reservation: ' + item.title);
  toggle.append(el('span', null, 'switch-thumb'));
  toggle.onclick = () => { if (!reservationBusy) openReservation(item, toggle); };
  control.append(toggle, el('span', item.reserved ? 'Reserved' : 'Available', 'reservation-status'));
  node.append(figure, content, control);
  return node;
}
function setAccount(user) {
  if (!user) {
    const link = el('a', 'Sign in', 'account-link'); link.href = '/login';
    $('#account-slot').replaceChildren(link); return;
  }
  const menu = el('div', null, 'account-menu');
  const options = el('div', null, 'account-options'); options.hidden = true;
  const ownList = el('a', 'My wishlist'); ownList.href = '/' + encodeURIComponent(user.username);
  const logout = el('button', 'Log out', 'account-logout'); logout.type = 'button';
  logout.onclick = async () => {
    logout.disabled = true;
    try {
      const response = await fetch('/logout', {method: 'POST'});
      if (!response.ok) throw new Error();
      location.href = '/';
    } catch { showNotice('Could not log out. Please try again.'); logout.disabled = false; }
  };
  const avatar = el('button', null, 'account-avatar');
  avatar.type = 'button'; avatar.setAttribute('aria-label', user.username); avatar.setAttribute('aria-expanded', 'false');
  if (user.avatar) {
    const image = el('img'); image.src = user.avatar; image.alt = '';
    image.onerror = () => { avatar.textContent = user.username.slice(0, 1).toUpperCase(); };
    avatar.append(image);
  } else avatar.textContent = user.username.slice(0, 1).toUpperCase();
  avatar.onclick = () => { options.hidden = !options.hidden; avatar.setAttribute('aria-expanded', String(!options.hidden)); };
  options.append(ownList, logout); menu.append(options, avatar); $('#account-slot').replaceChildren(menu);
}
function showNotice(message) { $('#notice').textContent = message; }
async function load() {
  const version = ++loadVersion;
  $('#empty').hidden = true; $('#retry').hidden = true;
  // Public lists must remain readable even if the separate account request fails.
  accountRequest = fetch('/api/me').then(response => { if (!response.ok) throw new Error(); return response.json(); })
    .then(data => { if (version === loadVersion) setAccount(data.user); return data.user; }).catch(() => undefined);
  if (!username) {
    $('#empty').hidden = false; $('#empty').textContent = 'Open a list at /username, or sign in.'; return;
  }
  try {
    const response = await fetch('/api/wishlists/' + encodeURIComponent(username));
    if (version !== loadVersion) return;
    if (response.status === 404) {
      $('#items').replaceChildren(); form.hidden = true;
      $('#empty').hidden = false; $('#empty').textContent = 'This wishlist does not exist yet.'; return;
    }
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (version !== loadVersion) return;
    document.title = data.owner.username + ' / Wishlist';
    $('#owner-name').textContent = data.owner.name || data.owner.username;
    form.hidden = !data.mine;
    $('#items').replaceChildren(...data.items.map(card));
    if (!data.items.length) {
      $('#empty').hidden = false; $('#empty').textContent = data.mine ? 'The list is empty. Add your first gift above.' : 'Nothing here yet.';
    }
    ensureZoom();
  } catch {
    if (version !== loadVersion) return;
    $('#empty').hidden = false; $('#empty').textContent = 'Could not load the wishlist. Check your connection and try again.';
    $('#retry').hidden = false;
  }
}
$('#retry').onclick = load;
function closeReservation(focus = false) {
  $('#reserve').hidden = true;
  if (focus && current?.toggle.isConnected) current.toggle.focus();
}
async function openReservation(item, toggle) {
  if (reservationBusy) return;
  reservationBusy = true;
  const user = await accountRequest;
  reservationBusy = false;
  if (user === undefined) {
    showNotice('Could not check your account. Reload the page and try again.'); return;
  }
  if (user) { await saveReservation(item, toggle); return; }
  current = {item, toggle}; $('#reserve-form').reset(); $('#name-error').textContent = '';
  $('#reservation-heading').textContent = item.reserved ? 'Release gift' : 'Reserve gift';
  $('#name-hint').textContent = item.reserved ? 'Use the same name.' : 'Names are unique. Use a name that is yours.';
  $('#reserve').hidden = false; placeReserve(); $('#name').focus();
}
function placeReserve() {
  const pop = $('#reserve'); if (pop.hidden || !current?.toggle.isConnected) return;
  const viewport = window.visualViewport;
  const top = viewport?.offsetTop || 0, leftEdge = viewport?.offsetLeft || 0;
  const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
  pop.style.maxHeight = Math.max(100, height - 24) + 'px';
  const box = current.toggle.getBoundingClientRect();
  const left = Math.max(leftEdge + 12, Math.min(leftEdge + width - pop.offsetWidth - 12, box.left + box.width / 2 - pop.offsetWidth / 2));
  const above = box.top - pop.offsetHeight - 12 >= top + 12;
  pop.style.left = left + 'px';
  pop.style.top = Math.max(top + 12, Math.min(top + height - pop.offsetHeight - 12, above ? box.top - pop.offsetHeight - 12 : box.bottom + 12)) + 'px';
  pop.classList.toggle('below', !above);
  pop.style.setProperty('--arrow-left', Math.max(14, Math.min(pop.offsetWidth - 14, box.left + box.width / 2 - left)) + 'px');
}
addEventListener('resize', placeReserve); addEventListener('scroll', placeReserve, true);
window.visualViewport?.addEventListener('resize', placeReserve);
window.visualViewport?.addEventListener('scroll', placeReserve);
async function saveReservation(item, toggle, name) {
  if (reservationBusy) return;
  const signedIn = name === undefined;
  const button = $('#reserve-form').querySelector('button');
  const reportError = message => {
    if (signedIn) showNotice(message);
    else { $('#name-error').textContent = message; placeReserve(); }
  };
  reservationBusy = true; button.disabled = true; toggle.disabled = true;
  $('#name-error').textContent = ''; showNotice('');
  try {
    const response = await fetch('/api/items/' + item.id + '/' + (item.reserved ? 'release' : 'reserve'), {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: signedIn ? undefined : JSON.stringify({name})
    });
    if (!response.ok) {
      reportError(response.status === 409 ? 'This gift is already reserved. Reload the page for its current status.' : response.status === 403 ? (signedIn ? 'This gift was reserved by someone else.' : 'The name does not match the reservation.') : 'Could not save. Please try again.');
      return;
    }
    await response.json();
    closeReservation(); await load();
    document.querySelector(`.gift[data-id="${item.id}"] .reservation-switch`)?.focus();
  } catch { reportError('Connection failed. Please try again.'); }
  finally { reservationBusy = false; button.disabled = false; toggle.disabled = false; }
}
$('#reserve-form').onsubmit = async event => {
  event.preventDefault(); if (reservationBusy || !current) return;
  await saveReservation(current.item, current.toggle, $('#name').value.trim());
};
addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    if (zoomOpening) closeZoomWhenReady = true;
    else zoom?.close();
    if (!$('#reserve').hidden) closeReservation(true);
    const options = $('.account-options'); if (options) options.hidden = true;
    $('.account-avatar')?.setAttribute('aria-expanded', 'false');
  }
});
document.addEventListener('pointerdown', event => {
  if (zoomOpening && event.target.matches('.medium-zoom-overlay,.medium-zoom-image--opened')) closeZoomWhenReady = true;
  if (!$('#reserve').hidden && !$('#reserve').contains(event.target) && !current?.toggle.contains(event.target)) closeReservation();
  if (!$('.account-menu')?.contains(event.target)) {
    const options = $('.account-options'); if (options) options.hidden = true;
    $('.account-avatar')?.setAttribute('aria-expanded', 'false');
  }
});
function clearPreview() {
  for (const photo of composerPhotos) if (photo.file) URL.revokeObjectURL(photo.url);
  composerPhotos = []; renderPhotos();
}
function movePhoto(id, targetId) {
  if (composerBusy || id === targetId) return;
  const from = composerPhotos.findIndex(photo => photo.id === id);
  const to = composerPhotos.findIndex(photo => photo.id === targetId);
  if (from < 0 || to < 0) return;
  composerPhotos.splice(to, 0, composerPhotos.splice(from, 1)[0]); renderPhotos();
}
function renderPhotos() {
  $('#photo-previews').replaceChildren(...composerPhotos.map((photo, index) => {
    const tile = el('div', null, 'photo-tile'); tile.dataset.photoId = photo.id;
    tile.setAttribute('role', 'listitem'); tile.draggable = !composerBusy;
    const image = el('img'); image.src = photo.url; image.alt = 'Photo ' + (index + 1); image.draggable = false;
    if (!index) image.id = 'photo-preview';
    const remove = el('button', '×', 'photo-remove'); remove.type = 'button';
    remove.setAttribute('aria-label', 'Remove photo ' + (index + 1));
    remove.onclick = () => {
      if (composerBusy) return;
      if (photo.file) URL.revokeObjectURL(photo.url);
      composerPhotos = composerPhotos.filter(value => value !== photo); renderPhotos();
    };
    const handle = el('button', '⠿', 'photo-handle'); handle.type = 'button';
    handle.setAttribute('aria-label', 'Drag photo ' + (index + 1));
    handle.onpointerdown = event => {
      if (composerBusy || event.button !== 0) return;
      event.preventDefault(); draggedPhoto = photo.id;
      $('#photo-previews').setPointerCapture(event.pointerId);
    };
    const controls = el('div', null, 'photo-moves');
    for (const [offset, text] of [[-1, '←'], [1, '→']]) {
      const button = el('button', text); button.type = 'button';
      button.setAttribute('aria-label', 'Move photo ' + (index + 1) + (offset < 0 ? ' left' : ' right'));
      button.disabled = !composerPhotos[index + offset];
      button.onclick = () => { movePhoto(photo.id, composerPhotos[index + offset]?.id); $('#photo-previews [data-photo-id="' + photo.id + '"] .photo-handle')?.focus(); };
      controls.append(button);
    }
    tile.ondragstart = event => { if (composerBusy) { event.preventDefault(); return; } draggedPhoto = photo.id; event.dataTransfer.setData('text/plain', photo.id); event.dataTransfer.effectAllowed = 'move'; };
    tile.ondragover = event => event.preventDefault();
    tile.ondrop = event => { event.preventDefault(); movePhoto(event.dataTransfer.getData('text/plain'), photo.id); draggedPhoto = null; };
    tile.ondragend = () => { draggedPhoto = null; };
    tile.append(image, remove, handle, controls); return tile;
  }));
  $('#remove-photo').hidden = !composerPhotos.length;
}
$('#photo-previews').onpointermove = event => {
  if (!draggedPhoto || composerBusy) return;
  const tile = document.elementFromPoint(event.clientX, event.clientY)?.closest('.photo-tile');
  if (tile) movePhoto(draggedPhoto, tile.dataset.photoId);
};
$('#photo-previews').onpointerup = $('#photo-previews').onpointercancel = () => { draggedPhoto = null; };
function resetComposer() {
  composerRequestId = null;
  form.reset(); delete form.dataset.id;
  clearPreview(); form.querySelector('[type=submit]').textContent = 'Add';
  for (const id of ['cancel-edit', 'delete-item', 'remove-photo']) $('#' + id).hidden = true;
  $('#composer-error').textContent = '';
}
function setComposerBusy(busy) {
  composerBusy = busy;
  for (const control of form.elements) control.disabled = busy;
  form.setAttribute('aria-busy', String(busy));
}
$('#cancel-edit').onclick = async () => {
  if (composerBusy) return;
  resetComposer(); await load();
};
$('#remove-photo').onclick = () => {
  field('image').value = ''; clearPreview();
};
form.onsubmit = async event => {
  event.preventDefault(); if (composerBusy) return;
  composerRequestId ||= crypto.randomUUID();
  const body = {request_id: composerRequestId, title: field('title').value, description: field('description').value, price: field('price').value, links: field('links').value.split(/\n+/).map(value => value.trim()).filter(Boolean)};
  const editing = form.dataset.id;
  setComposerBusy(true); $('#composer-error').textContent = ''; showNotice('');
  try {
    if (!body.title.trim()) throw new Error('Enter a title.');
    if (composerPhotos.length > 20) throw new Error('Use no more than 20 photos.');
    if (composerPhotos.some(photo => photo.file?.size > 8000000)) throw new Error('Photo must be under 8 MB.');
    if (body.links.length > 20) throw new Error('Use no more than 20 links.');
    for (const url of body.links) if (!service(url)) throw new Error('Links must start with http:// or https://.');
    const response = await fetch(editing ? '/api/items/' + editing : '/api/wishlists/' + encodeURIComponent(username) + '/items', {
      method: editing ? 'PATCH' : 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error(response.status === 401 ? 'Please sign in again.' : response.status === 403 ? 'This is not your wishlist.' : 'Could not save. Check the fields and links.');
    const item = await response.json();
    form.dataset.id = item.id; form.querySelector('[type=submit]').textContent = 'Save';
    $('#cancel-edit').hidden = false; $('#delete-item').hidden = false;
    // A previous upload may have committed even if its response was lost.
    const remoteIds = new Set((item.photos || []).map(photo => photo.id));
    for (const photo of composerPhotos) if (remoteIds.has(photo.id)) photo.persisted = true;
    const saveOrder = async () => {
      const response = await fetch('/api/items/' + item.id + '/images', {
        method: 'PATCH', headers: {'content-type': 'application/json'},
        body: JSON.stringify({ids: composerPhotos.filter(photo => photo.persisted).map(photo => photo.id)})
      });
      if (!response.ok) throw new Error('Gift saved, but the photo order was not saved. Try Save again.');
      await response.json();
    };
    await saveOrder();
    for (const photo of composerPhotos) {
      if (photo.persisted) continue;
      const data = new FormData(); data.append('file', photo.file);
      const uploaded = await fetch('/api/items/' + item.id + '/images/' + photo.id, {method: 'PUT', body: data});
      if (!uploaded.ok) throw new Error('Gift saved, but the photo was not saved. Check the file and try Save again.');
      await uploaded.json(); photo.persisted = true;
    }
    await saveOrder();
    resetComposer(); showNotice(editing ? 'Gift updated.' : 'Gift added.'); await load();
  } catch (error) { $('#composer-error').textContent = error instanceof TypeError ? 'Connection failed. Your form is kept; please try again.' : error.message; }
  finally { setComposerBusy(false); }
};
$('#delete-item').onclick = async () => {
  if (composerBusy || !form.dataset.id || !confirm('Delete this gift? Its reservation will also be removed.')) return;
  setComposerBusy(true);
  try {
    const response = await fetch('/api/items/' + form.dataset.id, {method: 'DELETE'});
    if (!response.ok) throw new Error();
    resetComposer(); showNotice('Gift deleted.'); await load();
  } catch { $('#composer-error').textContent = 'Could not delete the gift. Please try again.'; }
  finally { setComposerBusy(false); }
};
field('image').onchange = () => {
  const files = [...field('image').files]; field('image').value = '';
  if (composerPhotos.length + files.length > 20) { $('#composer-error').textContent = 'Use no more than 20 photos.'; return; }
  for (const file of files) composerPhotos.push({id: crypto.randomUUID(), file, url: URL.createObjectURL(file), persisted: false});
  renderPhotos();
};
function startEdit(item) {
  closeReservation(); resetComposer(); form.hidden = false; form.dataset.id = item.id;
  for (const name of ['title', 'description', 'price']) field(name).value = item[name] || '';
  field('links').value = (item.links || []).join('\n');
  composerPhotos = (item.photos || []).map(photo => ({...photo, persisted: true})); renderPhotos();
  $('#cancel-edit').hidden = false; $('#delete-item').hidden = false;
  form.querySelector('[type=submit]').textContent = 'Save';
  form.scrollIntoView({block: 'start'}); field('title').focus({preventScroll: true});
}
load();
