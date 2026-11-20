# Контекст и договорённости

## Продукт

Wishlist — публичные списки желаний: https://wishlist.alchemmist.xyz.
У Authentik-пользователя один список `/{username}`; на `/` вошедший переходит
к своему списку, гость получает вход/подсказку. Смотреть можно без входа.
Владелец редактирует подарки, ссылки и ordered photo galleries.
Гость бронирует именем; вошедший бронирует своим Authentik username автоматически.
Снять чужую бронь нельзя. Username — из OIDC, не произвольный введённый owner.

## Предпочтения пользователя

- Разговор на русском; исправления, тесты, деплой и реальная проверка результата.
- Завершённые изменения автоматически commit/push в main; не спрашивать каждый раз.
- Минималистичный редактор в форме карточки: фото слева, inline Title/Price/Description,
  Add/Save справа. Desktop/mobile alignment важен, в том числе ширина320px.
- Files/Paste — равные половины; одинаковая геометрия, понятные hover/focus states.
- Фото reorder — drag/touch/keyboard, без лишних ручек/стрелок. Первое — cover.
- Saved gallery — одно фото на всю высоту строки, без постоянного ряда thumbnails;
  hover story zones, touch swipe, keyboard, click/Enter zoom.
- Crop в карточке разрешён. Zoom — вся фотография, корректный aspect ratio,
  не thumbnail и не потеря качества из-за нового дополнительного сжатия master.
- Нет прикладного лимита количества/байтов/мегапикселей фото. Все upload requests
  стартуют сразу, без собственного worker cap. Ограничения браузера/CPU/сети реальны.
- Save feedback — floating island слева по центру: stage/progress/Stop/errors,
  затем галочка и autohide; не постоянные Gift added/updated/deleted banners.
- Только явный clipboard access; Cancel игнорирует поздние async results.

## Baseline после image pipeline, 2026-11-16

- FastAPI + SQLite WAL + vanilla JS/CSS + vendored medium-zoom.
- Authentik OIDC через Authlib; RustFS S3 для media.
- Отдельный приватный Sharp/libvips adapter, тот же container image.
- Новые uploads: full-resolution lossless WebP master + q90 preview <=480×960;
  unoptimized input не сохраняется в S3. ICC сохраняется, прочая metadata удаляется,
  EXIF orientation применяется. Alpha/animation проверены.
- API previews version2; immutable caching не должен закреплять старые тяжёлые bytes.
- Первые3 cover приоритетны, остальные готовятся постепенно; выбранный slide
  получает приоритет перед idle/offscreen background work.
- Старые originals сохранены. Для53 существующих фото additive backfill записал
 53 durable v2 previews без изменения DB image keys/старых объектов.
- Приватные telemetry/metrics подключены к существующим Collector/Tempo/Loki/
  Prometheus(Thanos)/Grafana. Privacy и общий tail sampler не ослаблялись.

## Проверенный snapshot (не live status)

Application: `1e5e35b6946f59e55967651bd6fe479106a49e5b`.
Image: `sha256:182152a2a99d04fa99fb72a2ad9f90234b55b82b9c037be180dcbe2a7b81eb03`.
Quality43935991777/release43936660784(attempt2) successful;77pytest/160browser checks.
Последний wiki/evidence-only Homelab commit перед этой документацией:
`cfc19dc58901f7cad58130e4c0c186985ab7e9a8`, QG43940284048 successful.
Оба deployment были1/1 Ready, zero restarts, на socrat-5.
Проверено19 карточек/53 фото анонимным браузером, без изменения записей подарков.
Нельзя трактовать эти counts, pod names или readiness как вечное текущее состояние.

## Границы

Не менять secrets/PVC/user DB ради acceptance, не делать blanket prune/sync или
live image override. Старый extraneous ConfigMap/wishlist оставлен; он давал
Argo OutOfSync при Healthy workloads. Это не причина удалять его вслепую.
Authoritative claim — wishlist-socrat5; retained wishlist-data не трогать.
Не читать личный clipboard. Не использовать production test-auth.
Не писать credentials/content в telemetry, wiki, логи и тестовые артефакты.
Подробные risks и проверенные/непроверенные обещания — history/images/operations.
