const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');

const photo = { name: 'coffee.webp', mimeType: 'image/webp', buffer: require('node:fs').readFileSync('static/assets/photos/coffee-1.webp') };
const newUser = () => 'user-' + randomUUID().slice(0, 12);
async function signIn(page, username = newUser(), expectedSuccess = true) {
  await page.goto('/');
  await page.getByRole('link', {name: 'Sign in', exact: true}).click();
  await page.getByRole('textbox', {name: 'Username'}).fill(username);
  await page.getByRole('button', {name: 'Continue', exact: true}).click();
  if (expectedSuccess && !username.startsWith('bad-') && username !== 'expired-token') {
    await expect(page).toHaveURL(new RegExp('/' + username + '$'));
    await expect(page.getByRole('form', {name: 'Gift editor'})).toBeVisible();
  }
  return username;
}
async function addGift(page, title = 'Coffee grinder', withPhoto = false) {
  await page.getByRole('textbox', {name: 'Title', exact: true}).fill(title);
  await page.getByRole('textbox', {name: 'Description', exact: true}).fill('First line\nSecond line');
  await page.getByRole('textbox', {name: 'Price', exact: true}).fill('12 000 ₽');
  await page.getByRole('textbox', {name: 'Links, one per line'}).fill('https://ozon.ru/product/test\nhttps://wildberries.ru/catalog/test\nhttps://market.yandex.ru/product/test\nhttps://example.com/gift');
  if (withPhoto) await page.getByLabel('Photo', {exact: true}).setInputFiles(photo);
  await page.getByRole('button', {name: 'Add', exact: true}).click();
  await expect(page.locator('.gift').filter({has: page.getByRole('heading', {name: title, exact: true})})).toBeVisible();
  await expect(page.locator('#composer [type=submit]')).toBeEnabled();
  return page.locator('.gift').filter({has: page.getByRole('heading', {name: title, exact: true})});
}
async function reserve(page, name) {
  await page.getByRole('switch').first().click();
  await page.getByRole('textbox', {name: /Reserve gift|Release gift/}).fill(name);
  await page.getByRole('button', {name: 'Confirm', exact: true}).click();
}

test('OIDC login, empty list, reload, own list link and logout', async ({page}) => {
  const user = await signIn(page);
  await expect(page.locator('#empty')).toContainText('The list is empty');
  await page.reload();
  await expect(page.getByRole('form', {name: 'Gift editor'})).toBeVisible();
  await page.getByRole('button', {name: user, exact: true}).click();
  await expect(page.getByRole('link', {name: 'My wishlist'})).toHaveAttribute('href', '/' + user);
  await page.getByRole('button', {name: 'Log out', exact: true}).click();
  await expect(page).toHaveURL(`http://127.0.0.1:${process.env.E2E_PORT || 19421}/`);
  await expect(page.getByRole('link', {name: 'Sign in', exact: true})).toBeVisible();
  expect(await (await page.request.get('/api/me')).json()).toEqual({user: null});
});

test('create with photo, price and all link badges; zoom; edit; cancel; remove photo; delete', async ({page}) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await signIn(page);
  let gift = await addGift(page, 'Coffee grinder', true);
  await expect(gift.locator('.gift-price')).toHaveText('12 000 ₽');
  for (const name of ['Ozon', 'Wildberries', 'Yandex Market', 'example.com']) {
    await expect(gift.getByRole('link', {name, exact: true})).toHaveAttribute('target', '_blank');
  }
  await expect(gift.locator('.product-photo')).toHaveJSProperty('naturalWidth', 640);
  await gift.getByRole('button', {name: 'Enlarge photo: Coffee grinder'}).click();
  await expect(page.locator('body')).toHaveAttribute('data-hc-zoom-active', '');
  await page.keyboard.press('Escape');
  await expect(page.locator('body')).not.toHaveAttribute('data-hc-zoom-active');
  await page.reload();
  await page.getByRole('button', {name: 'Edit Coffee grinder', exact: true}).click();
  await expect(page.locator('#photo-preview')).toBeVisible();
  await page.getByRole('textbox', {name: 'Title', exact: true}).fill('Unsaved');
  await page.getByRole('button', {name: 'Cancel', exact: true}).click();
  await expect(page.getByRole('textbox', {name: 'Title', exact: true})).toHaveValue('');
  await expect(page.getByRole('heading', {name: 'Coffee grinder', exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Edit Coffee grinder', exact: true}).click();
  await page.getByRole('textbox', {name: 'Title', exact: true}).fill('Updated grinder');
  await page.locator('.photo-tile').first().hover();
  await page.getByRole('button', {name: 'Remove photo 1', exact: true}).click();
  await page.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Updated grinder', exact: true})).toBeVisible();
  await expect(page.locator('.gift .product-photo')).toHaveCount(0);
  await expect(page.locator('.gift')).toHaveCount(1);
  await page.getByRole('button', {name: 'Edit Updated grinder', exact: true}).click();
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', {name: 'Delete gift', exact: true}).click();
  await expect(page.locator('.gift')).toHaveCount(1);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', {name: 'Delete gift', exact: true}).click();
  await expect(page.locator('.gift')).toHaveCount(0);
  await expect(page.locator('#empty')).toContainText('The list is empty');
  expect(errors).toEqual([]);
});

test('public list, reservation/release with Unicode name and wrong-name protection', async ({page, browser}) => {
  const user = await signIn(page); await addGift(page);
  const guestContext = await browser.newContext();
  const guest = await guestContext.newPage();
  await guest.goto('/' + user);
  await expect(guest.getByRole('form', {name: 'Gift editor'})).toBeHidden();
  await expect(guest.getByRole('button', {name: /^Edit /})).toHaveCount(0);
  await reserve(guest, '  Маша Иванова  ');
  await expect(guest.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  await guest.reload();
  await expect(guest.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  await reserve(guest, 'Другой');
  await expect(guest.locator('#name-error')).toHaveText('The name does not match the reservation.');
  await expect(guest.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  await guest.keyboard.press('Escape');
  await expect(guest.getByRole('dialog')).toBeHidden();
  await expect(guest.getByRole('switch')).toBeFocused();
  await reserve(guest, 'МАША ИВАНОВА');
  await expect(guest.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  await guestContext.close();
});

test('other signed-in user cannot mutate a wishlist through UI or API', async ({page, browser}) => {
  const owner = await signIn(page); const gift = await addGift(page);
  const id = await gift.getAttribute('data-id');
  const otherContext = await browser.newContext(); const other = await otherContext.newPage();
  await signIn(other); await other.goto('/' + owner);
  await expect(other.getByRole('form', {name: 'Gift editor'})).toBeHidden();
  expect((await other.request.patch('/api/items/' + id, {data: {title: 'Intruder'}})).status()).toBe(403);
  expect((await other.request.delete('/api/items/' + id)).status()).toBe(403);
  expect((await other.request.post('/api/wishlists/' + owner + '/items', {data: {title: 'Intruder'}})).status()).toBe(403);
  expect((await other.request.post('/api/items/' + id + '/image', {multipart: {file: photo}})).status()).toBe(403);
  await page.reload(); await expect(page.getByRole('heading', {name: 'Coffee grinder', exact: true})).toBeVisible();
  await otherContext.close();
});

test('photo upload failure can be retried without creating a duplicate', async ({page}) => {
  await signIn(page);
  let attempts = 0;
  await page.route('**/api/items/*/images/*', async route => {
    if (++attempts === 1) await route.fulfill({status: 503, json: {detail: 'Storage down'}});
    else await route.continue();
  });
  await page.getByRole('textbox', {name: 'Title', exact: true}).fill('Retry photo');
  await page.getByLabel('Photo', {exact: true}).setInputFiles(photo);
  await page.getByRole('button', {name: 'Add', exact: true}).click();
  await expect(page.locator('#composer-error')).toContainText('Gift saved, but the photo');
  await expect(page.getByRole('textbox', {name: 'Title', exact: true})).toHaveValue('Retry photo');
  await page.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.locator('.gift')).toHaveCount(1);
  await expect(page.locator('.product-photo')).toBeVisible();
  expect(attempts).toBe(2);
});

test('network failure keeps editor data; repeated submit creates one item', async ({page}) => {
  await signIn(page);
  await page.route('**/api/wishlists/*/items', route => route.abort('failed'), {times: 1});
  await page.getByRole('textbox', {name: 'Title', exact: true}).fill('Network retry');
  await page.getByRole('button', {name: 'Add', exact: true}).click();
  await expect(page.locator('#composer-error')).toContainText('Connection failed');
  await expect(page.getByRole('textbox', {name: 'Title', exact: true})).toHaveValue('Network retry');
  await page.route('**/api/wishlists/*/items', async route => { await new Promise(resolve => setTimeout(resolve, 250)); await route.continue(); });
  await page.locator('#composer').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
  await expect(page.locator('.gift')).toHaveCount(1);
  await expect(page.locator('#composer [type=submit]')).toBeEnabled();
});

test('invalid links, oversized photo and invalid image show recoverable errors', async ({page}) => {
  await signIn(page);
  await page.getByRole('textbox', {name: 'Title', exact: true}).fill('Validation');
  await page.getByRole('textbox', {name: 'Links, one per line'}).fill('javascript:alert(1)');
  await page.getByRole('button', {name: 'Add', exact: true}).click();
  await expect(page.locator('#composer-error')).toContainText('http://');
  await expect(page.locator('.gift')).toHaveCount(0);
  await page.getByRole('textbox', {name: 'Links, one per line'}).fill('');
  await page.getByLabel('Photo', {exact: true}).setInputFiles({name: 'big.png', mimeType: 'image/png', buffer: Buffer.alloc(8000001)});
  await page.getByRole('button', {name: 'Add', exact: true}).click();
  await expect(page.locator('#composer-error')).toHaveText('Photo must be under 8 MB.');
  await page.locator('.photo-tile').first().hover();
  await page.getByRole('button', {name: 'Remove photo 1', exact: true}).click();
  await page.getByLabel('Photo', {exact: true}).setInputFiles({name: 'fake.png', mimeType: 'image/png', buffer: Buffer.from('not an image')});
  await page.getByRole('button', {name: 'Add', exact: true}).click();
  await expect(page.locator('#composer-error')).toContainText('Gift saved, but the photo');
  await page.locator('.photo-tile').first().hover();
  await page.getByRole('button', {name: 'Remove photo 1', exact: true}).click();
  await page.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.locator('.gift')).toHaveCount(1);
});

test('public list survives account API failure; list request can be retried; unknown list', async ({page}) => {
  const user = await signIn(page); await addGift(page);
  await page.context().clearCookies();
  await page.route('**/api/me', route => route.abort('failed'));
  await page.goto('/' + user);
  await expect(page.getByRole('heading', {name: 'Coffee grinder', exact: true})).toBeVisible();
  await page.route('**/api/wishlists/' + user, route => route.fulfill({status: 500, body: 'Unavailable'}), {times: 1});
  await page.reload();
  await expect(page.locator('#empty')).toContainText('Could not load');
  await page.getByRole('button', {name: 'Try again', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Coffee grinder', exact: true})).toBeVisible();
  await expect(page.locator('#empty')).toBeHidden();
  await page.goto('/does-not-exist-' + randomUUID());
  await expect(page.locator('#empty')).toHaveText('This wishlist does not exist yet.');
});

test('narrow viewport, long content, accessible controls and bounded reservation popover', async ({page}) => {
  await page.setViewportSize({width: 320, height: 568});
  await signIn(page);
  const title = 'A'.repeat(120);
  await addGift(page, title, true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  for (const link of await page.locator('.gift a').all()) await expect(link).toHaveAttribute('aria-label', /.+/);
  await page.context().clearCookies(); await page.reload();
  await page.getByRole('switch').click();
  await expect(page.getByRole('dialog')).toBeVisible();
  const box = await page.getByRole('dialog').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(320);
  expect(box.y).toBeGreaterThanOrEqual(0); expect(box.y + box.height).toBeLessThanOrEqual(568);
  await page.keyboard.press('Escape'); await expect(page.getByRole('switch')).toBeFocused();
});

test('XSS strings render as text, and unsafe API links are rejected', async ({page}) => {
  const user = await signIn(page);
  const title = '<img src=x onerror="window.__xss=1">';
  await addGift(page, title);
  await expect(page.getByRole('heading', {name: title, exact: true})).toBeVisible();
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();
  expect((await page.request.post('/api/wishlists/' + user + '/items', {data: {title: 'Unsafe', links: ['javascript:alert(1)']}})).status()).toBe(422);
});

for (const username of ['bad-signature', 'bad-nonce', 'bad-audience', 'bad-issuer', 'expired-token', 'bad-subject']) {
  test('OIDC rejects ' + username, async ({page}) => {
    await signIn(page, username);
    await expect(page.locator('body')).toContainText('Sign-in failed');
    expect(await (await page.request.get('/api/me')).json()).toEqual({user: null});
  });
}

test('a lost creation response is safe to retry after the server has saved the item', async ({page}) => {
  const user = await signIn(page);
  await page.route('**/api/wishlists/*/items', async route => {
    const saved = await route.fetch(); expect(saved.status()).toBe(200);
    await route.abort('failed');
  }, {times: 1});
  await page.getByRole('textbox', {name: 'Title', exact: true}).fill('Lost response');
  await page.getByRole('button', {name: 'Add', exact: true}).click();
  await expect(page.locator('#composer-error')).toContainText('Connection failed');
  const before = await (await page.request.get('/api/wishlists/' + user)).json();
  expect(before.items).toHaveLength(1);
  await page.getByRole('button', {name: 'Add', exact: true}).click();
  await expect(page.locator('.gift')).toHaveCount(1);
  expect(await page.locator('.gift').getAttribute('data-id')).toBe(String(before.items[0].id));
});

test('reservation network error is retryable, and stale reservations report a conflict', async ({page}) => {
  await signIn(page); const gift = await addGift(page);
  const id = await gift.getAttribute('data-id');
  await page.context().clearCookies(); await page.reload();
  await page.route('**/api/items/*/reserve', route => route.abort('failed'), {times: 1});
  await reserve(page, 'Guest');
  await expect(page.locator('#name-error')).toContainText('Connection failed');
  await expect(page.getByRole('textbox', {name: 'Reserve gift', exact: true})).toHaveValue('Guest');
  expect((await page.request.post('/api/items/' + id + '/reserve', {data: {name: 'Other guest'}})).status()).toBe(200);
  await page.getByRole('button', {name: 'Confirm', exact: true}).click();
  await expect(page.locator('#name-error')).toContainText('already reserved');
  await page.keyboard.press('Escape'); await page.reload();
  await expect(page.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
});

test('keyboard photo zoom works with reduced motion and immediate Escape', async ({page}) => {
  await page.emulateMedia({reducedMotion: 'reduce'});
  await signIn(page); await addGift(page, 'Keyboard photo', true);
  const image = page.getByRole('button', {name: 'Enlarge photo: Keyboard photo'});
  await image.focus(); await page.keyboard.press('Enter'); await page.keyboard.press('Escape');
  await expect(page.locator('body')).not.toHaveAttribute('data-hc-zoom-active');
  await expect(image).toBeFocused();
});

test('OIDC rejects wrong state and callback replay, and shows a retry link', async ({page}) => {
  await page.goto('/login');
  const url = new URL(page.url());
  await page.goto('/login/callback?code=fake&state=' + url.searchParams.get('state') + '-wrong');
  await expect(page.locator('body')).toContainText('Sign-in failed');
  await expect(page.getByRole('link', {name: 'Try signing in again'})).toBeVisible();
  expect(await (await page.request.get('/api/me')).json()).toEqual({user: null});
  let callback;
  const capture = request => { if (new URL(request.url()).pathname === '/login/callback') callback = request.url(); };
  page.on('request', capture);
  await signIn(page);
  page.off('request', capture);
  expect(callback).toBeTruthy();
  await page.request.post('/logout');
  await page.goto(callback);
  await expect(page.locator('body')).toContainText('Sign-in failed');
  expect(await (await page.request.get('/api/me')).json()).toEqual({user: null});
});

test('case-insensitive username collision cannot take over the existing account', async ({page}) => {
  const user = await signIn(page); await addGift(page);
  await page.context().clearCookies();
  await signIn(page, user.toUpperCase(), false);
  await expect(page.locator('body')).toContainText('This username already belongs to another account');
  expect(await (await page.request.get('/api/me')).json()).toEqual({user: null});
  const data = await (await page.request.get('/api/wishlists/' + user)).json();
  expect(data.owner.username).toBe(user); expect(data.items).toHaveLength(1);
});


test('signed-in reservation toggle uses username without a name dialog and protects other reservations', async ({page, browser}) => {
  const owner = await signIn(page); const gift = await addGift(page);
  const id = await gift.getAttribute('data-id');
  const otherContext = await browser.newContext(); const other = await otherContext.newPage();
  const username = await signIn(other); await other.goto('/' + owner);
  const toggle = other.getByRole('switch');
  const [response] = await Promise.all([
    other.waitForResponse(response => response.url().endsWith('/api/items/' + id + '/reserve')),
    toggle.click(),
  ]);
  expect(await response.json()).toEqual({reserved: true, name: username});
  await expect(other.getByRole('dialog')).toBeHidden();
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await other.reload(); await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  await expect(other.getByRole('dialog')).toBeHidden();
  await toggle.click(); await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await page.reload(); await page.getByRole('switch').click();
  await expect(page.locator('#notice')).toContainText('reserved by someone else');
  await expect(page.getByRole('dialog')).toBeHidden();
  expect((await page.request.post('/api/items/' + id + '/release', {data: {name: username}})).status()).toBe(403);
  await expect(page.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  await otherContext.close();
});

test('signed-in toggle waits for account lookup and recovers from network failure without a dialog', async ({page}) => {
  await signIn(page); await addGift(page);
  let unblock;
  const gate = new Promise(resolve => { unblock = resolve; });
  await page.route('**/api/me', async route => { await gate; await route.continue(); });
  await page.reload();
  await page.route('**/api/items/*/reserve', route => route.abort('failed'), {times: 1});
  await page.getByRole('switch').click();
  await expect(page.getByRole('dialog')).toBeHidden();
  unblock();
  await expect(page.locator('#notice')).toContainText('Connection failed');
  await expect(page.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  await expect(page.getByRole('switch')).toBeEnabled();
  await page.getByRole('switch').click();
  await expect(page.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  await page.getByRole('switch').click();
  await expect(page.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  await expect(page.getByRole('dialog')).toBeHidden();
});

test('multiple photos: append selections, drag order, individual remove, save/reload and cancel', async ({page}) => {
  await signIn(page);
  await page.getByRole('textbox', {name: 'Title', exact: true}).fill('Gallery gift');
  await page.getByLabel('Photo', {exact: true}).setInputFiles([photo, {...photo, name: 'second.webp'}]);
  await expect(page.locator('.photo-tile')).toHaveCount(2);
  await page.getByLabel('Photo', {exact: true}).setInputFiles({...photo, name: 'third.webp'});
  await expect(page.locator('.photo-tile')).toHaveCount(3);
  await page.mouse.move(0, 0);
  await expect(page.locator('.photo-remove').first()).toHaveCSS('opacity', '0');
  await page.locator('.photo-tile').first().hover();
  await expect(page.locator('.photo-remove').first()).toHaveCSS('opacity', '1');
  await expect(page.locator('.photo-tile button')).toHaveCount(3);
  const original = await page.locator('.photo-tile').evaluateAll(tiles => tiles.map(tile => tile.dataset.photoId));
  await page.locator('.photo-tile').first().dragTo(page.locator('.photo-tile').nth(2));
  const initial = await page.locator('.photo-tile').evaluateAll(tiles => tiles.map(tile => tile.dataset.photoId));
  expect(initial).not.toEqual(original);
  await expect(page.locator('.photo-handle,.photo-moves,#remove-photo')).toHaveCount(0);
  await expect(page.locator('.photo-tile').first()).toHaveCSS('cursor', 'grab');
  await expect(page.locator('.photo-remove svg')).toHaveCount(3);
  await page.locator('.photo-tile').first().dragTo(page.locator('.photo-tile').nth(2));
  const order = await page.locator('.photo-tile').evaluateAll(tiles => tiles.map(tile => tile.dataset.photoId));
  await page.locator('.photo-tile').nth(2).hover();
  await page.getByRole('button', {name: 'Remove photo 3', exact: true}).click();
  await expect(page.locator('.photo-tile')).toHaveCount(2);
  await page.getByRole('button', {name: 'Add', exact: true}).click();
  await expect(page.locator('.gift .product-photo')).toHaveCount(1);
  await expect(page.locator('.gift .photo-story-bars span')).toHaveCount(2);
  const user = (await (await page.request.get('/api/me')).json()).user.username;
  let item = (await (await page.request.get('/api/wishlists/' + user)).json()).items[0];
  expect(item.photos.map(photo => photo.id)).toEqual(order.slice(0, 2));
  expect(item.image).toBe(item.photos[0].url);
  await page.reload();
  await page.getByRole('button', {name: 'Edit Gallery gift', exact: true}).click();
  await expect(page.locator('.photo-tile')).toHaveCount(2);
  await page.locator('.photo-tile').nth(1).dragTo(page.locator('.photo-tile').first());
  await page.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.locator('#composer [type=submit]')).toHaveText('Add');
  item = (await (await page.request.get('/api/wishlists/' + user)).json()).items[0];
  expect(item.photos.map(photo => photo.id)).toEqual(order.slice(0, 2).reverse());
  await page.getByRole('button', {name: 'Edit Gallery gift', exact: true}).click();
  await page.locator('.photo-tile').first().hover();
  await page.getByRole('button', {name: 'Remove photo 1', exact: true}).click();
  await page.getByRole('button', {name: 'Cancel', exact: true}).click();
  await expect(page.locator('.gift .product-photo')).toHaveCount(1);
  await expect(page.locator('.gift .photo-story-bars span')).toHaveCount(2);
  await page.getByRole('button', {name: 'Edit Gallery gift', exact: true}).click();
  await page.locator('.photo-tile').first().hover();
  await page.getByRole('button', {name: 'Remove photo 1', exact: true}).click();
  await page.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.locator('.gift .product-photo')).toHaveCount(1);
  await page.reload();
  await expect(page.locator('.gift .product-photo')).toHaveCount(1);
});

test('partial multi-photo upload with a lost response retries without duplicates or reuploading saved photos', async ({page}) => {
  await signIn(page);
  await page.getByRole('textbox', {name: 'Title', exact: true}).fill('Retry gallery');
  await page.getByLabel('Photo', {exact: true}).setInputFiles([photo, {...photo, name: 'second.webp'}]);
  let attempts = 0;
  await page.route('**/api/items/*/images/*', async route => {
    attempts++;
    if (attempts === 2) {
      // Commit via the isolated request context, then lose the browser response.
      // WebKit does not reliably expose intercepted multipart bodies to route.fetch().
      expect((await page.request.put(route.request().url(), {multipart: {file: photo}})).status()).toBe(200);
      await route.abort('failed');
    }
    else await route.continue();
  });
  await page.getByRole('button', {name: 'Add', exact: true}).click();
  await expect(page.locator('#composer-error')).toContainText('Connection failed');
  const user = (await (await page.request.get('/api/me')).json()).user.username;
  const before = (await (await page.request.get('/api/wishlists/' + user)).json()).items[0];
  expect(before.photos).toHaveLength(2);
  await page.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.locator('.gift .product-photo')).toHaveCount(1);
  await expect(page.locator('.gift .photo-story-bars span')).toHaveCount(2);
  expect(attempts).toBe(2);
  const after = (await (await page.request.get('/api/wishlists/' + user)).json()).items;
  expect(after).toHaveLength(1);
  expect(after[0].photos).toEqual(before.photos);
});

test('marketplace photo stories fill the row, track hover zones, loop and zoom the selected photo', async ({page}) => {
  const username = await signIn(page);
  await page.getByRole('textbox', {name: 'Title', exact: true}).fill('Photo stories');
  await page.getByRole('textbox', {name: 'Description', exact: true}).fill('A long description\n'.repeat(15));
  await page.getByLabel('Photo', {exact: true}).setInputFiles([photo, {...photo, name: 'second.webp'}, {...photo, name: 'third.webp'}]);
  await page.getByRole('button', {name: 'Add', exact: true}).click();
  const drawing = page.locator('.gift .drawing');
  const image = drawing.locator('.product-photo');
  await expect(image).toBeVisible();
  await expect(drawing.locator('.product-photo')).toHaveCount(1);
  await expect(drawing.locator('.photo-story-bars span')).toHaveCount(3);
  const dimensions = await page.locator('.gift').evaluate(gift => ({
    drawing: gift.querySelector('.drawing').getBoundingClientRect().height,
    image: gift.querySelector('.product-photo').getBoundingClientRect().height,
    content: gift.querySelector('.gift-content').getBoundingClientRect().height,
    overflow: document.documentElement.scrollWidth > innerWidth,
  }));
  expect(dimensions.drawing).toBeGreaterThanOrEqual(dimensions.content - 1);
  expect(dimensions.image).toBeCloseTo(dimensions.drawing, 0);
  expect(dimensions.overflow).toBe(false);
  const photos = (await (await page.request.get('/api/wishlists/' + username)).json()).items[0].photos;
  const box = await drawing.boundingBox();
  for (const [index, fraction] of [[0, .1], [1, .5], [2, .9], [0, .1]]) {
    await page.mouse.move(box.x + box.width * fraction, box.y + 25);
    await expect(drawing).toHaveAttribute('data-photo-index', String(index));
    await expect(image).toHaveAttribute('src', photos[index].url);
    await expect(drawing.locator('.photo-story-bars .active')).toHaveCount(1);
    await expect(drawing.locator('.photo-story-bars')).toHaveCSS('opacity', '1');
  }
  await image.focus(); await image.press('ArrowLeft');
  await expect(drawing).toHaveAttribute('data-photo-index', '2');
  await image.press('ArrowRight');
  await expect(drawing).toHaveAttribute('data-photo-index', '0');
  await drawing.dispatchEvent('pointerdown', {pointerType: 'touch', clientX: box.x + box.width - 2});
  await drawing.dispatchEvent('pointerup', {pointerType: 'touch', clientX: box.x + 2});
  await expect(drawing).toHaveAttribute('data-photo-index', '1');
  await image.press('Enter');
  await expect(page.locator('body')).toHaveAttribute('data-hc-zoom-active', '');
  await expect(page.locator('.medium-zoom-image--opened')).toHaveAttribute('src', photos[1].url);
  await page.keyboard.press('Escape');
  await expect(page.locator('body')).not.toHaveAttribute('data-hc-zoom-active');
});

test('owner deletes directly next to the pencil; guests cannot; cancellation and network retry are safe', async ({page, browser}) => {
  const username = await signIn(page); await addGift(page, 'Delete directly');
  const titleRow = page.locator('.gift-title');
  await expect(titleRow.getByRole('button', {name: 'Edit Delete directly', exact: true})).toBeVisible();
  const remove = titleRow.getByRole('button', {name: 'Delete Delete directly', exact: true});
  await expect(remove).toBeVisible();
  const guestContext = await browser.newContext(); const guest = await guestContext.newPage();
  await guest.goto('/' + username);
  await expect(guest.locator('.gift-title button')).toHaveCount(0);
  await guestContext.close();
  page.once('dialog', dialog => dialog.dismiss()); await remove.click();
  await expect(page.locator('.gift')).toHaveCount(1);
  await page.route('**/api/items/*', route => route.request().method() === 'DELETE' ? route.abort('failed') : route.continue(), {times: 1});
  page.once('dialog', dialog => dialog.accept()); await remove.click();
  await expect(page.locator('#notice')).toContainText('Could not delete');
  await expect(page.locator('.gift')).toHaveCount(1);
  page.once('dialog', dialog => dialog.accept()); await remove.click();
  await expect(page.locator('.gift')).toHaveCount(0);
  await page.reload(); await expect(page.locator('.gift')).toHaveCount(0);
});
