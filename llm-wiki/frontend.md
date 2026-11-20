# UI, Save и photo regressions

## Редактор и UX

Карточкообразный composer: фото слева, inline Title/Price/Description в content
column, Add/Save справа. Важна верхняя линия фото/title/actions на desktop/mobile.
Files/Paste занимают одинаковые половины, без скачка border/focus geometry.
Links: URL+Enter -> badge, input очищается/сужается; badge редактируется,
Delete удаляет focused badge, Backspace в пустом input возвращает последний URL.
Валидный незавершённый link включается в Add/Save. Максимум20 links.

Фото можно добавлять порциями, drag/touch/keyboard reorder; first=cover.
Trash на miniature — hover/focus, touch focus. Cancel не применяет несохранённые
изменения. Поздний clipboard/picker async result после Cancel игнорируется.
Clipboard API только после explicit Paste; keyboard Ctrl+V/⌘V fallback не требует
чтения буфера на page load. В тестах clipboard полностью mock, не системный.

Floating activity island: слева по центру, stage/progress/bytes/count/elapsed,
Stop/errors, затем check/autohide. Decorative surface `pointer-events:none`:
невидимая большая hit-area раньше могла перекрывать карточки. Controls остаются
интерактивными. Reduced motion — статичное feedback, без обязательной анимации.
Нет постоянных Gift added/updated/deleted success banners.

## Save и retries

- Create request UUID защищает и lost initial response; photo UUID защищает upload.
- Все photo upload requests стартуют сразу: старого трёх-worker application cap нет.
  Это не обещание бесконечной аппаратной concurrency браузера/Sharp/S3.
- Stop aborts browser requests, сохраняет форму/файлы для retry; сервер мог успеть
  commit/persist. Stop НЕ rollback.
- Retry reconcile по persisted server IDs; не дублировать уже сохранённые фото.
- Metadata-only Save с неизменёнными13 фото — один item PATCH, без uploads,
  gallery PATCH/deletes, account/list reload. Ответ обновляет только нужную card.
- Gallery order/deletion write только при реальном изменении, не на каждый Save.
- Ошибка не должна сбрасывать draft или скрывать частично сохранённое состояние.

## Gallery loading

Не ставить выбранный bitmap на экран до правильного load/decode. Старый bitmap
может оставаться в img при src swap и создавать ощущение одинаковых slides.
Preview preloader и generation guards скрывают stale image, показывают loading,
предлагают Retry. Arrow navigation работает и во время загрузки через figure focus.

Первые3 covers high priority. Четыре background preview jobs — это read prefetch
scheduler, НЕ лимит photo uploads. Idle и IntersectionObserver(+600px) прогревают
остальные gallery previews. Явно выбранный slide приоритетнее idle/offscreen cover.
Native browser lazy-image heuristics не должны блокировать выбранную фотографию.
WebKit stationary-pointer guard нужен: синтетический pointermove после focus/layout
не должен сбрасывать keyboard-selected slide обратно на hover segment.

## Full uncropped zoom

В карточке crop допустим. Для medium-zoom нужен настоящий intrinsic-ratio source
rectangle, иначе clone наследует crop/object-fit и часть фото остаётся обрезанной.
`prepareZoomSource`/`restoreZoomSource` временно меняют source geometry без reflow
карточки; размеры master могут отличаться от preview. Full master грузится только
по запросу zoom; после close возвращается cropped preview.

Сначала load, затем decode. Firefox мог успешно разрешить decode для старого
bitmap после изменения src: одного `await image.decode()` без load/error guards
недостаточно. Проверять source/connection/pending generation; Escape отменяет
отложенное открытие, а не обязан физически остановить network fetch.

Важные последние fixes:
- Escape очищает loading-original UI у pending request.
- `zoomLoadingRequests` WeakMap связывает feedback с request конкретной figure:
  завершение старого запроса не должно удалять индикатор нового и не должно
  оставлять старую карточку в eternal Loading original.
- Keyboard Enter zoom targets selected photo, не устаревший cover.

## Что проверять при изменении

Portrait/landscape edge markers видны полностью в zoom; preview снова cropped после
close. Delayed preview/master, Escape before completion, rapid slide changes,
second gallery while first master pending; source errors/Retry; touch swipe vs
accidental click; Left/Right при loading; WebKit stationary pointer; mobile320px,
412px и desktop; reduced motion; metadata-only network budget; late Cancel.

Изолированный Playwright покрывает4 профиля: desktop Chromium/Firefox,
mobile Chromium/WebKit. Snapshot1e5e35b:40scenarios ×4 =160checks.
