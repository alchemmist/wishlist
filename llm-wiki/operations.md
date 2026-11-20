# Тесты, release, observability и runbooks

## Локальная среда

Python3.13, Node24; точные зависимости pinned в requirements*/package locks.
Основной путь воспроизведения:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
npm ci
npx playwright install chromium firefox webkit  # Linux: --with-deps
.venv/bin/python -m pytest -q
PYTHON=.venv/bin/python npm run test:e2e
npm --prefix services/image-optimizer ci
npm --prefix services/image-optimizer test
npm --prefix services/image-optimizer audit --omit=dev
.venv/bin/qg validate
```

В исходной сессии отдельный Python env `/tmp/wishlist-audit-py313` работал, но это
не обязательная/долговечная зависимость. Создать свой env, если его больше нет.
Unit tests: temporary SQLite/local media, RSA OIDC helpers, boto Stubber,
telemetry/privacy/schema/CI regression contracts. Playwright поднимает собственные
app/OIDC на19421/19422,2workers, без production credentials. `PYTHON` выбирает
interpreter, `E2E_PORT` — другую пару портов. Не убивать чужие8765/8766/18765
или другие процессы вслепую, если возник конфликт порта.

Частичные browser проверки:
```sh
PYTHON=.venv/bin/python npx playwright test --grep 'gallery preloads|starting another original'
PYTHON=.venv/bin/python npx playwright test --project desktop-firefox
```

Evidence: ignored `test-results/`, `playwright-report/`, `reports/playwright/junit.xml`.
Не коммитить реальные пользовательские фото/личный clipboard в fixtures. Скриншоты
при визуальной проверке просматривать, а не делать вывод по имени файла; предпочитать
инструмент, возвращающий image inline. Не сохранять лишние screenshots с user data.

## Если старое локальное окружение сломалось

На момент создания wiki(2026-11-20) существующая `.venv` оказалась Python3.15.2
с неполными deps (`opentelemetry.sdk`, PyYAML отсутствовали); старый `/tmp` env
не сохранился. Это не результат тестов приложения. Не менять чужую среду вслепую:
можно запускать изолированно через uv с pinned requirements/Python3.13:

```sh
uv run --python 3.13 --with-requirements requirements-dev.txt python -m pytest -q
uv run --python 3.13 --with-requirements requirements-quality.txt qg validate
```

## Quality Graph

`qg.yaml`, не qg.yml; Quality Graph CLI/GitHub provider0.2.0, runtime/publisher
immutable release SHA `3b0cf7d8a33215d5a3bb5521b4e0e41c18e8cb7c`.
На baseline12 blocking nodes: graph, workflow syntax(actionlint), Python/JS syntax,
native image-optimizer contracts/audit, pytest, Python/npm audits и4browser jobs.

После изменения декларации:
```sh
.venv/bin/qg generate
.venv/bin/qg validate
.venv/bin/qg generated-files
```
Коммитить весь generated set:3quality workflows, `.quality-graph/manifest.json`,
managed block `.prettierignore`. Не править generated workflow вручную.
Jobs secretless/read-only checkout без persisted credentials; deploy отдельно.
`CI.md` — детали bootstrap/trusted publisher/required-check synchronization.
Не менять branch protection/admin settings как побочный эффект обычного validate.

## Release boundary

`.github/workflows/deploy.yaml` получает успешный trusted main `workflow_run`:
checked repository/branch/event, checkout именно head_sha, concurrency wishlist-release,
Harbor login/build/push (GHA build cache), current-main SHA guard, deploy-to-socrat.
PR/fork не допускается до deploy secrets. Устаревший SHA нельзя выкладывать.
Manual release: запустить Quality Graph на main, не обходить тесты.

```sh
SHA=$(git rev-parse HEAD)
gh run list --repo alchemmist/wishlist --commit "$SHA" \
  --json databaseId,name,status,conclusion
gh run watch RUN_ID --repo alchemmist/wishlist --interval 15 --exit-status
```
Для `gh --commit` использовать FULL SHA: короткий SHA может вернуть пустой список.
Harbor TLS handshake timeout встречался transient на docker login. Сначала читать
конкретную ошибку, затем rerun failed trusted run при уместности. Не отключать TLS,
не сбрасывать credentials и не выдавать fail за успешно доставленный release.

Deploy action записывает digest в `../homelab/charts/wishlist/values.yaml` и
`deploy/wishlist/values.yaml`. Argo CD применяет owning source. В Homelab сначала
его AGENTS.md и wiki. Сохранять параллельные commits; fetch/merge перед push,
без force. Нельзя live image override или blanket sync/prune.

## Production acceptance

```sh
helm template wishlist ../homelab/charts/wishlist --namespace wishlist \
  -f ../homelab/deploy/wishlist/values.yaml
kubectl -n wishlist rollout status deployment/wishlist --timeout=120s
kubectl -n wishlist rollout status deployment/wishlist-image-optimizer --timeout=120s
kubectl -n wishlist get pods -o wide
curl -fsS https://wishlist.alchemmist.xyz/ >/dev/null
```

Rollout status может относиться к СТАРОМУ spec, если Argo ещё не применил новый
commit. Сравнивать desired/actual digest, revision, новый код/поведение публичного
endpoint. Проверять payload, decode/selection/full uncropped zoom, не только200HTML.
Для data-preserving acceptance — anonymous read-only browser, синтетические свои
pixels и изолированные object keys с cleanup; не фальшивый owner login, не DB writes.
Не массово «пересохранять» реальные карточки ради замеров. Любой backfill отдельно
описать: что добавлено, какие originals/DB не менялись, errors/retry/cleanup.

SQLite claim wishlist-socrat5 на socrat-5; retained wishlist-data не удалять.
Старый extraneous ConfigMap может держать OutOfSync: проверить diff, не prune blindly.
Healthy != integrity/DR или exporter delivery. Новые image waves сначала готовят
optimizer/image, затем Recreate web; всё ещё single-pod stateful availability.

## Observability

Dashboard: https://grafana.socrat.alchemmist.xyz/d/wishlist-performance.
Инфраструктурный owning source и acceptance в Homelab docs/wiki/evidence.
Официальный read-only Grafana MCP — предпочтительный интерфейс к Tempo/Loki/
Prometheus-Thanos; Argo MCP — read-only state/events. Инструкции/credential-helper
порядок — в Homelab AGENTS.md. Не выводить credential-helper output и private keys.

App OTel bounded mTLS exporter -> existing Collector
`https://10.250.0.3:44318/v1/traces`; scoped renewable client identity
`otel-app-tls-wishlist`. Не читать root CA private key. Optimizer сам сертификат/
credentials не получает; client processing spans в Wishlist покрывают private RPC.
Metrics8001 только приватно, Collector egress должен разрешать Wishlist8001.
HTTP metrics bounded route/method/status, operations histograms, aggregate DB inventory.
Если readonly inventory read не удался — success0 и не подставлять фиктивные zero counts.

Browser relay: allowlisted names/attrs, max32spans/batch,256KiB API body, bounded
queues и global60-token burst/refill2batches/s. Это не arbitrary OTLP proxy и не
полный audit log. Нельзя labels с usernames, URLs, filenames, titles, cookies, bodies,
SQL parameters. SQL spans только verb, exceptions только sanitized type.
W3C traceparent, Server-Timing links связывают fetch/XHR/native img server spans.

Важные этапы: browser page/save/fetch/upload/render/resource/image.load/image.decode;
server HTTP full response, body receive, db.*, validation, image.optimize.master/
preview + native engine ms/input/output bytes, S3 put/download и cache preparation.
`wishlist.upload.receive_seconds` и `wishlist.upload.bytes` заменили numeric
request_body attrs, которые центральный privacy scrubber удалял. Scrubber не ослабляли.

Shared tail sampler: decision30s, errors/5xx/≥2s retained, ordinary10%. Быстрый
trace не обязательно сохранится; queue/sampling могут дать partial spans.
После force_flush нужно дождаться tail window. Задержка для retention — synthetic,
НЕ latency benchmark. Отделять backend processing от real end-to-end owner Save.

## Проверенные telemetry evidence IDs

- `756a5b59006bba95cdbbd9cf5f355db8`: browser→Traefik→Wishlist→DB и matching Loki,
  раннее подтверждение observability. Retention delay не benchmark.
- `8fd8f57610559c088409b3871a995f2a`: earlier real anonymous image run,
  123retained spans,26image.load/26image.decode, exact Loki match1.
- `f8ce472228afc6e7a4bd2f3d921322c7`: final synthetic image pipeline14spans,
  numeric input/output/engine attrs сохранились в raw Tempo.
- `1a845075e48f62279d6c99e7edc3a40e`: anonymous empty telemetry POST204,
 12body bytes, intentional2.2s client delay; receive_seconds~2.100554 survives scrubber.
  Не реальный photo upload и не throughput benchmark.

Исторические traces могут уйти по retention. Репозиторные обезличенные evidence
нужны именно поэтому; не обещать вечную доступность trace по ID.
