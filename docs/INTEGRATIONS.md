# План интеграций с внешними сервисами

Версия: 1.0 · дата: 18.11.2026 · относится к LocalAgent 0.6.0
Статус: план, код не менялся. Документ описывает, что подключаем, в каком порядке, как храним доступы и как показываем состояние в интерфейсе.

---

## 1. Резюме

- В каталоге 23 сервиса в 7 категориях. Оценка работ — **≈53 человеко-дня** на сами интеграции плюс **≈14 дней** на общую инфраструктуру (хранилище секретов, OAuth-движок, HTTP-транспорт MCP, реестр интеграций и статус в UI). С буфером 20 % — **≈80 дней**, то есть около четырёх месяцев одним разработчиком или ~2,5 месяца вдвоём.
- Порядок: **Telegram → Google (Calendar, Gmail, Sheets, Drive) → Obsidian → GitHub → Notion → МойСклад**. Логика — максимум пользы на день работы при минимуме риска; обоснование в §4.
- Главный технический разрыв: `app/mcp.py` умеет только **stdio** и не передаёт серверу переменные окружения. Большинство современных серверов (Notion, Linear, Slack, Atlassian, Airtable, GitHub, Google) — **remote HTTP с OAuth 2.1**. Без HTTP-транспорта и OAuth-движка подключается меньше половины списка.
- Второй разрыв: в `MEMORY.md` записано «ключи API — только DPAPI», но `app/keystore.py` использует собственную схему (PBKDF2 + blake2b-keystream, привязка к `USERNAME`/`COMPUTERNAME`). DPAPI в коде нет. До первой волны нужно либо привести код к DPAPI, либо исправить память и README (§5).
- Правило по умолчанию: **чтение — свободно, запись/отправка — через подтверждение**. Для внешних сервисов это важнее, чем для локальных файлов: ошибка агента уходит наружу и отзывается тяжело.

---

## 2. Что уже есть в коде (точки опоры)

| Компонент | Где | Что даёт | Чего не хватает |
|---|---|---|---|
| MCP-клиент | `app/mcp.py` | stdio JSON-RPC: `initialize` (protocolVersion `2025-06-18`), `tools/list`, `tools/call`; префикс `mcp__<сервер>__<инструмент>`; пул сессий; `TIMEOUT = 25 c` | HTTP/SSE транспорт, env для секретов, захват `stderr`, сверка согласованной версии протокола, отмена запроса, health-статус |
| Каталог наборов | `MCP_CATALOG` в `app/main.py` | три набора: `filesystem`, `memory`, `fetch` | поля авторизации, версии, health, прав, секретов |
| API | `app/main.py` | `GET /api/mcp`, `POST /api/mcp/add`, `/api/mcp/remove`, `/api/mcp/test`; защита заголовком `X-Agent-Token` | реестр интеграций, `/api/integrations`, OAuth-callback, revoke |
| UI | `renderMcp()` в `app/ui/app.js`, панель `mcpPanel` | список серверов, «Проверить», «Удалить», готовые наборы | индикатор «подключено/нет», аккаунт, срок токена, текст ошибки |
| Хранилище ключей | `app/keystore.py` | шифрование значений, файл `keys.json`, соль `keys.salt` | DPAPI, версия формата, миграция, ACL, аудит |
| Настройки | `app/config.py` | `settings.json`: `mcp`, `projects`, `approval_mode`, `allow_write/shell/web` | политика прав на интеграцию (read/write/send), таймауты, лимиты |
| Тесты | `tests/fake_mcp.py`, `tests/smoke_mcp.py` | эхо-сервер и smoke-прогон клиента без сети | сценарии «медленный», «падает», «пустой список», «не та версия протокола» |

Практический вывод: каркас есть, но он про **локальные** MCP-серверы, запускаемые через `npx`/`uvx`. Всё, что требует OAuth и удалённого эндпоинта, потребует новой инфраструктуры, а не ещё одной записи в `MCP_CATALOG`.

---

## 3. Каталог сервисов по категориям

### 3.1 Как читать таблицы

- **Способ**: `MCP` (готовый сервер по протоколу), `REST` (свой клиент на `urllib`), `IMAP/SMTP` (почтовые протоколы), `FS` (локальные файлы).
- **Авторизация**: `нет`, `токен` (bot/PAT/API-key), `OAuth2` (+ тип flow), `Basic`. Для каждого указано, нужна ли регистрация приложения.
- **Дни** — один разработчик, включая тесты и документацию, при **готовой инфраструктуре из §5–§8**. Без неё к каждой OAuth-интеграции добавляйте 3–5 дней.
- **Польза** (1–5): 5 — ежедневный сценарий, без которого агент заметно слабее; 3 — периодические задачи; 1 — нишевое.
- **Волна**: 1 — приоритетные из §4 (девять сервисов), 2 — вторая волна, 3 — по запросу, «—» — не планируется.

### 3.2 Мессенджеры

| Сервис | Способ | Авторизация | Дни | Польза | Волна | Документация |
|---|---|---|---|---|---|---|
| Telegram | REST (Bot API) | токен бота от @BotFather | 2 | 5 | 1 | [core.telegram.org/bots/api](https://core.telegram.org/bots/api) |
| Slack | MCP (remote) или REST Web API | OAuth2 (user scopes), одобрение админа воркспейса | 3 | 3 | 2 | [docs.slack.dev/ai/mcp-server](https://docs.slack.dev/ai/mcp-server) |
| Discord | REST + Gateway (или готовый MCP) | токен бота, privileged intent | 4 | 2 | 3 | [discord.com/developers/docs](https://discord.com/developers/docs) |

**Telegram.** Бот: `getUpdates` (long polling, без публичного вебхука — важно для домашнего ПК), `sendMessage`, `sendDocument`. Инструменты: `tg_send`, `tg_read` (только чаты, куда бота добавили), `tg_digest`. Ограничения Bot API: бот не видит историю до добавления, не пишет первым незнакомому пользователю. Вариант «от имени пользователя» (MTProto/TDLib, `api_id`/`api_hash` + сессия) — 7–10 дней и риск ограничений аккаунта; **не планируем**.

**Slack.** Remote MCP `https://mcp.slack.com/mcp`, OAuth, нужен client ID приложения и одобрение администратором воркспейса. Даёт поиск по сообщениям/файлам, чтение каналов и тредов, отправку, canvas. Запасной путь — обычный бот-токен `xoxb` и Web API, если MCP в воркспейсе запрещён.

**Discord.** Токен бота из Developer Portal, для чтения содержимого нужен privileged intent `MESSAGE_CONTENT` (включается вручную). У stdlib нет WebSocket-клиента: либо REST-опрос (медленно, упирается в лимиты), либо готовый MCP-сервер, либо новая зависимость. Самобот (токен пользователя) запрещён правилами Discord — не рассматриваем.

### 3.3 Почта и календарь

| Сервис | Способ | Авторизация | Дни | Польза | Волна | Документация |
|---|---|---|---|---|---|---|
| Gmail / IMAP | IMAP/SMTP, затем REST | пароль приложения (2FA) → OAuth2 + PKCE | 3 | 5 | 1 | [gmail/imap](https://developers.google.com/gmail/imap), [gmail/api](https://developers.google.com/gmail/api) |
| Google Calendar | REST | OAuth2 + PKCE | 2 | 5 | 1 | [calendar/api](https://developers.google.com/calendar/api) |
| Outlook (Microsoft 365) | REST (Graph) | OAuth2, device code flow | 3 | 3 | 3 | [learn.microsoft.com/graph](https://learn.microsoft.com/graph/) |

**Gmail.** Два пути. Первый — `imaplib`/`smtplib` с паролем приложения: 2 дня, без OAuth-верификации, но нужен включённый IMAP и 2FA, и нет тредов/меток API. Второй — Gmail API: поиск, треды, метки, отправка; скоупы `gmail.readonly`/`gmail.send` относятся к restricted, для внешних пользователей нужна верификация Google. Рекомендация: **начать с IMAP**, API добавить позже на том же OAuth-клиенте, что Calendar.

**Google Calendar.** `calendar.events`, `events.list/insert/quickAdd`, `syncToken` для инкрементальной синхронизации, отдельная возня с часовыми поясами и recurring events. Даёт агенту «что у меня завтра» — один из самых частых запросов.

**Outlook.** Microsoft Graph (`/me/messages`, `/me/events`, `/me/sendMail`), регистрация приложения в Entra ID как public client **без секрета**, device code flow — локальный порт не нужен, удобно для десктопа. Скоупы `Mail.Read`, `Mail.Send`, `Calendars.ReadWrite`, `offline_access`. Официального MCP для потребительского Outlook нет — пишем нативный клиент.

### 3.4 Заметки

| Сервис | Способ | Авторизация | Дни | Польза | Волна | Документация |
|---|---|---|---|---|---|---|
| Notion | MCP (remote) или REST | OAuth2 либо internal integration token | 2 | 5 | 1 | [notion.dev/mcp](https://developers.notion.com/docs/mcp), [API](https://developers.notion.com/) |
| Obsidian | FS (хранилище на диске) | нет | 1 | 4 | 1 | [Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) |
| Evernote | — | — | 0 | 1 | — | [dev.evernote.com](https://dev.evernote.com/) |

**Notion.** Remote MCP `https://mcp.notion.com/mcp` с OAuth: доступ в пределах прав пользователя, админ воркспейса видит подключения. Альтернатива — внутренняя интеграция с токеном `ntn_...` и REST (`Notion-Version` в заголовке), но тогда каждую страницу нужно расшарить на интеграцию вручную. Для «личного помощника» MCP удобнее, для корпоративного — токен с узким доступом.

**Obsidian.** Официального API нет. Лучший путь — работать с vault как с папкой: чтение, поиск по markdown, добавление в конец заметки, учёт `mtime` перед записью (иначе затрём правки из редактора). Плагин Local REST API + `mcp-obsidian` тоже работает, но добавляет зависимость, HTTPS-сертификат и ещё один ключ.

**Evernote.** Новые API-ключи не выдаются, SDK в legacy-состоянии. Интеграцию не делаем; приемлемая замена — экспорт `.enex` и импорт в Obsidian/Notion разовым скриптом.

### 3.5 Файлы

| Сервис | Способ | Авторизация | Дни | Польза | Волна | Документация |
|---|---|---|---|---|---|---|
| Google Drive | REST | OAuth2, скоуп `drive.file` | 2 | 3 | 1 | [drive/api](https://developers.google.com/drive/api) |
| OneDrive | REST (Graph) | OAuth2, тот же клиент, что Outlook | 2 | 2 | 3 | [graph/onedrive](https://learn.microsoft.com/graph/api/resources/onedrive) |
| Dropbox | MCP (beta) или REST | OAuth2 + PKCE, scoped app | 2 | 2 | 2 | [HTTP API](https://www.dropbox.com/developers/documentation/http/documentation) |

**Google Drive.** `drive.file` — нечувствительный скоуп: приложение видит только созданные им файлы, верификация Google не нужна. Для чтения чужих файлов нужен `drive.readonly` (restricted) — включаем осознанно и отдельным переключателем. Экспорт Google Docs в `text/plain`/`docx`.

**OneDrive.** Тот же Azure-клиент, что у Outlook: `/me/drive/root/children`, `/content`, `Files.ReadWrite`. Поэтому ставим после Outlook и считаем +2 дня, а не полную интеграцию.

**Dropbox.** Scoped-приложение, PKCE, `files.metadata.read` + `files.content.read/write`. Лимит `list_folder` — 100 записей на вызов, нужна пагинация. Есть remote MCP в бете; для нас предпочтительнее REST, чтобы не зависеть от беты.

### 3.6 Разработка

| Сервис | Способ | Авторизация | Дни | Польза | Волна | Документация |
|---|---|---|---|---|---|---|
| GitHub | MCP (remote/локальный) или REST | fine-grained PAT либо OAuth | 2 | 5 | 1 | [github-mcp-server](https://github.com/github/github-mcp-server), [REST](https://docs.github.com/rest) |
| GitLab | MCP или REST v4 | PAT либо OAuth 2.0 (DCR) | 2 | 2 | 3 | [GitLab MCP](https://docs.gitlab.com/user/gitlab_duo/model_context_protocol/mcp_server/), [REST](https://docs.gitlab.com/api/rest/) |
| Jira | MCP (stdio `mcp-atlassian`) или REST v3 | API token (Basic) либо PAT для Server | 3 | 4 | 2 | [mcp-atlassian](https://github.com/sooperset/mcp-atlassian), [REST v3](https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/) |
| Linear | MCP (remote) или GraphQL | OAuth либо personal API key | 2 | 3 | 3 | [linear.app/docs/mcp](https://linear.app/docs/mcp), [GraphQL](https://linear.app/developers/graphql) |

**GitHub.** Remote MCP `https://api.githubcopilot.com/mcp/` (заголовок `Authorization: Bearer <PAT>`) либо локальный `github-mcp-server` через stdio. Fine-grained PAT по конкретным репозиториям: `contents:read`, `issues:write`, `pull_requests:read`. Отдельно: пат не должен иметь `repo` целиком и не должен попадать в `settings.json`.

**GitLab.** Официальный MCP с OAuth 2.0 Dynamic Client Registration; на self-managed доступ включается администратором инстанса. Если инстанс старый — REST v4 с PAT, это надёжнее.

**Jira.** Быстрее всего `uvx mcp-atlassian` (stdio, env `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`) — 37+ инструментов, JQL, создание задач, комментарии. Официальный Rovo MCP (`https://mcp.atlassian.com/v2/mcp`, OAuth 2.1) тоже вариант, но зависит от условий Atlassian AI Gateway. Запись (создание задачи, комментарий) — только через подтверждение.

**Linear.** Remote MCP `https://mcp.linear.app/mcp` (Streamable HTTP, OAuth; для старых клиентов есть SSE-фолбэк). Альтернатива — GraphQL с personal API key, если OAuth-мост не взлетит.

### 3.7 Данные

| Сервис | Способ | Авторизация | Дни | Польза | Волна | Документация |
|---|---|---|---|---|---|---|
| Google Sheets | REST | OAuth2 (общий клиент Google) | 1,5 | 4 | 1 | [sheets/api](https://developers.google.com/sheets/api) |
| Airtable | MCP (remote) или REST | scoped PAT | 1,5 | 3 | 2 | [airtable mcp](https://airtable.com/developers/agents/mcp/getting-started), [Web API](https://airtable.com/developers/web/api/introduction) |
| 1С | REST (OData) | Basic (логин/пароль публикации) | 7 | 4 | 3 | [odata.org](https://www.odata.org/documentation/), ИТС (по подписке) |
| МойСклад | REST JSON API 1.2 | Bearer token | 3 | 4 | 1 | [dev.moysklad.ru](https://dev.moysklad.ru/doc/api/remap/1.2/) |

**Google Sheets.** `spreadsheets.values.get/append/update`, скоуп `spreadsheets` (или `spreadsheets.readonly`). Квоты проекта — порядка 300 запросов/мин, для наших сценариев с запасом. Хороший «отчётный» канал: агент складывает таблицу туда, где пользователь её ждёт.

**Airtable.** PAT со скоупами `data.records:read`, `data.records:write`, `schema.bases:read`; REST `https://api.airtable.com/v0/{base}/{table}`. Официальный MCP доступен на всех тарифах и уважает права пользователя — можно взять его, если нужен быстрый старт.

**1С.** Стандартный интерфейс OData: публикация базы на веб-сервере, `$metadata`, `Basic`, пагинация `$top/$skip`, фильтры `$filter`. Сложность не в протоколе, а в разнообразии конфигураций: состав сущностей и доработки у каждого клиента свои, документация — на ИТС по подписке. Честная оценка 7 дней; начинать только под конкретную базу.

**МойСклад.** JSON API 1.2, `Authorization: Bearer <token>`, пагинация `limit/offset` (макс. 1000), отдельные лимиты на частоту запросов. Закрывает сценарии «остатки», «заказы», «отчёт за период» для малого бизнеса — высокая польза при узкой аудитории.

### 3.8 Прочее

| Сервис | Способ | Авторизация | Дни | Польза | Волна | Документация |
|---|---|---|---|---|---|---|
| RSS / Atom | REST (свой парсер) | нет | 1 | 4 | 2 | [feedparser](https://feedparser.readthedocs.io/) |
| Погода | REST | нет (Open-Meteo) / API key (OpenWeatherMap) | 0,5 | 3 | 2 | [open-meteo.com](https://open-meteo.com/en/docs), [openweathermap.org](https://openweathermap.org/api) |
| Браузер | MCP (Playwright) | нет (локальный профиль) | 3 | 4 | 2 | [playwright-mcp](https://github.com/microsoft/playwright-mcp), [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) |

**RSS.** `urllib` + `ElementTree` достаточно; `ETag`/`Last-Modified`, дедуп по `guid`/`link`, лимит на размер ответа. Кормит утренний дайджест — самый дешёвый способ сделать агента полезным каждый день.

**Погода.** Open-Meteo без ключа и без регистрации: координаты или геокодинг, кэш 10–15 минут. OpenWeatherMap — если нужны русские описания и прогноз по часам, там ключ.

**Браузер.** Playwright MCP (stdio, `npx @playwright/mcp@<версия>`) — навигация, чтение страницы, клики, скриншоты; отдельный профиль, чтобы не отдавать агенту авторизованные сессии. Chrome DevTools MCP — вариант для отладки. Обязательны allowlist доменов, запрет скачиваний и подтверждение на любую форму/отправку. Свой CDP-клиент на stdlib не сделать: нужен WebSocket.

### 3.9 Сводка

| Категория | Сервисов | Дней |
|---|---|---|
| Мессенджеры | 3 | 9 |
| Почта и календарь | 3 | 8 |
| Заметки | 3 (2 рабочих) | 3 |
| Файлы | 3 | 6 |
| Разработка | 4 | 9 |
| Данные | 4 | 13 |
| Прочее | 3 | 4,5 |
| **Итого** | **23** | **≈53** |

---

## 4. Приоритет внедрения

### 4.1 Первый: Telegram — почему именно он

1. **Самый дешёвый вход в «агент снаружи».** Один токен, один HTTP-клиент на `urllib`, 2 дня. Не нужны ни OAuth-движок, ни браузерный колбэк, ни новый транспорт — то есть он не блокируется инфраструктурой и даёт работающий результат в первую неделю.
2. **Ежедневный сценарий.** Отправить себе заметку, получить дайджест, поставить напоминание, переслать файл агенту — это происходит каждый день, в отличие от Jira или Airtable.
3. **Канал доставки для остальных интеграций.** Утренний брифинг (календарь + RSS + погода), алерт «упал токен Google», отчёт «новые задачи в Jira» — всё это уходит в Telegram. Сделав его первым, мы получаем транспорт уведомлений для волны 2 и 3 и не изобретаем его позже.
4. **Низкий риск.** Bot API стабилен, лимиты предсказуемы, аккаунт пользователя не затрагивается, отзыв — одна кнопка у @BotFather. Ошибочная отправка ограничена чатом с ботом.
5. **Проверяет архитектуру целиком на дешёвом кейсе:** хранилище токена, реестр интеграций, статус в UI, аудит вызовов, подтверждение на запись. Все находки переносятся на OAuth-сервисы бесплатно.

Определение готовности: `tg_send`, `tg_read`, `tg_digest`; long polling в фоновом потоке; токен в DPAPI; карточка в UI со статусом и кнопкой «Отключить»; аудит каждого вызова; тест на mock-сервере без сети.

Альтернативные кандидаты на первое место (если приоритеты изменятся): **Google Calendar** — если важнее «рабочий день», **Obsidian** — если важнее локальные заметки (1 день и ноль авторизации). Оба ниже в списке и легко меняются местами с Telegram без переделки плана.

### 4.2 Следующие пять

| # | Интеграция | Дни | Польза | Почему здесь |
|---|---|---|---|---|
| 2 | **Google: Calendar + Gmail** | 5 | 5 | Один OAuth-клиент на два ежедневных сценария: «что у меня завтра» и «разбери почту». Почту начинаем с IMAP — ноль верификаций Google, потом меняем транспорт на API без смены инструментов |
| 3 | **Obsidian (FS)** | 1 | 4 | Самый высокий ROI в списке: 1 день, нет авторизации, локальные данные — ровно то, ради чего продукт существует. Заодно обкатываем безопасную запись с проверкой `mtime` |
| 4 | **GitHub** | 2 | 5 | У пользователя почти наверняка есть репозитории; PAT выдаётся за минуту, remote MCP уже готов. Закрывает «что сломалось в CI», «сделай issue из этого текста» |
| 5 | **Notion** | 2 | 5 | Основное хранилище заметок и баз у многих; OAuth-MCP снимает ручное расшаривание страниц. Важно, чтобы OAuth-движок уже был отлажен на Google |
| 6 | **МойСклад** | 3 | 4 | Единственная учётная интеграция в первой волне: токен, стабильный API, понятная польза (остатки, заказы, отчёт). Даёт связь от бизнес-пользователей до того, как вложимся в 1С |

Суммарно: **≈13 дней**, пять пунктов (шесть сервисов), три способа авторизации (токен, OAuth2, FS) — то есть все механики проверены на дешёвых кейсах.

Порядок внутри пар «Google» выбран так: Calendar → Gmail → Sheets → Drive. Календарь проще (один ресурс), Gmail даёт больше пользы, Sheets/Drive — «отчётность» и файлы, их можно отложить на пару недель без потери смысла.

### 4.3 Дорожная карта

| Спринт | Содержание | Дни | Результат |
|---|---|---|---|
| 0 | DPAPI-хранилище + миграция, OAuth-движок (loopback + PKCE), HTTP-транспорт MCP, реестр интеграций + `/api/integrations` + карточки в UI, аудит и редакция секретов | 14 | Инфраструктура, на которой любая интеграция — это 1–3 дня |
| 1 | Telegram, Google Calendar, Gmail (IMAP) | 7 | Агент пишет в мессенджер, знает календарь, читает почту |
| 2 | Google Sheets, Google Drive, Obsidian, GitHub | 6,5 | Отчёты в таблицы, файлы, заметки, репозитории |
| 3 | Notion, МойСклад | 5 | Первая волна закрыта |
| 4 | Slack, Jira, RSS, погода, браузер, Dropbox | 12 | «Рабочий» сценарий и ежедневный дайджест |
| 5 | Linear, Airtable, OneDrive, GitLab, Discord | 12 | Закрываем разработку и файлы |
| 6 | 1С, Outlook | 10 | По запросу конкретного пользователя |

Буфер 20 % заложен в общую оценку §1; спринты 0–3 — это минимум, который имеет смысл выпускать как 0.7.0.

### 4.4 Что осознанно отложено

- **Evernote** — API закрыт для новых ключей, польза 1. Вместо интеграции — импорт `.enex`.
- **Telegram от имени пользователя (MTProto)** — 7–10 дней, риск ограничений аккаунта, дублирует Bot API по смыслу.
- **Discord** — низкая польза при высокой цене (нет WebSocket в stdlib, privileged intents, модерация).
- **1С** — самая дорогая позиция (7 дней) и самая неоднородная; только под конкретную базу и с доступом к её метаданным.
- **Google Workspace MCP** — официальные remote MCP Google требуют участия в Developer Preview Program и проекта в Google Cloud; для личного `@gmail.com` не подходят. Идём через REST + свой OAuth-клиент.

---

## 5. Токены: хранение и авторизация

### 5.1 Модель угроз (что именно защищаем)

| Угроза | Реалистичность | Что делает DPAPI |
|---|---|---|
| Файл `keys.json` скопирован на другой ПК / в бэкап | высокая | Расшифровка невозможна вне профиля пользователя |
| Другой пользователь на том же ПК | средняя | Ключ выводится из профиля, чужой пользователь не расшифрует |
| Портативная копия `data\` на флешке | средняя | Токены не переносятся — потребуется повторный вход (это правильное поведение, но его надо объяснить в UI) |
| Токен попал в лог, трейсбек, дамп | высокая | Не защищает — нужна редакция логов |
| Токен отдан фронтенду через `/api/state` | средняя | Не защищает — нужен контракт API |
| Вредонос под тем же пользователем | средняя | Не защищает: DPAPI расшифрует любой процесс от имени пользователя |

Вывод: DPAPI решает ровно одну задачу — «секрет на диске бесполезен вне этой учётной записи». Остальное закрывается кодом, а не шифрованием.

### 5.2 Как работает DPAPI

`CryptProtectData` шифрует данные ключом, производным от учётной записи Windows (мастер-ключ в профиле, опционально — от домена). Приватный ключ не покидает профиль; при переустановке системы или смене пароля без миграции профиля данные теряются.

Параметры, которые нам важны:

- **entropy** — дополнительная соль. От вредоноса под тем же пользователем не спасает (константа есть в коде), но чужой процесс без этой строки блоб не расшифрует. Менять её нельзя: старые блобы перестанут читаться.
- **`CRYPTPROTECT_UI_FORBIDDEN` (0x1)** — не показывать диалоги, ошибка вместо зависания (для фонового процесса обязательно).
- **`CRYPTPROTECT_LOCAL_MACHINE` (0x4)** — «машинная» область: расшифрует любой пользователь ПК. **Не используем**: для токенов это хуже, чем профильная привязка.
- Повторное шифрование того же блоба даёт другой результат (случайная соль внутри) — сравнивать блобы нельзя, сравнивать нужно расшифрованные значения.

Документация: [CryptProtectData](https://learn.microsoft.com/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata), [CryptUnprotectData](https://learn.microsoft.com/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata).

### 5.3 Реализация на stdlib (ctypes)

Новый модуль `app/dpapi.py` — без зависимостей, только `ctypes`:

```python
"""Обёртка над Windows DPAPI: шифрование секретов ключом профиля пользователя."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes

crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CRYPTPROTECT_UI_FORBIDDEN = 0x1
ENTROPY = b"LocalAgent/secrets/v2"   # менять нельзя: старые блобы перестанут читаться


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
]
crypt32.CryptProtectData.restype = wintypes.BOOL
crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
]
crypt32.CryptUnprotectData.restype = wintypes.BOOL
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
kernel32.LocalFree.restype = ctypes.c_void_p


def _blob(data: bytes):
    """Возвращает (DATA_BLOB, буфер) — буфер держим живым до конца вызова."""
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def protect(text: str) -> str:
    src, _keep = _blob(text.encode("utf-8"))
    ent, _keep2 = _blob(ENTROPY)
    out = DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(src), "LocalAgent", ctypes.byref(ent),
        None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData")
    try:
        return base64.b64encode(ctypes.string_at(out.pbData, out.cbData)).decode("ascii")
    finally:
        kernel32.LocalFree(out.pbData)


def unprotect(packed: str) -> str:
    raw = base64.b64decode(packed.encode("ascii"))
    src, _keep = _blob(raw)
    ent, _keep2 = _blob(ENTROPY)
    out = DATA_BLOB()
    descr = wintypes.LPWSTR()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(src), ctypes.byref(descr), ctypes.byref(ent),
        None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
    )
    if descr:
        kernel32.LocalFree(descr)
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData")
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(out.pbData)
```

Три вещи, на которых легко ошибиться: не задать `argtypes` (на 64-битной винде указатели обрежутся), отпустить буфер `create_string_buffer` до вызова (мусор на входе), забыть `LocalFree` (утечка на каждый вызов).

### 5.4 Формат файла и миграция

`%LOCALAPPDATA%\LocalAgent\secrets.v2.json` (в портативном режиме — `data\secrets.v2.json`):

```json
{
  "v": 2,
  "items": {
    "telegram.bot_token": {"blob": "AQAAANCMnd8BFdERjHoA...", "updated": "2026-11-18T09:40:12+02:00", "hint": "bot123456:••••7fQ"},
    "google.oauth": {"blob": "AQAAANCMnd8BFdERjHoA...", "updated": "2026-11-18T09:41:03+02:00", "hint": "user@gmail.com"}
  }
}
```

- `blob` — только DPAPI, ничего открытым текстом. `hint` — не секрет: маска токена или e-mail, чтобы UI мог показать «к какому аккаунту подключено».
- Файл создаётся с явным ACL: `icacls secrets.v2.json /inheritance:r /grant:r "%USERNAME%":F`. Наследование от `%LOCALAPPDATA%` и так ограничено, но проверка не помешает.
- `v` — версия формата. Незнакомая версия → «требуется обновление приложения», а не тихая попытка прочитать.

Миграция с текущего `keys.json` (PBKDF2-схема) — одноразовая и обратимая:

```python
def get(name: str) -> str:
    item = _load_v2()["items"].get(name)
    if item:
        return dpapi.unprotect(item["blob"])          # обычный путь
    legacy = _legacy_get(name)                        # keys.json, PBKDF2
    if legacy:
        put(name, legacy)                             # переписываем в v2
        _legacy_drop(name)
        return legacy
    return ""
```

Если `unprotect` падает (другой ПК, другой профиль) — не молчим: интеграция переходит в состояние `needs_reauth`, в UI пишем «токен нельзя расшифровать на этом компьютере, войдите заново». Это ожидаемый сценарий для портативного режима.

После миграции: удалить `keys.json` и `keys.salt`, обновить запись в `MEMORY.md` (сейчас там утверждение про DPAPI, которое коду не соответствует) и раздел безопасности в `README.md`.

### 5.5 OAuth-движок (общий для Google, Microsoft, Dropbox, Slack, Notion)

Отдельный модуль `app/oauth.py`, ~250 строк, без зависимостей:

1. **Авторизация.** Случайный `state` + `code_verifier` (PKCE S256, [RFC 7636](https://datatracker.ietf.org/doc/html/rfc7636)), локальный сервер на `[IP_ADDRESS]:0` (порт выдаёт ОС), `redirect_uri = http://[IP_ADDRESS]:<port>/callback`, открытие браузера. Таймаут ожидания — 180 с, потом аккуратная ошибка. Для нативных приложений loopback — рекомендованный вариант ([RFC 8252](https://datatracker.ietf.org/doc/html/rfc8252)), регистрация `https`-схемы не нужна.
2. **Обмен кода** на `access_token` + `refresh_token`; refresh-токен сразу в DPAPI, access-токен — только в памяти процесса.
3. **Обновление** за 60 с до истечения, однократно на интеграцию (блокировка, чтобы два потока не обновили одновременно и не отозвали токен друг другу).
4. **Отзыв.** Кнопка «Отключить» дергает revoke-эндпоинт провайдера (Google: `https://oauth2.googleapis.com/revoke`, Slack: `auth.revoke`), затем удаляет блоб. Просто удалить файл недостаточно — токен останется живым на стороне провайдера.

| Провайдер | Flow | Redirect | Особенность |
|---|---|---|---|
| Google | code + PKCE | loopback | `access_type=offline`, `prompt=consent`; в статусе публикации «Testing» refresh-токен живёт **7 дней** |
| Microsoft | device code (или code + PKCE) | не нужен | public client без секрета, `tenant=common`, `offline_access` |
| Dropbox | code + PKCE | loopback | `token_access_type=offline`, scoped app |
| Slack | code (+ client secret) | loopback | user scopes, нужно одобрение админа воркспейса |
| Notion | code + Basic `client_id:client_secret` | loopback | при согласии выбирается воркспейс |
| GitLab | code / DCR | loopback | работает и на self-managed |

Google-нюанс, который ломает «подключил и забыл»: пока приложение в статусе Testing, refresh-токен отзывается через неделю. Либо публикуем приложение (для restricted-скоупов — верификация), либо используем IMAP с паролем приложения для почты и `drive.file` (нечувствительный скоуп) для файлов.

### 5.6 Правила обращения с секретами

1. Секрет живёт **только** в DPAPI-файле и в памяти процесса. В `settings.json`, `integrations.json`, логах, чатах и `/api/state` его быть не может.
2. `repr`/`str` для объектов с токенами — переопределены; в лог пишется `bot123456:••••7fQ`.
3. `redact()` применяется к тексту ошибок провайдера: многие API возвращают токен в сообщении об ошибке.
4. Access-токен не кэшируется на диск; refresh-токен — только DPAPI.
5. Никакой параметризации секретов через аргументы командной строки (`ps`/диспетчер задач видят их всем). Только env или файл с ACL.
6. Каждая интеграция имеет `expires_at`; UI показывает «истекает через N дней» и предупреждает за 3 дня.
7. «Отключить всё» — одна кнопка: revoke по всем провайдерам, затем удаление всех блобов.
8. Резервная копия `data\` содержит нерасшифровываемые блобы — это нормально, но в инструкции должно быть написано явно, иначе пользователь решит, что бэкап «сломался».

### 5.7 Альтернатива: Windows Credential Manager

`CredWrite`/`CredRead` (документация: [CredWriteW](https://learn.microsoft.com/windows/win32/api/wincred/nf-wincred-credwritew)) даёт то же шифрование плюс штатный интерфейс «Панель управления → Диспетчер учётных данных», где пользователь видит и удаляет записи.

Плюсы: прозрачно для пользователя, не нужно следить за файлом и ACL.
Минусы: лимит ≈2,5 КБ на одну запись (OAuth-набор из refresh-токена, id-токена и метаданных может не влезть), нет версионирования и атомарной записи набора ключей, сложнее тестировать.

Решение: **DPAPI-файл** как основное хранилище, Credential Manager — не используем. Плюс к этому — Windows Hello/биометрия не даёт дополнительной защиты для фонового процесса и в план не входит.

---

## 6. Интерфейс «подключено / нет»

### 6.1 Состояния

| Состояние | Цвет | Текст в карточке | Действия |
|---|---|---|---|
| `not_connected` | серый | «Не подключено» + чем полезно | [Подключить] [Документация] |
| `connecting` | синий, спиннер | «Открыл браузер, жду подтверждения…» | [Отменить] |
| `connected` | зелёный | «@bot» / «user@gmail.com», «проверено 2 мин назад» | [Проверить] [Настроить] [Отключить] |
| `needs_reauth` | жёлтый | «Нужно войти заново: истёк доступ» | [Войти заново] [Отключить] |
| `error` | красный | человеческий текст + код (`spawn_failed`) | [Повторить] [Документация] [Отключить] |
| `disabled` | серый пунктир | «Выключено» + «инструменты недоступны агенту» | [Включить] |

Правила: состояние вычисляется на сервере, UI его только рисует; переходы не анимируются дольше 300 мс; `error` никогда не показывает стектрейс и никогда — токен.

### 6.2 API

`GET /api/integrations` — состояние всех интеграций, без секретов:

```json
{
  "checked_at": "2026-11-18T10:12:03+02:00",
  "items": [
    {
      "id": "telegram", "title": "Telegram", "category": "messengers",
      "state": "connected", "account": "@my_helper_bot",
      "scopes": ["bot"], "tools": 4,
      "last_check": "2026-11-18T10:11:40+02:00", "latency_ms": 180,
      "expires_in": null, "error": null, "writable": true,
      "docs": "https://core.telegram.org/bots/api", "can_revoke": false
    },
    {
      "id": "google", "title": "Google (Calendar, Gmail)", "category": "mail",
      "state": "needs_reauth", "account": "user@gmail.com",
      "scopes": ["calendar.events", "gmail.readonly"], "tools": 9,
      "last_check": "2026-11-18T10:11:42+02:00", "latency_ms": 240,
      "expires_in": -259200, "error": "refresh_token_expired",
      "writable": false, "docs": "https://developers.google.com/calendar/api",
      "can_revoke": true
    }
  ]
}
```

Остальные методы:

| Метод | Назначение | Ответ |
|---|---|---|
| `POST /api/integrations/connect` | `{"id": "google"}` — запуск OAuth | `{"ok": true, "auth_url": "..."}` (или `{"mode": "device_code", "user_code": "..."}` для Microsoft) |
| `POST /api/integrations/test` | `{"id": "jira"}` — принудительная проверка | результат healthcheck (§8.2) |
| `POST /api/integrations/disconnect` | `{"id": "google"}` — revoke + удаление блоба | `{"ok": true}` |
| `POST /api/integrations/toggle` | `{"id": "discord", "enabled": false}` | `{"ok": true}` — выключить, не удаляя токен |
| `GET /oauth/callback` (временный loopback-порт) | принимает код только со `state`, живёт 180 с | редирект на `/` + `toast`, порт закрывается |

Все POST — с уже существующим `X-Agent-Token`. Callback не идёт через основной API: это отдельный сервер на loopback с единственной защитой — `state` и время жизни.

### 6.3 Как проверяем и кэшируем

- При старте приложения — фоновый прогрев с разбросом 0–10 с, чтобы не бить по всем API одновременно.
- `state` отдаётся из кэша (`integrations.json`, без секретов), UI рисуется сразу; реальная проверка обновляет кэш и присылает событие через существующий SSE/опрос состояния.
- TTL: `connected` — 15 минут, `error`/`needs_reauth` — 5 минут, `not_connected` — не проверяем вовсе.
- Проверка не должна блокировать чат: отдельный `ThreadPoolExecutor(max_workers=3)`, жёсткий таймаут 10 с на интеграцию.
- Circuit breaker: 3 неудачи подряд → пауза 10 минут, в карточке «повторю через 8 мин», чтобы не долбить заблокированный API.

### 6.4 Макет (панель «Настройки → Интеграции», над MCP)

```
┌ Интеграции ──────────────────────────────────────────────┐
│ [Все] [Сообщения] [Почта] [Заметки] [Файлы] [Разработка] │
│                                                          │
│ ┌ Telegram ────────────── ● подключено ────────────────┐ │
│ │ @my_helper_bot · проверено 2 мин назад               │ │
│ │ 4 инструмента: отправка, чтение, дайджест            │ │
│ │ [Проверить] [Настроить] [Отключить]                  │ │
│ └──────────────────────────────────────────────────────┘ │
│ ┌ Notion ──────────────── ○ не подключено ─────────────┐ │
│ │ Поиск и правка страниц. Доступ — в пределах ваших прав│ │
│ │ [Подключить] [Документация]                          │ │
│ └──────────────────────────────────────────────────────┘ │
│ ┌ Jira ────────────────── ▲ нужно войти заново ───────┐ │
│ │ Токен истёк 3 дня назад                              │ │
│ │ [Войти заново] [Отключить]                           │ │
│ └──────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

Детали, которые стоит сделать сразу: счётчик «N из 23 подключено» в заголовке; сортировка «сначала проблемные»; переключатель «Разрешить агенту изменять данные» на карточке (по умолчанию выключен для всего, кроме заметок и календаря); строка «инструменты появятся в чате» только у `connected`; у `disabled` — затемнение, но не удаление.

### 6.5 Тексты ошибок

| Код | Что видит пользователь |
|---|---|
| `token_expired` | «Доступ истёк — войдите заново, это займёт полминуты» |
| `invalid_token` | «Сервис не принял токен. Проверьте, что он не отозван» |
| `rate_limited` | «Сервис просит подождать: лимит запросов. Повторю через N минут» |
| `network` | «Нет связи с сервисом. Проверьте интернет или VPN» |
| `spawn_failed` | «Не нашёл программу запуска (npx/uvx). Установите Node.js или Python-инструмент» |
| `init_timeout` | «Сервер не ответил за 35 секунд — возможно, он завис» |
| `protocol_mismatch` | «Сервер требует другую версию протокола — обновите приложение» |
| `auth_required` | «Этот сервер просит вход, которого мы пока не умеем» |
| `tools_empty` | «Сервер запустился, но не отдал ни одного инструмента» |
| `permission_denied` | «У токена не хватает прав: нужно …» |

---

## 7. Шаблон описания MCP-сервера в каталоге

### 7.1 Принципы

1. **Декларативно.** Одна запись = один сервер; никакой логики в каталоге, только данные.
2. **Без секретов.** В каталоге только *ссылки* на секреты (`@secret:...`) и настройки (`@setting:...`); значения подставляются при запуске.
3. **Версионируемо.** `schema` для формата, `version` для сервера, точный пин пакета.
4. **Проверяемо.** У каждой записи есть `health` — что и как дёргать, чтобы понять «жив ли».
5. Два источника: встроенный каталог в коде и пользовательский `data/mcp_catalog.json` (приоритет у пользовательского, но с валидацией и предупреждением «непроверенный источник»).

### 7.2 Схема `schema: 1`

| Поле | Тип | Обяз. | Смысл |
|---|---|---|---|
| `schema` | int | да | версия формата описания |
| `id` | string | да | `^[a-z][a-z0-9_]{1,24}$`, уникален, попадает в имена инструментов |
| `title` | string | да | человеческое имя для UI |
| `category` | enum | да | `messengers`, `mail`, `notes`, `files`, `dev`, `data`, `other` |
| `transport` | enum | да | `stdio` \| `http` \| `sse` |
| `command`, `args` | string, [] | для stdio | запуск; `args` — только литералы, без shell |
| `url` | string | для http/sse | только `https://` |
| `env` | object | нет | `KEY: "@secret:name"` \| `"@setting:path"` \| литерал (для нечувствительных) |
| `auth.type` | enum | да | `none`, `bot_token`, `api_token`, `pat`, `basic`, `oauth2` |
| `auth.secret_key` | string | если нужен | ключ в DPAPI-хранилище |
| `auth.scopes` | [] | для oauth2 | минимально необходимые |
| `auth.docs` | url | нет | куда отправить пользователя за токеном |
| `capabilities` | object | да | `{"read": true, "write": false, "files": false, "send": false}` |
| `risk` | enum | да | `low` \| `medium` \| `high` (утечка/необратимость) |
| `requires` | [] | нет | `npx`, `uvx`, `docker`, `node>=20`, `python>=3.11` |
| `tools_hint` | [] | нет | ожидаемые имена инструментов — для проверки после обновления |
| `health` | object | да | `{probe_tool, probe_args, timeout_s, interval_s}` |
| `version` | string | да | версия сервера/пакета |
| `docs` | [] | да | 1–2 ссылки |
| `source` | enum | да | `official` \| `vendor` \| `community` |
| `license` | string | да | для аудита |
| `notes` | string | нет | подводные камни, одним абзацем |

### 7.3 Примеры

**Remote MCP с OAuth (Notion):**

```json
{
  "schema": 1,
  "id": "notion",
  "title": "Notion",
  "category": "notes",
  "transport": "http",
  "url": "https://mcp.notion.com/mcp",
  "auth": {
    "type": "oauth2",
    "secret_key": "notion.oauth",
    "scopes": [],
    "docs": "https://developers.notion.com/docs/mcp"
  },
  "capabilities": {"read": true, "write": true, "files": false, "send": false},
  "risk": "medium",
  "requires": [],
  "tools_hint": ["search", "fetch", "create_page"],
  "health": {"probe_tool": "search", "probe_args": {"query": "test"}, "timeout_s": 15, "interval_s": 900},
  "version": "remote",
  "docs": ["https://developers.notion.com/docs/mcp"],
  "source": "official",
  "license": "proprietary-service",
  "notes": "OAuth-MCP; доступ в пределах прав пользователя. Скоупы не задаются — доверяем провайдеру, но пишем это в UI."
}
```

**stdio с токеном в env (Jira):**

```json
{
  "schema": 1,
  "id": "jira",
  "title": "Jira",
  "category": "dev",
  "transport": "stdio",
  "command": "uvx",
  "args": ["mcp-atlassian==0.11.9"],
  "env": {
    "JIRA_URL": "@setting:jira.url",
    "JIRA_USERNAME": "@setting:jira.email",
    "JIRA_API_TOKEN": "@secret:jira.token",
    "READ_ONLY_MODE": "false"
  },
  "auth": {
    "type": "api_token",
    "secret_key": "jira.token",
    "docs": "https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/"
  },
  "capabilities": {"read": true, "write": true, "files": false, "send": false},
  "risk": "medium",
  "requires": ["uvx"],
  "tools_hint": ["jira_search", "jira_get_issue", "jira_create_issue"],
  "health": {"probe_tool": "jira_search", "probe_args": {"jql": "updated >= -1d", "limit": 1}, "timeout_s": 15, "interval_s": 900},
  "version": "0.11.9",
  "docs": ["https://github.com/sooperset/mcp-atlassian"],
  "source": "community",
  "license": "MIT",
  "notes": "Версия в примере условная: при добавлении фиксируется фактическая с PyPI. Write-инструменты требуют подтверждения."
}
```

**Локальный сервер без авторизации (Obsidian через FS-мост):**

```json
{
  "schema": 1,
  "id": "obsidian",
  "title": "Obsidian",
  "category": "notes",
  "transport": "stdio",
  "command": "python",
  "args": ["-m", "app.mcp_obsidian", "--vault", "@setting:obsidian.vault"],
  "env": {},
  "auth": {"type": "none"},
  "capabilities": {"read": true, "write": true, "files": true, "send": false},
  "risk": "low",
  "requires": [],
  "tools_hint": ["notes_search", "note_read", "note_append"],
  "health": {"probe_tool": "notes_search", "probe_args": {"query": ""}, "timeout_s": 10, "interval_s": 1800},
  "version": "1.0",
  "docs": ["https://github.com/coddingtonbear/obsidian-local-rest-api"],
  "source": "community",
  "license": "MIT",
  "notes": "Пишем только в конец заметки и только если mtime не изменился с момента чтения."
}
```

### 7.4 Валидация при загрузке

Проверки, без которых запись не попадает в реестр (возвращаем список ошибок, а не «не удалось»):

1. `id` по регулярке, уникален, не совпадает с именами встроенных инструментов (`read_file`, `bash`, ...).
2. `transport` и обязательные поля для него согласованы: stdio → `command`; http/sse → `url` с `https://` (исключение — loopback, для локальных серверов).
3. `command` не содержит `/`, `\`, пробелов и `;&|><` — только имя программы, ищущееся в `PATH`; `shutil.which(command)` обязателен.
4. `args` — плоский список строк, без подстановок; `env` — только `@secret:`, `@setting:` или значения из белого списка.
5. `auth.type != none` → `secret_key` существует в хранилище (или помечаем `not_connected`, а не «сломан»).
6. `health.probe_tool` есть в `tools_hint` (если `tools_hint` задан).
7. `risk == high` → в UI отдельная плашка «может отправлять данные наружу» и подтверждение при первом включении.
8. Суммарное число инструментов сервера ≤ 80: иначе схема инструментов съест контекст модели, и сервер надо включать частично (allowlist инструментов).

### 7.5 Подстановка секретов

Точка расширения в `app/mcp.py` — один метод, который собирает окружение:

```python
def _env_for(spec: dict) -> dict:
    import os
    env = {k: v for k, v in os.environ.items() if not k.upper().endswith(("_TOKEN", "_KEY", "_SECRET"))}
    for key, ref in (spec.get("env") or {}).items():
        if ref.startswith("@secret:"):
            env[key] = keystore.get(ref[8:])
        elif ref.startswith("@setting:"):
            env[key] = str(config.get_path(ref[9:]))
        else:
            env[key] = str(ref)
    return env
```

Два важных следствия: (а) родительские переменные с токенами не протекают в дочерний процесс по умолчанию; (б) `Popen(..., env=_env_for(spec), stderr=subprocess.PIPE)` плюс поток-читатель `stderr` — иначе диагностика «сервер упал» остаётся пустой.

### 7.6 Транспорты: чего не хватает

| Транспорт | Сейчас | Нужно | Дни |
|---|---|---|---|
| stdio | есть | env, stderr, kill по таймауту, сверка версии | 1 |
| Streamable HTTP | нет | POST `initialize`/`tools/list`/`tools/call`, заголовок `MCP-Protocol-Version`, `Session-Id`, потоковый ответ | 3 |
| SSE (legacy) | нет | GET-поток событий + POST на endpoint из `event: endpoint` | 1–2 |

Мост `npx mcp-remote <url>` закрывает дыру за полчаса, но хранит OAuth-токены в `~/.mcp-auth` **открытым текстом** и требует Node. Для чувствительных сервисов (почта, файлы, задачи) это неприемлемо — поэтому HTTP-транспорт в своём коде входит в спринт 0, а мост остаётся аварийным вариантом для экспериментов.

---

## 8. Проверка работоспособности

### 8.1 Пять стадий healthcheck

| # | Стадия | Что делаем | Таймаут | Что ловим |
|---|---|---|---|---|
| 1 | `spawn` | `shutil.which(command)`, проверка `requires`, запуск процесса | 6 с | `spawn_failed` |
| 2 | `initialize` | `initialize` + `notifications/initialized`, сверка `protocolVersion` и `capabilities.tools` | 8 с | `init_timeout`, `protocol_mismatch` |
| 3 | `tools/list` | непустой список, валидные `inputSchema`, уникальность имён после префикса, наличие `description` | 8 с | `tools_empty`, `schema_invalid` |
| 4 | `probe` | безопасный read-only вызов из `health.probe_tool` (1 запись/1 поиск) | 10 с | `auth_required`, `permission_denied`, `probe_failed` |
| 5 | `report` | версия сервера, число инструментов, латентность, следующий запуск | — | — |

Общий бюджет 35 с, прогресс показывается по стадиям. Текущий `TIMEOUT = 25 c` в `mcp.py` относится к одному запросу и остаётся, но стадии должны иметь свои лимиты — иначе одна зависшая стадия съедает весь бюджет.

Важно: сейчас клиент отправляет `2025-06-18`, а согласованную версию из ответа не проверяет (`tests/fake_mcp.py` отвечает `2024-11-05`, и это проходит незамеченным). На стадии 2 нужно сохранять `result.protocolVersion` и предупреждать при расхождении: часть серверов на незнакомую версию отвечает ошибкой в `tools/list`, и это выглядит как «сервер сломан».

### 8.2 Контракт ответа `/api/mcp/test`

```json
{
  "ok": true,
  "id": "jira",
  "stages": [
    {"stage": "spawn", "ok": true, "ms": 240},
    {"stage": "initialize", "ok": true, "ms": 520, "protocol": "2025-06-18", "server": "mcp-atlassian 0.11.9"},
    {"stage": "tools/list", "ok": true, "ms": 90, "tools": 37, "warnings": ["у 2 инструментов нет description"]},
    {"stage": "probe", "ok": true, "ms": 610, "tool": "jira_search"},
    {"stage": "report", "ok": true, "ms": 2}
  ],
  "tools": ["mcp__jira__jira_search", "mcp__jira__jira_get_issue"],
  "next_check": "2026-11-18T10:27:00+02:00"
}
```

Существующий ответ `{"ok": true, "tools": [...]}` остаётся совместимым: новые поля добавляются, старые не меняются — UI обновляем постепенно.

### 8.3 Коды и поведение

| Код | Когда | Что делает приложение |
|---|---|---|
| `spawn_failed` | программы нет в `PATH` | предлагает установить Node/Python, ссылка на инструкцию |
| `init_timeout` | нет ответа на `initialize` | убивает процесс, пауза 10 мин |
| `protocol_mismatch` | другая версия протокола | предупреждение, сервер помечается «ограниченно работает» |
| `auth_required` | сервер вернул 401/нужен вход | `needs_reauth`, кнопка «Войти» |
| `tools_empty` | 0 инструментов | не подключаем к агенту, показываем причину |
| `schema_invalid` | битые `inputSchema` | отбрасываем конкретный инструмент, не весь сервер |
| `probe_failed` | инструмент вернул ошибку | «сервер жив, но данные недоступны» |
| `rate_limited` | 429 | backoff по `Retry-After`, карточка «повторю через N мин» |
| `crashed` | процесс умер между вызовами | перезапуск 1 раз, затем пауза |

### 8.4 Расписание

- при добавлении сервера — полный прогон до сохранения;
- при старте приложения — ленивый прогон включённых серверов (не блокирует чат);
- каждые 15 минут для `connected`, каждые 5 для `error`;
- после смены токена или настроек — немедленно;
- circuit breaker: 3 неудачи → пауза 10 минут (защита от «долблю заблокированный API»).

### 8.5 Тесты без сети

`tests/fake_mcp.py` расширяем режимами через аргумент: `--slow` (спит 40 с), `--crash` (падает после `initialize`), `--empty` (пустой `tools/list`), `--old-protocol` (2024-11-05), `--auth` (401 на `tools/call`), `--bad-schema` (`inputSchema` — строка). Один smoke-тест на каждый режим проверяет код ошибки, а не только `ok: false`.

Отдельно — контрактный тест каталога: каждая запись валидируется по §7.4, `tools_hint` сверяется с реальным `tools/list` (запускается вручную, не в CI, потому что требует сети и токенов).

### 8.6 Приёмка нового сервера в каталог

1. Источник: официальный или вендорский; для community — ≥ 500 звёзд, коммит за последние 6 месяцев, открытая лицензия.
2. Пакет зафиксирован точной версией (никаких `-y latest`).
3. Проверено, что сервер не отправляет телеметрию на сторонние домены (смотрим `tools/list` и сетевые вызовы на тестовом прогоне).
4. `tools_hint` совпадает с фактическим списком; лишние инструменты выключены allowlist'ом.
5. Healthcheck проходит все 5 стадий на чистой машине без токена (кроме `probe`, который должен дать `auth_required`).
6. Записан `risk` и минимальные скоупы; для `write` — подтверждение в UI.
7. Добавлена запись в `docs/INTEGRATIONS.md` (этот файл) и в UI-описание «что это даёт».
8. Проверено удаление: процесс не остаётся висеть, файлы не мусорятся.

---

## 9. Риски

### 9.1 Основные

| Риск | Вер. | Влияние | Меры |
|---|---|---|---|
| Утечка токенов из хранилища | средняя | высокое | DPAPI + ACL, миграция с PBKDF2, «отключить всё», регулярная ротация |
| Токен в логах/трейсбеке/дампе | высокая | высокое | `redact()` в логгере, запрет `repr` секретов, тест «в логах нет `bot`/`ya29.`/`ghp_`» |
| Токен ушёл во фронтенд | средняя | высокое | контракт `/api/state` и `/api/integrations` без секретов, тест на отсутствие ключей в JSON |
| Избыточные права | высокая | высокое | минимальные скоупы, scoped PAT, `drive.file`, read-only по умолчанию, отдельный переключатель на запись |
| Prompt injection из писем/страниц/RSS → отправка данных наружу | высокая | высокое | внешний контент помечается как недоверенный, авто-отправка запрещена, `send`/`write` только через подтверждение, лимит вызовов на ход |
| Блокировка/лимиты API | высокая | среднее | лимитер на интеграцию, backoff + `Retry-After`, кэш, оффлайн-деградация, «повторю через N мин» |
| Бан аккаунта за автоматизацию | средняя | высокое | только Bot API для Telegram, никаких self-bot в Discord, уважение ToS |
| Компрометация через `npx -y` | средняя | высокое | точный пин версии, lockfile, обзор изменений `tools/list` при обновлении, отказ от неподдерживаемых пакетов |
| Remote MCP видит данные | средняя | среднее | только официальные серверы, раскрытие в UI, отключаемость, аудит |
| Истечение refresh-токена (Google Testing — 7 дней) | высокая | среднее | состояние `needs_reauth`, предупреждение за 3 дня, инструкция «как починить» |
| Портативный режим: блобы не расшифровываются на другом ПК | средняя | низкое | явное сообщение, повторный вход, не удалять данные молча |
| Верификация Google для restricted-скоупов | высокая | среднее | `drive.file`, IMAP вместо Gmail API на старте, тестовые пользователи |
| Зависание stdio-сервера | средняя | среднее | таймауты по стадиям, kill, circuit breaker, stderr в лог |
| Секреты в `settings.json` при ручном добавлении MCP | средняя | высокое | в UI принимаем только `@secret:`, значения с признаками токена отклоняем |

### 9.2 Правила, которые нельзя нарушать

1. Секрет не покидает процесс: ни в UI, ни в лог, ни в конфиг, ни в аргументы командной строки.
2. Любая операция «наружу» (отправить сообщение, создать задачу, залить файл) — через подтверждение, пока пользователь явно не разрешил иное для конкретной интеграции.
3. Внешний текст — данные, а не инструкции. Агент не выполняет команды из письма или веб-страницы.
4. Токен с широкими правами не выдаём: если сервис не умеет узкие скоупы — интеграция не нужна.
5. У каждой интеграции есть выключатель и кнопка отзыва; «удалить токен» без revoke считается ошибкой.

### 9.3 Минимальные права по интеграциям

| Интеграция | Минимум для чтения | Что добавляет запись | Что ломается при урезании |
|---|---|---|---|
| Telegram | бот в чате | `sendMessage` | ничего, бот и так не читает чужие чаты |
| Gmail (IMAP) | `INBOX` + поиск | `SMTP AUTH` | папки-метки недоступны |
| Google Calendar | `calendar.readonly` | `calendar.events` | нельзя создать событие |
| Google Drive | `drive.file` | `drive.file` + `drive.readonly` | не видит файлы, созданные вне приложения |
| Google Sheets | `spreadsheets.readonly` | `spreadsheets` | только чтение |
| Notion | доступ к выбранным страницам | те же права на запись | вне расшаренных страниц ничего не видно |
| GitHub | `contents:read`, `issues:read` | `issues:write`, `pull_requests:write` | нельзя создать issue/PR |
| Jira | `BROWSE_PROJECTS` | `CREATE_ISSUES`, `ADD_COMMENTS` | только чтение |
| Slack | `search:read`, `channels:read` | `chat:write` | нельзя отправлять |
| Obsidian | чтение vault | запись (append) | только чтение заметок |

---

## 10. Ссылки на документацию

**Мессенджеры**
- Telegram Bot API — https://core.telegram.org/bots/api, TDLib (для варианта «от пользователя») — https://core.telegram.org/tdlib
- Slack MCP — https://docs.slack.dev/ai/mcp-server, Web API — https://api.slack.com/
- Discord — https://discord.com/developers/docs

**Почта и календарь**
- Gmail IMAP/SMTP — https://developers.google.com/gmail/imap, Gmail API — https://developers.google.com/gmail/api
- Google Calendar API — https://developers.google.com/calendar/api
- Microsoft Graph — https://learn.microsoft.com/graph/, OneDrive в Graph — https://learn.microsoft.com/graph/api/resources/onedrive
- Device code flow — https://learn.microsoft.com/entra/identity-platform/v2-oauth2-device-code

**Заметки**
- Notion API — https://developers.notion.com/, Notion MCP — https://developers.notion.com/docs/mcp
- Obsidian Local REST API — https://github.com/coddingtonbear/obsidian-local-rest-api, mcp-obsidian — https://github.com/MarkusPfundstein/mcp-obsidian
- Evernote (legacy) — https://dev.evernote.com/

**Файлы**
- Google Drive API — https://developers.google.com/drive/api
- Dropbox HTTP API — https://www.dropbox.com/developers/documentation/http/documentation, Dropbox MCP (beta) — https://help.dropbox.com/integrations/connect-dropbox-mcp-server
- rclone (как альтернатива «файлы через ФС») — https://rclone.org/

**Разработка**
- GitHub MCP — https://github.com/github/github-mcp-server, REST — https://docs.github.com/rest
- GitLab MCP — https://docs.gitlab.com/user/gitlab_duo/model_context_protocol/mcp_server/, REST — https://docs.gitlab.com/api/rest/
- Jira REST v3 — https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/, Rovo MCP — https://developer.atlassian.com/cloud/rovo-mcp/guides/getting-started, mcp-atlassian — https://github.com/sooperset/mcp-atlassian
- Linear MCP — https://linear.app/docs/mcp, GraphQL — https://linear.app/developers/graphql

**Данные**
- Google Sheets API — https://developers.google.com/sheets/api
- Airtable MCP — https://airtable.com/developers/agents/mcp/getting-started, Web API — https://airtable.com/developers/web/api/introduction
- 1С OData — https://www.odata.org/documentation/ (документация 1С на ИТС, по подписке)
- МойСклад JSON API 1.2 — https://dev.moysklad.ru/doc/api/remap/1.2/

**Прочее**
- RSS/Atom — https://feedparser.readthedocs.io/
- Open-Meteo — https://open-meteo.com/en/docs, OpenWeatherMap — https://openweathermap.org/api
- Playwright MCP — https://github.com/microsoft/playwright-mcp, Chrome DevTools MCP — https://github.com/ChromeDevTools/chrome-devtools-mcp

**Протокол и безопасность**
- MCP спецификация (2025-06-18) — https://modelcontextprotocol.io/specification/2025-06-18
- MCP Authorization — https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
- Официальные/референсные серверы MCP — https://github.com/modelcontextprotocol/servers
- DPAPI: CryptProtectData — https://learn.microsoft.com/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata
- Credential Manager: CredWriteW — https://learn.microsoft.com/windows/win32/api/wincred/nf-wincred-credwritew
- OAuth 2.0 для нативных приложений (RFC 8252) — https://datatracker.ietf.org/doc/html/rfc8252
- Google OAuth 2.0 (сроки refresh-токена, статус Testing) — https://developers.google.com/identity/protocols/oauth2
- PKCE (RFC 7636) — https://datatracker.ietf.org/doc/html/rfc7636
- Google Workspace MCP (для справки, требует Preview Program) — https://developers.google.com/workspace/guides/configure-mcp-servers

---

## 11. Приложение: чек-лист релиза интеграции

- [ ] Токен хранится через DPAPI, `hint` не содержит секрета, ACL выставлен.
- [ ] Сервис есть в `/api/integrations`, состояние и аккаунт видны в UI.
- [ ] Есть `health`-проба, которая проходит без токена до `auth_required`, и с токеном — до `probe`.
- [ ] Read-инструменты работают без подтверждения; write/send — только через `approval_mode`.
- [ ] В логах нет токенов (тест на `bot\d+:|ya29\.|ghp_|xox[bp]-|ntn_`).
- [ ] «Отключить» вызывает revoke и удаляет блоб; повторное подключение работает.
- [ ] Истёкший токен переводит карточку в `needs_reauth`, а не в общую ошибку.
- [ ] Описан риск и минимальные права в этом документе и в UI.
- [ ] Есть smoke-тест с mock-сервером (для MCP) или записанными ответами (для REST).
- [ ] Обновлены `README.md` и `MEMORY.md` (в частности, утверждение про DPAPI).
