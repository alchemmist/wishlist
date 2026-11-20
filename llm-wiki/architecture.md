# Архитектура и security invariants

## Карта кода

| Путь | Ответственность |
| --- | --- |
| `app/main.py` | routes, schemas, session/OIDC, CRUD, ordered photos, reservations, media, browser relay |
| `app/db.py` | SQLite connections/schema/migrations, normalization, guest identity |
| `app/storage.py` | safe keys, validation, local/S3 persistence, multipart, cleanup, derivative lookup |
| `app/images.py` | private optimizer client, streamed output verification, atomic bounded media cache |
| `app/middleware.py` | body limits и authentication до uncapped upload body read |
| `app/telemetry.py` | sanitized spans/logs, private metrics, inventory, bounded browser ingestion |
| `static/app.js` | editor, saving/recovery, reservations, gallery, full-master zoom |
| `static/photos.js` | preview scheduling, warmup/cache, selected/background priorities |
| `static/telemetry.js` | content-free browser spans, relay, resource/server trace links |
| `static/index.html`, CSS | card layout, picker, accessibility, activity island |
| `services/image-optimizer/` | core Node HTTP adapter + Sharp0.35.5, package lock/native contracts |
| `tests/e2e_server.py` | isolated app/OIDC provider; никогда не production auth bypass |

Если имя/граница модуля изменились, обновить эту карту. Версии библиотек брать из
requirements/package locks, а не полагаться на старый номер из переписки.

## Данные и API

- `users`: Authentik subject/username; `items`: owner, metadata, legacy cover,
  `creation_key` для UUID-idempotent create; `item_images`: ordered photo IDs/keys.
- Links, guests и reservations — отдельные сущности. HTTP(S) links максимум20,
  unsafe schemes отвергаются; title/price/description валидируются сервером.
- `GET /api/me`, `GET /api/wishlists/{username}` — account/list.
- `POST /api/wishlists/{username}` — create; `PATCH /api/items/{id}` — metadata.
- `PUT /api/items/{id}/images/{uuid}` — idempotent image upload.
- `PATCH /api/items/{id}/images` — ordered IDs и удаления; первый ID — cover.
- Старые `/image` endpoints сохраняют compatibility: POST заменяет gallery одним
  фото, DELETE убирает все. Не удалять legacy paths без сознательной миграции.
- `GET /media/{key}` — full; `?preview=1&v=2` — производная.
- `POST /api/telemetry/traces` — bounded allowlisted JSON, не raw OTLP proxy.
- Точные routes/methods/schema проверять в main.py/OpenAPI; эта карта не новый контракт.

## SQLite

Один authoritative SQLite volume, WAL, per-request connections, rollback/close.
Не возвращать shared concurrent request connection. `BEGIN IMMEDIATE` защищает
write races/reservations. `UNICODE_NOCASE`/normalization обеспечивают case-insensitive
Unicode uniqueness. Миграции объединяют только гостей с переносом броней;
конфликт owner usernames должен остановить запуск, а не слить аккаунты.
OIDC callback DB writes вынесены в threadpool: синхронный DB wait в event loop
раньше приводил к `sqlite3.OperationalError: database is locked`.
Миграции ordered photos сохраняют старый cover без копирования S3 originals.
Backup работающей WAL-базы — SQLite backup API, не самостоятельный DB file copy.

## OIDC, session, reservations

Authlib: подпись ID token, issuer, audience, expiry, state, nonce, PKCE S256,
userinfo-sub agreement. Не заменять проверку JWT простым decode.
Signed HttpOnly session; после входа identity по sub, временно state во время login.
Для HTTPS обязателен сильный стабильный SESSION_SECRET. Authorization по owner ID,
не по введённому display name. Login policy/group `app-wishlist-user` — в Authentik,
не дублирующий самодельный group gate в этом приложении.
Authenticated reservation identity берётся из session, supplied name игнорируется.
Гость вводит имя; Unicode case-insensitive identity. Чужую бронь снять нельзя.
Guest name не сильное доказательство личности: угадывание имён остаётся риском.

## Security и storage

CSRF/origin checks, CSP/security headers, no-store для чувствительных API/session.
Не использовать DOM named properties вроде `form.title` (это уже ломало editor).
S3 credentials заданы вместе; bucket существует заранее, startup не создаёт bucket.
Safe object keys, явные timeouts/retries, closing response bodies, missing vs outage
различаются. Write/DB reconciliation и best-effort orphan cleanup — не полноценная
распределённая транзакция. Обещать transactional rollback S3+SQLite нельзя.

Поддерживаемые фото JPEG/PNG/WebP/GIF. Проверка контейнера не должна декодировать
неограниченный массив pixels в web process. Upload spooling, S3 streaming/multipart,
отдельный CPU/memory-isolated codec. Обычный JSON capped256KiB; фото/picture-order
не должны вновь получить прежние count/body caps. Auth session проверяется до
uncapped body read; owner authorization на route всё равно обязательна.

Подробности производных/cache и physical bounds — [images.md](images.md).
