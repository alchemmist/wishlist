const {test} = require('node:test');
const assert = require('node:assert/strict');
const sharp = require('sharp');
const {createServer} = require('./server.cjs');
test('lossless master, alpha, sizes, previews and no URL fetching', async () => {
  const server = createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const input = await sharp({create:{width: 1000,height: 2000,channels:4,background:{r:15,g:40,b:250,alpha:.5}}}).png().toBuffer();
    const [master, preview] = await Promise.all(['/master','/preview'].map(async path => {
      const response = await fetch(base + path, {method:'POST',body:input}); assert.equal(response.status,200);
      assert.equal(response.headers.get('content-type'),'image/webp'); return Buffer.from(await response.arrayBuffer());
    }));
    const meta = await sharp(master).metadata(); assert.equal(meta.width,1000); assert.equal(meta.height,2000); assert.equal(meta.format,'webp');
    assert.deepEqual(await sharp(master).raw().toBuffer(),await sharp(input).raw().toBuffer());
    const thumb = await sharp(preview).metadata(); assert.equal(thumb.width,480); assert.equal(thumb.height,960);
    assert.ok(preview.length < input.length); // Display derivative, not the pixel-perfect master.
    const oriented = await sharp({create:{width:40,height:20,channels:3,background:'red'}}).jpeg().withMetadata({orientation:6}).toBuffer();
    const orientedResponse = await fetch(base + '/master',{method:'POST',body:oriented});
    const orientedMaster = Buffer.from(await orientedResponse.arrayBuffer());
    const orientedMeta = await sharp(orientedMaster).metadata();
    assert.equal(orientedMeta.width,20); assert.equal(orientedMeta.height,40); assert.equal(orientedMeta.exif,undefined);
    assert.deepEqual(await sharp(orientedMaster).raw().toBuffer(),await sharp(oriented).autoOrient().raw().toBuffer());
    const gif = await sharp(Buffer.concat([Buffer.alloc(16*16*3,50),Buffer.alloc(16*16*3,200)]),{raw:{width:16,height:32,channels:3,pageHeight:16}}).gif({delay:[100,200],loop:2}).toBuffer();
    const animatedResponse = await fetch(base + '/master',{method:'POST',body:gif});
    const animated = Buffer.from(await animatedResponse.arrayBuffer());
    const animation = await sharp(animated,{animated:true}).metadata();
    assert.equal(animation.pages,2); assert.equal(animation.pageHeight,16); assert.equal(animation.loop,2); assert.deepEqual(animation.delay,[100,200]);
    assert.deepEqual(await sharp(animated,{animated:true}).ensureAlpha().raw().toBuffer(),await sharp(gif,{animated:true}).ensureAlpha().raw().toBuffer());
    assert.equal((await fetch(base + '/master?url=http://example.com',{method:'POST',body:input})).status,404);
    assert.equal((await fetch(base + '/master',{method:'POST',body:'<svg></svg>'})).status,422);
  } finally { await new Promise(resolve => server.close(resolve)); }
});
