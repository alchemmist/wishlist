# История решений и незавершённые риски

Это конспект большой audit/implementation-сессии до появления постоянной wiki.
Не нужно повторять уже сделанную работу только потому, что чат был compacted.
Точную реализацию смотреть в коде/git history; acceptance — в Homelab evidence.

## Завершённые пласты работ

1. **Audit API/UI/security.** Исправлены DOM `form.title`, цены/ссылки/validation,
   retries, mobile/a11y/Escape/reduced motion. OIDC signature/claims/state/nonce/
   PKCE/userinfo agreement, callback DB threadpool; auth policies остаются Authentik.
2. **SQLite.** Per-request WAL connections, rollback/close, BEGIN IMMEDIATE,
   permissions, Unicode uniqueness; guest-only duplicate merge без owner merge.
3. **Storage/recovery.** Safe keys, no bucket creation on startup, explicit timeout/
   retries/body close, missing/outage distinction, idempotent creation_key/photo UUID,
   ordered item_images, legacy cover compatibility, retry reconciliation/cleanup.
4. **Tests/CI.** Isolated pytest/E2E with temporary DB/local RSA OIDC/S3 Stubber;
   pinned Quality Graph0.2, blocking gates/JUnit/evidence, trusted main SHA release.
5. **Reservations.** Signed-in session identity automatic, guest name flow,
   account wait/retry, чужую бронь снимать нельзя.
6. **UI.** Inline card editor, ordered galleries/drag-touch-keyboard, story hover,
   swipe/zoom, links badges20,320px; Files/Paste halves, explicit clipboard fallback.
7. **No application photo caps.** Removed20photos/8MB/25MP; streaming/spooling,
   upload progress/Stop/retry; Stop != rollback, late Cancel ignored, WebKit guard.
8. **Save + observability (`ab3ecbf`).** Metadata-only13-photo edit1PATCH, no unchanged
   gallery writes/reloads; activity island/check/autohide; privacy-safe OTel/metrics/
   browser relay. At that stage71pytest/148browser checks.
9. **Homelab telemetry.** Scoped renewable mTLS certificate, Collector private scrape
   egress fixed, Tempo/Loki delivery verified; dashboard and aggregate inventory.
   Homelab commits07983ef5/eb16aadb/35311990/79db096b. Synthetic retention probes
   never advertised as latency benchmark.
10. **Uncropped medium-zoom (`e68e850`).** Intrinsic geometry and restore, correct
    selected target, load-before-decode Firefox race, stale pending cancellation.
    Portrait/landscape edge markers and delayed-load tests;71pytest/152browser.
    QG43879115597/release43879731169 successful.
11. **Image pipeline (`cd209e4`, `73e887a`, `e3d27bf`, `693b0bf`).** Private Sharp
    adapter/master+preview, all upload requests fan-out, streaming S3/multipart,
    disk cache/ranges/ETags, scheduled preview loading and explicit-selected priority,
    native decoder/animation contracts, new tracing.77pytest/156browser at first.
12. **Size/rollout hardening.** `58cabff` display-only preview q90/version2,
    `750c7f8` pending Escape feedback/GHA build cache,
    `1e5e35b` per-gallery loading-request WeakMap regression test. Final160browser.
    Homelab optimizer wave-1 (`71b612a9`) prevents cold image pull before web stop;
    scratch bounds/prefix-scoped restart cleanup (`441f84a0`). Initial YAML indent
    mistake727dd2a6 fixed0ec3be1c before activation; final render/server dry-run passed.
13. **Production image acceptance/backfill.**53durable v2 derivative objects,
    old originals/DB untouched,2.03MB previews vs46.2MB sources,53/53 selectable,
    full-size files absent in list network, exact synthetic master pixel roundtrip/
    cleanup. Final image release1e5e35b, evidence709bd3f7/cfc19dc5 in Homelab.

## Что важно не «починить обратно»

- Four background read jobs != four/three upload workers: uploads intentionally unbounded.
- Card crop != zoom crop: latter is regression.
- Lossless master != lossless thumbnail: q90 applies only to separate display preview.
- Cached legacy derivative generation != destructive migration of source objects.
- One metadata PATCH is deliberate optimization, not lost photo synchronization.
- No rollback on Stop is explicit durability/retry design, not failed cancellation.
- Bound telemetry/sampling/redaction are security/privacy requirements, not debugging
  inconvenience to switch off.
- GitOps/current-main guard and secretless checks must not be bypassed.

## Остаточные риски / не выдавать за выполненное

- Best-effort orphan cleanup, не durable GC/reconciler; S3+SQLite не одна transaction.
- Guest identity by name угадываема; это не authenticated reservation ownership.
- Single-pod SQLite/local node/PVC — availability/DR risk. Backups требуют настоящего
  WAL-consistent backup/restore acceptance, не Argo Healthy.
- Main application container исторически root; optimizer явно non-root/read-only.
  Не заявлять full workload hardening на основании sidecar security context.
- Unlimited application photos != infinite physical resources; memory/disk/network/
  browser limits действуют. Quotas/rate limiting для business uploads отдельно
  обсуждать, не возвращать отменённые UI caps без запроса.
- JPEG/already lossy WebP -> lossless master can grow. Нет real post-change owner
  16-photo Save benchmark; initial uplink original не уменьшается server codec.
- Fast browser telemetry может быть partial/dropped/sampled. Это не audit ledger.
- Historical extraneous ConfigMap/wishlist оставлен OutOfSync. Не blanket prune.
- Authlib/httpx→httpx2 deprecation warning известен; не трактовать как current failure.
- CI required-check/admin bootstrap/trusted publisher configuration требуют отдельной
  осознанной процедуры из CI.md; наличие generated workflow само не доказывает,
  что все GitHub admin settings синхронизированы.

Эти follow-ups не являются автоматическим разрешением запускать новые миграции,
менять secrets/SSO, удалять данные или переделывать инфраструктуру без задачи.
