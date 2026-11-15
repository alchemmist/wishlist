import mediumZoom from './vendor/zoom.js';

const $ = selector => document.querySelector(selector);
const form = $('#composer');
const field = name => form.elements.namedItem(name);
let username;
try { username = decodeURIComponent(location.pathname.replace(/^\//, '').replace(/\/$/, '')); }
catch { username = ''; }
let zoom, current, previewURL;
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
  if (item.image) {
    const image = el('img', null, 'product-photo');
    image.src = item.image; image.alt = item.title;
    image.onerror = () => { image.hidden = true; node.classList.add('no-photo'); };
    figure.append(image);
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
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = null; $('#photo-preview').hidden = true; $('#photo-preview').removeAttribute('src');
}
function resetComposer() {
  composerRequestId = null;
  form.reset(); delete form.dataset.id; delete form.dataset.removeImage;
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
  field('image').value = ''; clearPreview(); form.dataset.removeImage = 'true'; $('#remove-photo').hidden = true;
};
form.onsubmit = async event => {
  event.preventDefault(); if (composerBusy) return;
  const file = field('image').files[0];
  composerRequestId ||= crypto.randomUUID();
  const body = {request_id: composerRequestId, title: field('title').value, description: field('description').value, price: field('price').value, links: field('links').value.split(/\n+/).map(value => value.trim()).filter(Boolean)};
  const editing = form.dataset.id;
  setComposerBusy(true); $('#composer-error').textContent = ''; showNotice('');
  try {
    if (!body.title.trim()) throw new Error('Enter a title.');
    if (file && file.size > 8000000) throw new Error('Photo must be under 8 MB.');
    if (body.links.length > 20) throw new Error('Use no more than 20 links.');
    for (const url of body.links) if (!service(url)) throw new Error('Links must start with http:// or https://.');
    const response = await fetch(editing ? '/api/items/' + editing : '/api/wishlists/' + encodeURIComponent(username) + '/items', {
      method: editing ? 'PATCH' : 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error(response.status === 401 ? 'Please sign in again.' : response.status === 403 ? 'This is not your wishlist.' : 'Could not save. Check the fields and links.');
    const item = await response.json();
    form.dataset.id = item.id; form.querySelector('[type=submit]').textContent = 'Save';
    $('#cancel-edit').hidden = false; $('#delete-item').hidden = false;
    if (file || form.dataset.removeImage) {
      let uploaded;
      if (file) {
        const data = new FormData(); data.append('file', file);
        uploaded = await fetch('/api/items/' + item.id + '/image', {method: 'POST', body: data});
      } else uploaded = await fetch('/api/items/' + item.id + '/image', {method: 'DELETE'});
      if (!uploaded.ok) throw new Error('Gift saved, but the photo was not saved. Check the file and try Save again.');
    }
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
  clearPreview(); const file = field('image').files[0];
  if (!file) return;
  delete form.dataset.removeImage;
  $('#photo-preview').src = previewURL = URL.createObjectURL(file); $('#photo-preview').hidden = false; $('#remove-photo').hidden = false;
};
function startEdit(item) {
  closeReservation(); resetComposer(); form.hidden = false; form.dataset.id = item.id;
  for (const name of ['title', 'description', 'price']) field(name).value = item[name] || '';
  field('links').value = (item.links || []).join('\n');
  if (item.image) { $('#photo-preview').src = item.image; $('#photo-preview').hidden = false; $('#remove-photo').hidden = false; }
  $('#cancel-edit').hidden = false; $('#delete-item').hidden = false;
  form.querySelector('[type=submit]').textContent = 'Save';
  form.scrollIntoView({block: 'start'}); field('title').focus({preventScroll: true});
}
load();
