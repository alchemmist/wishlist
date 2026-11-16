# Wishlist

Public wishlists on https://wishlist.alchemmist.xyz. How the pieces fit together is in [OVERVIEW.md](OVERVIEW.md).

A signed-in person has one list at `/{username}`. The username comes from Authentik. Anyone can open that page and reserve a gift with a name. Names are unique: the same spelling, ignoring case, is one person. Releasing a gift requires that same name. Signed-in users reserve and release
with a single toggle click, using their Authentik username automatically; only
anonymous visitors are asked to enter a name. The server derives signed-in
names from the session, ignoring a supplied name.

Sign-in is Authentik OIDC. External accounts should be allowed by the Authentik application policy (group `app-wishlist-user`). After login the corner link becomes the Authentik avatar.

Pictures go to the RustFS bucket `wishlist` when `S3_ENDPOINT`, `S3_ACCESS_KEY` and `S3_SECRET_KEY` are set. Without them they stay on the SQLite volume under `/data/media`.

SQLite lives on a single-node volume. The pod is pinned to `socrat-0` for that reason.

## Local development

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PUBLIC_URL=http://localhost:8000 SESSION_SECRET=local-development-only \
  .venv/bin/uvicorn app.main:app --reload
```

For real sign-in, set `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`
and register `${PUBLIC_URL}/login/callback` in Authentik. Access/group policy is
enforced by the Authentik application, not by a group check in this repository.
`SESSION_SECRET` is mandatory with an HTTPS `PUBLIC_URL`; use a strong stable
secret in deployment. S3 settings must be supplied together and the bucket must
already exist.

## Tests and audit

```sh
.venv/bin/python -m pytest -q
npm ci
npx playwright install chromium firefox webkit
npm run test:e2e
```

E2E tests start an isolated app and a local RSA-signing OIDC provider on ports
19421/19422, using a temporary database and local media. They do not need or use
production credentials. Set `PYTHON` to choose the test interpreter, or
`E2E_PORT` to choose another pair of ports. On Linux, use Playwright's
`install --with-deps` option. Failure traces, screenshots and the HTML report
are in ignored `test-results/` and `playwright-report/` directories. JUnit output
is written to `reports/playwright/junit.xml`.

See [AUDIT.md](AUDIT.md) for findings, fixes, test coverage and the remaining
production/infrastructure checks. Back up SQLite before deploying migrations;
use the SQLite backup API rather than copying a live database file without its
WAL.


## CI: Quality Graph

[`qg.yaml`](qg.yaml) is the source of truth for all eleven quality jobs:
graph/workflow validation, Python/JavaScript syntax, the full pytest suite,
Python/npm dependency audits and four browser E2E profiles. Quality Graph 0.2.0
requires the filename `qg.yaml`, not `qg.yml`.

```sh
.venv/bin/qg generate
.venv/bin/qg validate
```

Commit the generated workflows, manifest and `.prettierignore` along with the
declaration. Do not hand-edit generated files. Tests publish structured JUnit
results; Playwright evidence is retained even on test failure.

Deployment remains separate and runs only after successful trusted `main`
checks, using exactly the checked SHA. For a manual release, run the
**Quality Graph** workflow on `main`; it checks everything before deploying.
See [CI.md](CI.md) for the DAG, artifacts, security boundary, bootstrap/probe PR
procedure and the explicit required-check synchronization step.


## Photo galleries

Each gift supports JPEG/PNG/WebP/GIF photos without application-level count,
file-byte or pixel-count caps. Select several
files at once or add more in subsequent selections. In the editor, drag the
photo itself to reorder, with no handles or movement buttons. The first photo
is the cover. A round trash button appears in the upper-right corner on
hover/focus (tap to focus on touch devices). Delete individual photos, then Save. Cancel discards unsaved
editor changes. Existing single-photo gifts migrate automatically without
copying image objects. Upload retries reuse photo IDs and preserve completed
uploads, including when an upload response was lost.


Saved cards show one full-row-height photo, not a large cover plus thumbnails.
On hover, story segments appear across the top: moving the pointer horizontally
selects the corresponding photo without clicking. Touch swipes and focused-photo
Left/Right keys cycle through photos; clicking/Enter still zooms the selected
photo. Owners can delete a gift directly with the trash icon next to its pencil
(confirmation required).


The top editor mirrors a saved card: a dashed photo picker/dropzone with a plus
on the left, ordered miniatures underneath, borderless Title/Price/Description
fields in the content column, and Add/Save in place of the reservation control.
The same layout adapts to mobile; links remain editable below the description.


Paste a URL into Link and press Enter: a round service badge appears on the
left, the input clears and shrinks to leave room for the badges. Click a badge
to edit its URL, press Delete on a focused badge to remove it, or Backspace in
an empty input to edit the last URL. Add/Save includes a valid pending URL too.
Photo, title and action controls share the same top edge on desktop and mobile;
focus outlines stay within that edge. Upload limits are still enforced without
permanent explanatory text in the editor.


Saving shows a floating activity island at the left edge, vertically centered: current stage, transfer
progress/bytes, completed photo count, elapsed time, and thumbnail states. Up to
three files transfer concurrently (a concurrency bound, not a photo-count cap).
Stop saving aborts outstanding browser requests without discarding selected
files; Save retries reconcile already-stored photo IDs and do not duplicate them.
Metadata or photos already committed before stopping are not rolled back.
Large files spool to disk and stream to storage; S3 multipart transfer and media
responses avoid buffering whole large files in application memory. Validation
checks supported image/container structure, not an unbounded decoded pixel array.
Actual disk, browser, network and storage capacities still apply.


The dashed photo picker has two halves: **Files** opens the filesystem picker;
**Paste** reads image data from the clipboard after an explicit click. You can
also focus either half and press Ctrl+V / ⌘V to paste an image. Clipboard images
join the same reorderable/removable upload queue, including when editing.
If direct clipboard access is denied or unsupported, the picker explains the
keyboard-paste fallback. Copy the image itself, not merely its URL; supported
clipboard image types are PNG, JPEG, WebP and GIF. Clipboard contents are never
read automatically on page load.


Metadata-only edits make one PATCH request: unchanged photos are not uploaded,
reordered or deleted, and the list/account are not reloaded. The saved card is
updated from the server response. Success uses an animated checkmark and fade-out,
not a permanent “Gift updated” banner. Reduced-motion users get static feedback.

## Observability

See `OBSERVABILITY.md` for tracing, privacy, private metrics and deployment. The
production chart, dedicated renewable client certificate, Collector job and
Grafana dashboard are maintained in the sibling Homelab repository.

## Image pipeline

`IMAGE_OPTIMIZER_URL` enables the private Sharp/libvips HTTP service packaged in
the same image (`node /opt/image-optimizer/server.cjs`). It accepts binary POST
`/master` and `/preview`, not URLs. New originals are validated, converted to
full-resolution **lossless WebP** plus a separate <=480×960 preview, then only
those two derivatives are stored in S3. Metadata is stripped except ICC; EXIF
orientation is applied. Animation/alpha are retained. JPEG→lossless WebP can grow:
we do not promise lossless conversion always reduces bytes. Preview downscaling
is display-only; zoom fetches the full master. A failed optimizer fails the
upload with retry, never silently persists an unoptimized original.

Each photo starts its own upload immediately (no application upload semaphore).
Finite browser transports, CPU/native workers and Kubernetes memory remain real
limits. New jobs use the private optimizer concurrently; Node/libvips schedules
its native CPU work. Infrastructure resources do not make unlimited work free.

Gallery covers for the first three cards have high priority. A four-request
**background preview** scheduler prioritizes other covers, nearby galleries
(IntersectionObserver +600px) and then idle work. This scheduler is intentionally
separate from uncapped upload fan-out. Failed slides show retry, not a stale image.
Full masters load only on zoom. Existing originals are not destructively migrated:
previews are derived on demand into an atomic disk cache, and no user row/key is
changed. Immutable media supports Content-Length, ETag/304 and byte ranges;
`DATA_DIR/media-cache` is disposable, pruned toward a 1GiB budget (an oversized
active entry/concurrent writes can temporarily exceed it), not authoritative data.

Tracing: image.optimize.master/preview with input/output bytes and native engine
time; storage.open/s3.download/media.cache/media.prepare; browser.image.load and
browser.image.decode. Request receive timing uses `wishlist.upload.receive_seconds`
and byte count `wishlist.upload.bytes` (the old request_body attribute was removed
by the central privacy scrubber). No bodies, filenames or gift content are labels.
