// Private binary-in/binary-out adapter around Sharp/libvips. No URL fetching,
// user filenames, persistence, application job semaphore or lossy master codec.
const http = require('node:http');
const {mkdtemp, rm, stat} = require('node:fs/promises');
const {createWriteStream, createReadStream} = require('node:fs');
const {pipeline} = require('node:stream/promises');
const {tmpdir} = require('node:os');
const {join} = require('node:path');
const sharp = require('sharp');
sharp.cache({memory: 32, files: 0, items: 32});
function createServer() {
  return http.createServer(async (req, res) => {
    if (req.method === 'GET' && req.url === '/health') { res.end('ok'); return; }
    if (req.method !== 'POST' || !['/master', '/preview'].includes(req.url)) { res.writeHead(404).end(); return; }
    let dir;
    try {
      dir = await mkdtemp(join(tmpdir(), 'wishlist-image-'));
      const input = join(dir, 'input'), output = join(dir, 'output.webp');
      await pipeline(req, createWriteStream(input));
      const started = performance.now();
      let image = sharp(input, {animated: true, limitInputPixels: false, failOn: 'error'}).autoOrient().keepIccProfile();
      const metadata = await image.metadata();
      // Do not enable SVG/PDF or arbitrary libvips loaders via this API.
      if (!['jpeg','png','webp','gif'].includes(metadata.format)) { res.writeHead(415).end(); return; }
      if (req.url === '/preview') image = image.resize({width: 480, height: 960, fit: 'inside', withoutEnlargement: true});
      await image.webp({lossless: true, effort: 4}).toFile(output);
      const info = await stat(output);
      res.writeHead(200, {'Content-Type': 'image/webp', 'Content-Length': info.size, 'Cache-Control': 'no-store', 'X-Image-Process-Ms': (performance.now() - started).toFixed(2)});
      await pipeline(createReadStream(output), res);
    } catch {
      if (!res.headersSent) res.writeHead(422, {'Cache-Control': 'no-store'}).end();
      else res.destroy();
    } finally { if (dir) await rm(dir, {recursive: true, force: true}); }
  });
}
if (require.main === module) {
  const server = createServer();
  server.requestTimeout = 300_000;
  server.listen(Number(process.env.OPTIMIZER_PORT || 8081), '0.0.0.0');
}
module.exports = {createServer};
