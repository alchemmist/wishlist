import mediumZoom from './vendor/zoom.js';
import {tracedFetch as fetch, beginSpan, pageSpan, routeOf, traceHeader, rememberRequest} from './telemetry.js';

const $ = selector => document.querySelector(selector);
const form = $('#composer');
const field = name => form.elements.namedItem(name);
let username;
try { username = decodeURIComponent(location.pathname.replace(/^\//, '').replace(/\/$/, '')); }
catch { username = ''; }
let zoom, current;
let composerPhotos = [];
let progressTimers = [];
let composerLinks = [];
let saveController = null;
let clipboardBusy = false, composerRevision = 0;
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
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault(); image.parentElement.dispatchEvent(new CustomEvent('gallery-step', {detail: event.key === 'ArrowLeft' ? -1 : 1}));
      }
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
    const image = el('img', null, 'product-photo');
    image.src = photos[0].url; image.alt = item.title;
    image.loading = 'lazy'; image.draggable = false;
    image.onerror = () => { image.hidden = true; };
    figure.append(image);
    if (photos.length > 1) {
      figure.classList.add('photo-story');
      const indicators = el('div', null, 'photo-story-bars');
      indicators.setAttribute('aria-hidden', 'true');
      for (let index = 0; index < photos.length; index++) indicators.append(el('span'));
      let active = -1, touchStart = null, swiped = false, lastMouse = null;
      const show = index => {
        if (zoomOpening || document.body.hasAttribute('data-hc-zoom-active')) return;
        const next = (index + photos.length) % photos.length;
        if (active === next) return;
        indicators.children[active]?.classList.remove('active');
        active = next;
        image.src = photos[active].url; image.hidden = false;
        image.alt = active ? item.title + ' — photo ' + (active + 1) : item.title;
        image.setAttribute('aria-label', 'Enlarge photo: ' + image.alt);
        figure.dataset.photoIndex = active;
        indicators.children[active].classList.add('active');
      };
      figure.onpointermove = event => {
        if (event.pointerType !== 'mouse') return;
        // Focus/scroll in WebKit can emit a stationary pointermove; do not
        // overwrite a keyboard/swipe selection unless the mouse really moved.
        if (lastMouse && lastMouse.x === event.clientX && lastMouse.y === event.clientY) return;
        lastMouse = {x: event.clientX, y: event.clientY};
        const box = figure.getBoundingClientRect();
        show(Math.max(0, Math.min(photos.length - 1, Math.floor((event.clientX - box.left) / box.width * photos.length))));
      };
      figure.onpointerdown = event => { if (event.pointerType !== 'mouse') { touchStart = event.clientX; swiped = false; } };
      figure.onpointerup = event => {
        if (touchStart === null) return;
        const distance = event.clientX - touchStart; touchStart = null;
        if (Math.abs(distance) > 25) { swiped = true; show(active + (distance < 0 ? 1 : -1)); }
      };
      figure.onpointercancel = () => { touchStart = null; };
      figure.addEventListener('click', event => { if (swiped) { event.preventDefault(); event.stopPropagation(); swiped = false; } }, true);
      figure.addEventListener('gallery-step', event => show(active + event.detail));
      figure.append(indicators); show(0);
    }
  } else node.classList.add('no-photo');
  const content = el('div', null, 'gift-content');
  const heading = el('h2', item.title);
  const titleRow = el('div', null, 'gift-title'); titleRow.append(heading);
  if (item.mine) {
    const edit = el('button', null, 'edit-item');
    edit.type = 'button'; edit.setAttribute('aria-label', 'Edit ' + item.title);
    edit.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4l10-10-4-4L4 16z"/><path d="m12.5 6.5 4 4"/></svg>';
    edit.onclick = () => { if (!composerBusy) startEdit(item); };
    const remove = el('button', null, 'delete-item');
    remove.type = 'button'; remove.setAttribute('aria-label', 'Delete ' + item.title);
    remove.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg>';
    remove.onclick = () => deleteGift(item.id);
    titleRow.append(edit, remove);
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
function showNotice(message) {
  $('#notice').textContent = message;
  if (!message) return;
  openSaveProgress(); $('#save-progress').dataset.state = 'error';
  saveStatus('Could not finish', ''); $('#save-elapsed').textContent = '';
  $('#stop-saving').hidden = true; $('#dismiss-progress').hidden = false;
}
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
    tile.setAttribute('role', 'listitem'); tile.draggable = !composerBusy; tile.tabIndex = 0;
    tile.setAttribute('aria-label', 'Photo ' + (index + 1));
    tile.onpointerdown = event => {
      if (composerBusy || event.target.closest('button') || event.pointerType === 'mouse') return;
      tile.focus({preventScroll: true}); draggedPhoto = photo.id;
      $('#photo-previews').setPointerCapture(event.pointerId);
    };
    tile.onkeydown = event => {
      if (event.target !== tile || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      movePhoto(photo.id, composerPhotos[index + (event.key === 'ArrowLeft' ? -1 : 1)]?.id);
      $('#photo-previews [data-photo-id="' + photo.id + '"]')?.focus();
    };
    const image = el('img'); image.src = photo.url; image.alt = 'Photo ' + (index + 1); image.draggable = false; image.loading = 'lazy'; image.decoding = 'async';
    if (!index) image.id = 'photo-preview';
    const remove = el('button', null, 'photo-remove');
    remove.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg>'; remove.type = 'button';
    remove.setAttribute('aria-label', 'Remove photo ' + (index + 1));
    remove.onclick = () => {
      if (composerBusy) return;
      if (photo.file) URL.revokeObjectURL(photo.url);
      composerPhotos = composerPhotos.filter(value => value !== photo); renderPhotos();
    };
    tile.ondragstart = event => { if (composerBusy) { event.preventDefault(); return; } draggedPhoto = photo.id; event.dataTransfer.setData('text/plain', photo.id); event.dataTransfer.effectAllowed = 'move'; };
    tile.ondragover = event => event.preventDefault();
    tile.ondrop = event => { event.preventDefault(); movePhoto(event.dataTransfer.getData('text/plain'), photo.id); draggedPhoto = null; };
    tile.ondragend = () => { draggedPhoto = null; };
    tile.append(image, remove); return tile;
  }));
}
$('#photo-previews').onpointermove = event => {
  if (!draggedPhoto || composerBusy) return;
  const tile = document.elementFromPoint(event.clientX, event.clientY)?.closest('.photo-tile');
  if (tile) movePhoto(draggedPhoto, tile.dataset.photoId);
};
$('#photo-previews').onpointerup = $('#photo-previews').onpointercancel = () => { draggedPhoto = null; };
function resetComposer() {
  if (!composerBusy) { for (const timer of progressTimers) clearTimeout(timer); progressTimers = []; delete $('#save-progress').dataset.open; $('#save-progress').hidden = true; }
  composerRequestId = null; composerRevision++; $('#photo-input-status').textContent = '';
  form.reset(); delete form.dataset.id; composerLinks = []; renderLinkBadges();
  clearPreview(); form.querySelector('[type=submit]').textContent = 'Add';
  for (const id of ['cancel-edit', 'delete-item']) $('#' + id).hidden = true;
  $('#composer-error').textContent = ''; resizeComposerText();
}
function setComposerBusy(busy) {
  composerBusy = busy;
  for (const control of form.elements) control.disabled = busy && control.id !== 'stop-saving';
  form.setAttribute('aria-busy', String(busy));
}
$('#cancel-edit').onclick = () => {
  if (composerBusy) return;
  resetComposer();
};
function openSaveProgress() {
  for (const timer of progressTimers) clearTimeout(timer);
  progressTimers = [];
  $('#save-progress').hidden = false; $('#dismiss-progress').hidden = true;
  requestAnimationFrame(() => { $('#save-progress').dataset.open = ''; });
}
function completeSaveProgress() {
  $('#save-progress').dataset.state = 'success'; saveStatus('Saved', '', 100);
  progressTimers.push(setTimeout(() => {
    delete $('#save-progress').dataset.open;
    progressTimers.push(setTimeout(() => { $('#save-progress').hidden = true; }, 350));
  }, 900));
}
function updateSavedCard(item) {
  const previous = $('#items [data-id="' + item.id + '"]');
  const node = card(item);
  if (previous) previous.replaceWith(node); else $('#items').append(node);
  $('#empty').hidden = true; ensureZoom();
}
function saveStatus(message, detail = '', percent = null) {
  $('#save-status').textContent = message; $('#save-detail').textContent = detail;
  if (percent === null) $('#save-meter').removeAttribute('value');
  else $('#save-meter').value = percent;
}
function uploadPhoto(itemId, photo, signal, onProgress, onReceived, parent) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const url = '/api/items/' + itemId + '/images/' + photo.id;
    const span = beginSpan('browser.upload', parent, {route: routeOf(url), method: 'PUT'});
    const cleanup = () => signal.removeEventListener('abort', abort);
    const abort = () => { span.end({error: true}); xhr.abort(); cleanup(); reject(new DOMException('Saving stopped', 'AbortError')); };
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, {once: true});
    xhr.open('PUT', url); xhr.setRequestHeader('traceparent', traceHeader(span.context)); rememberRequest(url, parent);
    xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(event.loaded / event.total); };
    xhr.upload.onload = onReceived;
    xhr.onload = () => {
      span.end({status: xhr.status, error: xhr.status >= 400}); cleanup();
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error('Gift saved, but the photo was not saved (HTTP ' + xhr.status + '). ' +
          (xhr.status === 413 ? 'The proxy rejected the upload. Your files are kept; try Save again.' : 'Check the file or connection and try Save again.'))); return;
      }
      try { JSON.parse(xhr.responseText); resolve(); }
      catch { reject(new Error('The server returned an invalid upload response. Your files are kept; try Save again.')); }
    };
    xhr.onerror = () => { span.end({error: true}); cleanup(); reject(new TypeError('Upload connection failed')); };
    const data = new FormData(); data.append('file', photo.file); xhr.send(data);
  });
}
$('#stop-saving').onclick = () => saveController?.abort();
$('#dismiss-progress').onclick = () => { delete $('#save-progress').dataset.open; progressTimers.push(setTimeout(() => { $('#save-progress').hidden = true; }, 350)); };
addEventListener('beforeunload', event => { if (saveController) { event.preventDefault(); event.returnValue = ''; } });
form.onsubmit = async event => {
  event.preventDefault(); if (composerBusy) return;
  if (clipboardBusy) { $('#photo-input-status').textContent = 'Please wait for the clipboard image to finish loading.'; return; }
  if (!commitLinks()) return;
  composerRequestId ||= crypto.randomUUID();
  const body = {request_id: composerRequestId, title: field('title').value, description: field('description').value, price: field('price').value, links: [...composerLinks]};
  const editing = form.dataset.id;
  saveController = new AbortController(); const controller = saveController;
  const started = Date.now();
  const saveSpan = beginSpan('browser.save'); let saveFailed = false;
  openSaveProgress(); $('#save-progress').dataset.state = 'saving'; $('#stop-saving').hidden = false;
  const submitButton = form.querySelector('[type=submit]'); submitButton.textContent = 'Saving…';
  saveStatus(editing ? 'Saving changes…' : 'Creating gift…', 'Your form and selected photos are kept until saving finishes.');
  const tick = () => { const seconds = Math.floor((Date.now() - started) / 1000); $('#save-elapsed').textContent = 'Elapsed ' + Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0'); };
  tick(); const timer = setInterval(tick, 1000);
  setComposerBusy(true); $('#composer-error').textContent = ''; showNotice('');
  try {
    if (!body.title.trim()) throw new Error('Enter a title.');
    if (body.links.length > 20) throw new Error('Use no more than 20 links.');
    for (const url of body.links) if (!service(url)) throw new Error('Links must start with http:// or https://.');
    const response = await fetch(editing ? '/api/items/' + editing : '/api/wishlists/' + encodeURIComponent(username) + '/items', {
      signal: controller.signal, method: editing ? 'PATCH' : 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body)
    }, saveSpan.context);
    if (!response.ok) throw new Error(response.status === 401 ? 'Please sign in again.' : response.status === 403 ? 'This is not your wishlist.' : 'Could not save. Check the fields and links.');
    const item = await response.json();
    form.dataset.id = item.id;
    $('#cancel-edit').hidden = false; $('#delete-item').hidden = false;
    // A previous upload may have committed even if its response was lost.
    const remoteIds = new Set((item.photos || []).map(photo => photo.id));
    for (const photo of composerPhotos) if (remoteIds.has(photo.id)) photo.persisted = true;
    const saveOrder = async () => {
      const response = await fetch('/api/items/' + item.id + '/images', {
        signal: controller.signal, method: 'PATCH', headers: {'content-type': 'application/json'},
        body: JSON.stringify({ids: composerPhotos.filter(photo => photo.persisted).map(photo => photo.id)})
      }, saveSpan.context);
      if (!response.ok) throw new Error('Gift saved, but the photo order was not saved. Try Save again.');
      item.photos = (await response.json()).photos; item.image = item.photos[0]?.url || null;
    };
    const sameOrder = ids => ids.length === (item.photos || []).length && ids.every((id, index) => id === item.photos[index].id);
    const persistedIds = composerPhotos.filter(photo => photo.persisted).map(photo => photo.id);
    if (!sameOrder(persistedIds)) { saveStatus('Updating gallery…'); await saveOrder(); }
    const pending = composerPhotos.filter(photo => !photo.persisted);
    const totalBytes = pending.reduce((total, photo) => total + photo.file.size, 0);
    const sent = new Map(); let completed = 0, cursor = 0;
    const formatBytes = value => value >= 1024 * 1024 ? (value / (1024 * 1024)).toFixed(1) + ' MB' : Math.round(value / 1024) + ' KB';
    const update = (photo, ratio, received = false) => {
      sent.set(photo.id, photo.file.size * ratio);
      const transferred = [...sent.values()].reduce((sum, bytes) => sum + bytes, 0);
      saveStatus((received ? 'Saving photos to storage' : 'Uploading photos') + ' — ' + completed + ' of ' + pending.length + ' saved',
        photo.file.name + ' · ' + formatBytes(transferred) + ' / ' + formatBytes(totalBytes), received ? null : (totalBytes ? transferred / totalBytes * 100 : 0));
      const tile = $('#photo-previews [data-photo-id="' + photo.id + '"]');
      if (tile) tile.dataset.upload = received ? 'Saving…' : 'Uploading ' + Math.round(ratio * 100) + '%';
    };
    const worker = async () => {
      while (cursor < pending.length) {
        if (controller.signal.aborted) throw new DOMException('Saving stopped', 'AbortError');
        const photo = pending[cursor++]; update(photo, 0);
        await uploadPhoto(item.id, photo, controller.signal, ratio => update(photo, ratio), () => update(photo, 1, true), saveSpan.context);
        photo.persisted = true; completed++;
        const tile = $('#photo-previews [data-photo-id="' + photo.id + '"]');
        if (tile) tile.dataset.upload = 'Saved';
      }
    };
    await Promise.all(Array.from({length: Math.min(3, pending.length)}, worker));
    if (pending.length) { saveStatus('Finishing…'); await saveOrder(); }
    const renderSpan = beginSpan('browser.render', saveSpan.context);
    updateSavedCard(item); resetComposer(); renderSpan.end(); completeSaveProgress();
  } catch (error) {
    saveFailed = true; controller.abort();
    const stopped = error.name === 'AbortError';
    saveStatus(stopped ? 'Saving stopped' : 'Saving could not finish', (stopped ? '' : error.message + ' ') + 'Your form is kept. Some photos may already be saved; retry resumes without duplicates.');
    $('#save-progress').dataset.state = 'error';
    $('#composer-error').textContent = stopped ? 'Saving stopped. Your changes and selected files are kept. Press Save to resume.' : error instanceof TypeError ? 'Connection failed. Your form is kept; please try again.' : error.message;
  } finally {
    $('#dismiss-progress').hidden = !saveFailed; saveSpan.end({error: saveFailed}); clearInterval(timer); saveController = null; $('#stop-saving').hidden = true;
    for (const tile of document.querySelectorAll('.photo-tile')) delete tile.dataset.upload;
    submitButton.textContent = form.dataset.id ? 'Save' : 'Add'; setComposerBusy(false);
  }
};
async function deleteGift(id) {
  if (composerBusy || !id || !confirm('Delete this gift? Its reservation will also be removed.')) return;
  setComposerBusy(true);
  try {
    const response = await fetch('/api/items/' + id, {method: 'DELETE'});
    if (!response.ok) throw new Error();
    if (String(form.dataset.id) === String(id)) resetComposer();
    showNotice(''); await load();
  } catch { showNotice('Could not delete the gift. Please try again.'); }
  finally { setComposerBusy(false); }
}
$('#delete-item').onclick = () => deleteGift(form.dataset.id);
function renderLinkBadges() {
  $('#link-badges').replaceChildren(...composerLinks.map(url => {
    const info = service(url);
    const badge = el('button', null, 'link-badge'); badge.type = 'button';
    badge.title = url; badge.setAttribute('aria-label', 'Edit link ' + url);
    if (info && info.kind !== 'web') {
      const image = el('img'); image.src = '/static/assets/' + info.kind + '.webp'; image.alt = ''; badge.append(image);
    } else badge.textContent = (info?.label || '?').slice(0, 1).toUpperCase();
    const remove = () => { if (composerBusy) return; composerLinks = composerLinks.filter(value => value !== url); renderLinkBadges(); };
    badge.onclick = () => {
      if (composerBusy || !commitLinks()) return;
      remove(); field('links').value = url; field('links').focus();
    };
    badge.onkeydown = event => {
      if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); remove(); field('links').focus(); }
    };
    return badge;
  }));
  $('#link-badges').scrollLeft = $('#link-badges').scrollWidth;
}
function commitLinks(value = field('links').value) {
  const values = value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  if (values.some(url => url.length > 2048 || !service(url))) {
    $('#composer-error').textContent = 'Links must start with http:// or https://.'; return false;
  }
  const next = [...new Set([...composerLinks, ...values])];
  if (next.length > 20) { $('#composer-error').textContent = 'Use no more than 20 links.'; return false; }
  composerLinks = next; field('links').value = ''; renderLinkBadges();
  $('#composer-error').textContent = ''; return true;
}
field('links').onkeydown = event => {
  if (event.isComposing || composerBusy) return;
  if (event.key === 'Enter') { event.preventDefault(); commitLinks(); }
  else if (event.key === 'Backspace' && !field('links').value && composerLinks.length) {
    event.preventDefault(); field('links').value = composerLinks.pop(); renderLinkBadges();
  }
};
field('links').onpaste = event => {
  const text = event.clipboardData.getData('text');
  if (text.includes('\n')) { event.preventDefault(); if (!commitLinks(text)) field('links').value = text; }
};
function addPhotos(files) {
  if (composerBusy) return;
  for (const file of files) composerPhotos.push({id: crypto.randomUUID(), file, url: URL.createObjectURL(file), persisted: false});
  renderPhotos();
}
field('image').onchange = () => {
  const files = [...field('image').files]; field('image').value = ''; addPhotos(files);
};
const photoDrop = $('.photo-field');
$('#choose-photos').onclick = () => { if (!composerBusy) field('image').click(); };
const clipboardTypes = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif'};
$('#paste-photos').onclick = async () => {
  if (composerBusy || clipboardBusy) return;
  const revision = composerRevision;
  const button = $('#paste-photos');
  const status = message => { if (revision === composerRevision) $('#photo-input-status').textContent = message; };
  if (!navigator.clipboard?.read) {
    status('Press Ctrl+V / ⌘V here to paste an image. Direct clipboard access is unavailable in this browser.'); return;
  }
  clipboardBusy = true; button.disabled = true; button.setAttribute('aria-busy', 'true');
  button.querySelector('.photo-source-label').textContent = 'Reading…'; status('Reading clipboard…');
  try {
    const items = await navigator.clipboard.read();
    const files = [];
    for (const item of items) {
      const type = item.types.find(type => clipboardTypes[type]);
      if (type) files.push(new File([await item.getType(type)], 'clipboard-' + Date.now() + '-' + files.length + '.' + clipboardTypes[type], {type}));
    }
    if (revision !== composerRevision) return;
    if (!files.length) { status('No image in the clipboard. Copy an image and try again.'); return; }
    addPhotos(files); status(files.length === 1 ? 'Image added from clipboard.' : files.length + ' images added from clipboard.');
  } catch {
    status('Clipboard access was denied or unavailable. Press Ctrl+V / ⌘V here to paste an image.');
  } finally {
    clipboardBusy = false; button.disabled = composerBusy; button.removeAttribute('aria-busy');
    button.querySelector('.photo-source-label').textContent = 'Paste';
  }
};
photoDrop.addEventListener('paste', event => {
  if (composerBusy || clipboardBusy) return;
  const data = event.clipboardData;
  const incoming = data?.files?.length ? [...data.files] : [...(data?.items || [])].filter(item => item.kind === 'file').map(item => item.getAsFile()).filter(Boolean);
  const files = incoming.filter(file => clipboardTypes[file.type]);
  if (!files.length) { $('#photo-input-status').textContent = 'No image in the clipboard. Copy an image and try again.'; return; }
  event.preventDefault(); addPhotos(files);
  $('#photo-input-status').textContent = files.length === 1 ? 'Image pasted.' : files.length + ' images pasted.';
});
photoDrop.ondragover = event => { event.preventDefault(); if (!composerBusy) photoDrop.classList.add('is-dragover'); };
photoDrop.ondragleave = () => photoDrop.classList.remove('is-dragover');
photoDrop.ondrop = event => { event.preventDefault(); photoDrop.classList.remove('is-dragover'); addPhotos([...event.dataTransfer.files]); };
function resizeComposerText() {
  for (const textarea of form.querySelectorAll('textarea')) {
    textarea.style.height = 'auto'; textarea.style.height = textarea.scrollHeight + 'px';
  }
}
form.addEventListener('input', resizeComposerText);
addEventListener('resize', resizeComposerText);
function startEdit(item) {
  closeReservation(); resetComposer(); form.hidden = false; form.dataset.id = item.id;
  for (const name of ['title', 'description', 'price']) field(name).value = item[name] || '';
  composerLinks = [...(item.links || [])]; renderLinkBadges();
  composerPhotos = (item.photos || []).map(photo => ({...photo, persisted: true})); renderPhotos();
  $('#cancel-edit').hidden = false; $('#delete-item').hidden = false;
  form.querySelector('[type=submit]').textContent = 'Save';
  resizeComposerText();
  form.scrollIntoView({block: 'start'}); field('title').focus({preventScroll: true});
}
load();
