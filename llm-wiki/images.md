# Image pipeline и media performance

## Новый upload

Browser original -> authenticated Wishlist upload -> structural validation ->
private Sharp `/master` -> full-resolution lossless WebP -> `/preview` ->
display WebP q90/alphaQuality100 fit inside480×960 -> persist master+preview -> DB.
Новый unoptimized original не сохраняется в S3. Lossless относится к master,
не к уменьшенному display derivative. Исходная дополнительная quality loss при
zoom недопустима. ICC сохраняется, EXIF orientation применяется, остальные metadata
убираются; alpha/animation (frame delay/loop) проверены native tests.
JPEG и уже lossy WebP могут стать БОЛЬШЕ после lossless re-encode. Нельзя обещать,
что любой full master уменьшится, или что server codec ускоряет browser uplink.

`IMAGE_OPTIMIZER_URL` включает client. Sharp0.35.5/core Node HTTP adapter в
`services/image-optimizer`, отдельный private workload из ТОГО ЖЕ image:
`node /opt/image-optimizer/server.cjs`, ClusterIP8081. Binary POST `/master`,
`/preview`; remote URL fetch нет. Это не public weserv/img-proxy; существующий
nginx->weserv workload в Homelab не переиспользовался.

Optimizer client streams input/output, validates returned WebP; processing span
заканчивается до downstream preview/S3 работы. HTTP timeout180s/connect5s,
no app client connection cap; фактический native CPU scheduling всё равно конечен.

## S3 и cache

Master key — DB image_key, preview sibling `_previews/v2/{key}.webp`.
API preview URL `?preview=1&v=2`, ETag variant preview-v2. Версия нужна, чтобы
старые immutable lossless previews не закрепляли лишние bytes в browser cache.
Delete чистит master и текущую/старую preview version best-effort.

S3 streaming/file bodies, multipart5MiB, connection pool64. Не буферизовать все
большие файлы × fan-out в RAM. Все StreamingBody закрывать и на ошибках.
`MediaCache`: atomic writes, coalescing/striped locks, target~1GiB pruning,
immutable headers, stable ETags/304, Content-Length, byte ranges.
Media preparation/download отделены от HTTP response/client backpressure.

Старые originals не мигрировать/переписывать. Lookup сначала ищет durable preview;
при missing legacy preview генерирует его в disposable cache. Обычный read fallback
не пишет автоматически каждую legacy производную в S3. Одноразовый additive
backfill53v2 previews выполнен отдельно и записан в Homelab evidence. Благодаря ему
рестарт web не требует вновь скачать/перекодировать53originals.

## Infrastructure isolation (snapshot)

Owning source: `../homelab/charts/wishlist/`, prod values `deploy/wishlist/values.yaml`.
Optimizer: same namespace Wishlist-only ingress8081, deny all egress, no credentials,
no service-account token/PVC; UID/GID1000, non-root/read-only rootfs/scratch.
CPU request250m/no CPU limit, memory request256Mi/limit2Gi, scratch/ephemeral4Gi.
postStart чистит ТОЛЬКО свои `wishlist-image-*` interrupted temp jobs при каждом
container start, не пользовательские/PVC files. Проверять actual hooks/readiness.
Web: memory request128Mi/limit512Mi, ephemeral4Gi; `/data/media-cache` отдельный
2Gi emptyDir. Authoritative1Gi SQLite claim не заполнять media cache.
Optimizer resources sync-wave-1: same-node image pull/readiness до wave0 Recreate
web. Первоначальный cold pull давал временный503; pre-pull уменьшает это окно.
Single-pod SQLite всё равно не zero-downtime HA.

## Реальное расследование до исправлений

Grafana MCP, trace `d4ee505c2beea3d00306d989edf0ae42`:
16photos/17.076s Save, overlapping S3 puts0.287–3.570s,
commit sum~106ms, validation sum~153ms. Sum S3 puts~20.84s — перекрывающиеся
интервалы, НЕ20.84s exclusive critical path. Другой Save4photos/14.014s,
trace `790bffe3aa50dcf9987b927cfc96b809`, puts2.078–3.168s.
Read traces включали~15.6s и interrupted/late streams near56s.
Не вся задержка доказана как S3; старые request_body timing attrs scrubber удалял.
Новые safe numeric receive/bytes нужны для дальнейшей диагностики, не повод
ослаблять scrubber. Старые результаты не являются post-change upload benchmark.

## Production acceptance 2026-11-16

Анонимный Chromium через production URL,19items/53photos:
-53/53 HTTP200,53/53decoded/selectable slides, errors0, full-master requests0.
-53preview bytes2,025,542 vs original46,193,502 (~22.8× меньше для просмотра всех).
-Последний fresh-client run: first3covers538ms, last network media responseEnd1796ms
  от navigation. Полный script wall10.6s включает дополнительные UI checks/wait.
-Отдельный forced-empty-server-cache run: first3~1943ms, all53successful с S3
  derivatives; cache eviction не менял DB/S3. Это локальные замеры, не SLA/benchmark
  любой сети. Не называть их universally cold/warm без проверки cache conditions.
-Backfill53new derived objects, zero originals rewritten/deleted, DB unchanged.
-Source format snapshot36PNG/10WebP/7JPEG.

Synthetic production write/read/delete probe:1200×1800RGBA master,480×720preview,
exact decoded master pixels; both objects cleanup confirmed. Trace
`f8ce472228afc6e7a4bd2f3d921322c7`,14 retained spans. Measured pipeline~0.279s
относится к простой synthetic картинке, НЕ реальной batch latency. Extra2.1s sleep
был только для unchanged tail retention. User Save под owner identity не повторяли.
Полный JSON/условия: Homelab `migration-evidence/wishlist-images-20261116.json`.
