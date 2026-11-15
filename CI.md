# Quality Graph CI

## Source of truth

The quality pipeline is declared in [`qg.yaml`](qg.yaml). Quality Graph 0.2.0
accepts **only this filename**, not `qg.yml`. The CLI and GitHub provider are
pinned together in `requirements-quality.txt`; execution and trusted publisher
Actions are pinned to the immutable 0.2.0 release commit.

Generate and commit the complete output set after changing the declaration:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/qg generate
.venv/bin/qg validate
.venv/bin/qg generated-files
```

Do not hand-edit:

- `.github/workflows/quality-graph.yml` — pull requests to `main` and manual checks;
- `.github/workflows/quality-graph-push.yml` — pushes to `main`;
- `.github/workflows/quality-graph-publish.yml` — trusted PR reporting;
- `.quality-graph/manifest.json` — expanded graph and provenance digest;
- the managed generated-file block in `.prettierignore`.

Generation is local and deterministic. It does not change branch protection,
run GitHub workflows, publish images or deploy the application.

## Checks and dependencies

All eleven nodes are blocking. Every job checks out without persisted
credentials, has read-only permissions and receives no deployment secrets.
Python is 3.13, Node is 24. Browser jobs install only their required engine.

| Node | Command / purpose | Requires |
| --- | --- | --- |
| `graph` | `qg validate`: schema, DAG and generated-file freshness | — |
| `workflow-syntax` | actionlint 1.7.11: all workflows, expressions and step syntax | `graph` |
| `python-syntax` | `python -m compileall -q app tests` | `graph` |
| `javascript-syntax` | Node syntax checks for frontend, vendor scripts, E2E and Playwright config | `graph` |
| `unit` | Entire pytest suite: API, concurrency, migrations, storage, CI regression checks; JUnit | Python/workflow syntax |
| `python-audit` | `pip-audit -r requirements-dev.txt`: application, test and Quality Graph dependencies | `graph` |
| `npm-audit` | `npm ci` and `npm audit --audit-level=low` | `graph` |
| `e2e-desktop-chromium` | 23 scenarios in desktop Chromium | `unit`, JS syntax |
| `e2e-desktop-firefox` | Same scenarios in desktop Firefox | `unit`, JS syntax |
| `e2e-mobile-chromium` | Same scenarios with Pixel 7 emulation | `unit`, JS syntax |
| `e2e-mobile-webkit` | Same scenarios with iPhone 13 / WebKit emulation | `unit`, JS syntax |

Independent checks run in parallel; each browser gets a separate runner and
an isolated app/temporary database/local OIDC fixture. No real Authentik/S3
credentials are required. Native jobs/logs remain visible in GitHub Actions.

`pytest` and Playwright publish JUnit through Quality Graph's structured
adapter, producing test counts and failure details in job summaries / the PR
dashboard. JUnit test failures cannot be approved away: finding approvals are
explicitly disabled for test nodes. Dependency audit output uses the exit-code
adapter with captured diagnostic logs; `pipefail` preserves the real failure.

## Reports and failure behavior

- pytest: `reports/pytest.xml`, with repository-relative testcase file paths;
- Playwright: `reports/playwright/junit.xml` in each job's separate workspace;
- Playwright evidence: `playwright-<project>-<run_attempt>` artifact, retained
  for seven days, containing HTML reports, JUnit and any failure traces/screenshots;
- Quality Graph: `quality-result-<node>-<run_attempt>` artifacts, retained for
  seven days, with validated result/provenance and diagnostics.

For browser jobs the steps intentionally:

1. run tests and record their exit status;
2. upload the evidence even when tests failed;
3. exit with the recorded status;
4. let Quality Graph collect/upload/enforce the result via `always()` steps.

This is not a test-failure exemption. A failed test, missing report, failed
upload or missing exit-status file leaves the job failed. Repository tests
exercise both successful and unsuccessful shell commands and a missing status
file to guard this behavior. Setup failures still fail the job; they cannot
produce a passing structured result.

Local reports are ignored by Git and excluded from the Docker build context.
Use `npx playwright show-report` to inspect the most recent local HTML report.

## Trusted publication and deploy

The generated publisher runs separately from pull-request execution. It does
not check out PR code. Its restricted write permissions are used for the managed
PR dashboard/check and authorized `/qg` administration, not application deployment.

The handwritten `.github/workflows/deploy.yaml` retains the credential-bearing
Harbor/Socrat steps. It listens for completed quality workflows and runs **only**
for successful `main` results produced in this repository by:

- a `push` run of **Quality Graph (push)**; or
- a manual `workflow_dispatch` run of **Quality Graph** on `main`.

PR runs, fork runs, feature-branch manual checks, failed and cancelled runs cannot
deploy. Checkout and image tag both use `workflow_run.head_sha`, not the release
workflow's `github.sha`. Immediately before deployment, the workflow verifies
that the checked SHA is still `main` HEAD; superseded runs cannot roll back a
newer commit. Harbor credentials and the homelab deployment token exist only in
this separate job. No untrusted result artifacts are downloaded or executed there.

For a manual release, select **Quality Graph → Run workflow → main**. The same
full graph must pass before the release workflow is triggered; there is no
manual bypass directly into deployment.

## Bootstrap and merge protection

`merge.required: true` declares the stable **Quality Graph** aggregate check as
the intended merge requirement. This does not mutate GitHub settings automatically.
The first adoption PR may not have a complete trusted dashboard because the
publisher reads the base branch, which does not yet contain this graph.

1. Review the declaration, generated workflows, permissions and release SHA.
   Commit all source/output files together, including the modified deploy workflow.
2. Merge the reviewed bootstrap and open a small probe PR from the updated base.
3. Verify the native jobs, managed PR dashboard and retained artifacts on GitHub.
4. Only then synchronize the required check with an administrative token
   (`Administration: write`) supplied securely as `GITHUB_TOKEN`:

   ```sh
   GITHUB_REPOSITORY=alchemmist/wishlist .venv/bin/qg github required-checks sync
   ```

5. Remove the superseded old required check, if one exists, after confirming
   the new aggregate works. Quality Graph's sync preserves unrelated checks;
   it does not remove an old Wishlist test job's requirement for you.

These administrative API changes have **not** been executed as part of the
local migration. Do not put an administrative token into PR job environments.

## Local verification

```sh
.venv/bin/qg validate
.venv/bin/python -m pytest -q --junitxml=reports/pytest.xml -o junit_family=xunit1
.venv/bin/python -m pip_audit -r requirements-dev.txt
npm ci
npm audit --audit-level=low
npx playwright install chromium firefox webkit
npm run test:e2e
# Run the workflow checker with a local Go installation:
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.11 -shellcheck= -pyflakes=
```

`PYTHON` and `E2E_PORT` overrides are described in README. GitHub runners install
Playwright with `--with-deps`; local macOS runs do not need Linux system packages.

Locally verified for this migration: `qg generate` / `qg validate`, actionlint,
**57 pytest checks**, **92 browser checks** (including authenticated one-click reservations),
Quality Graph JUnit parsing for pytest and each of the four browser reports,
and clean Python/npm dependency audits. GitHub-hosted execution, write-token
publication, branch protection and actual deployment require the bootstrap/probe
steps above; they were not exercised locally.
