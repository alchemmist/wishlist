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

Each gift supports up to 20 JPEG/PNG/WebP/GIF photos (8 MB each). Select several
files at once or add more in subsequent selections. In the editor, drag a
thumbnail or its handle to reorder; arrow buttons also work with a keyboard.
The first photo is the cover. Remove individual photos with the hover/focus
cross (always visible on touch devices), then Save. Cancel discards unsaved
editor changes. Existing single-photo gifts migrate automatically without
copying image objects. Upload retries reuse photo IDs and preserve completed
uploads, including when an upload response was lost.
