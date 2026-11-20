# Постоянная память Wishlist

Создано 2026-11-20; baseline production acceptance — 2026-11-16, чтобы знания не зависели от истории одной LLM-сессии.
Стартовые инструкции для агентов: [`../AGENTS.md`](../AGENTS.md).

## Карта знаний

| Файл | Что хранится |
| --- | --- |
| [context.md](context.md) | Продукт, требования пользователя, актуальный baseline, ограничения |
| [architecture.md](architecture.md) | Код, API, SQLite, OIDC, reservations, security |
| [frontend.md](frontend.md) | Редактор, Save/retry/Stop, clipboard, gallery/zoom, регрессии |
| [images.md](images.md) | Sharp, S3, master/preview, cache, production acceptance |
| [operations.md](operations.md) | Тесты, Quality Graph, trusted release, GitOps, telemetry, runbooks |
| [history.md](history.md) | Крупные исправления, commits, evidence и оставшиеся риски |

## Как пользоваться

- Сначала context, потом тематический файл и настоящий код.
- Snapshot реализации/acceptance ниже относится к application commit
  `1e5e35b6946f59e55967651bd6fe479106a49e5b` до добавления этой wiki.
- Не переписывать историю измерений как текущий benchmark. У результатов должны
  быть дата, условия, источник и ограничения.
- Новые задачи завершать обновлением соответствующей страницы; долгие исследования
  сохранять обезличенно в репозитории, а не только в `/tmp`.
- Если заметки расходятся с кодом/GitOps, выяснить причину и исправить документацию.
  Секреты и пользовательские данные сюда не копировать.

## Другие источники истины

- [README](../README.md), [OVERVIEW](../OVERVIEW.md): использование и обзор.
- [AUDIT](../AUDIT.md): аудит, regression coverage, известные ограничения.
- [CI](../CI.md): graph, generated files, trusted publisher/bootstrap.
- [OBSERVABILITY](../OBSERVABILITY.md): instrumentation/privacy/metrics.
- `../homelab/AGENTS.md`, `../homelab/llm-wiki/`: общая инфраструктура.
- `../homelab/charts/wishlist/`, `../homelab/deploy/wishlist/values.yaml`: deployment.
- `../homelab/docs/wishlist-images.md`, `wishlist-observability.md`: live acceptance.
- `../homelab/deploy/monitoring/migration-evidence/wishlist-images-20261116.json`
  и `wishlist-observability-20261116.json`: структурированные доказательства.
