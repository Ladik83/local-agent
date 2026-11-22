"""Навыки агента: короткие проверенные инструкции под типовые задачи.

Навык — это файл `SKILL.md` в папке навыков:

    ---
    name: правки-кода
    title: Правки кода с проверкой
    when: правка, рефакторинг, багфix, изменить код, починить
    tags: код, правка, тест
    ---
    1. ...

Агент сам подбирает 1–2 навыка под задачу (ключевые слова + смысловая близость)
и получает их как отдельное системное сообщение. Ничего не копируется у чужих
агентов: формат, подбор и тексты — свои.
"""
from __future__ import annotations

from . import config
from . import diag

import pathlib
import re
import time

CACHE = {"ts": 0.0, "items": []}
CACHE_TTL = 20.0
MAX_BODY = 1600
MAX_SKILLS = 2

BUILTIN = {
    "правки-кода": """---
name: правки-кода
title: Правки кода с проверкой
when: правка, изменить код, рефакторинг, починить, баг, ошибка в коде, доработать
tags: код, правка, тест, ошибка
---
1. Сначала прочитай файл целиком (`read_file`) — не правь по куску.
2. Найди все места использования символа (`grep`), чтобы не сломать вызовы.
3. Меняй минимально: одна правка — одна причина.
4. После правки запусти проверку проекта (`verify_project`); если упало — читай вывод и правь, пока не зелено.
5. В ответе укажи: что изменил, где, чем проверил.
""",
    "разбор-ошибки": """---
name: разбор-ошибки
title: Разбор ошибки по шагам
when: ошибка, падает, не работает, трейсбек, исключение, не запускается
tags: ошибка, отладка, лог, трейсбек
---
1. Прочитай текст ошибки целиком: тип, сообщение, файл и строку.
2. Открой место падения и 20 строк вокруг — что там происходит.
3. Проверь входные данные и состояние: пустое, None, не тот тип, не тот путь.
4. Найди похожие места (`grep` по функции или сообщению).
5. Сформулируй причину одной фразой, потом правь. Не угадывай вслепую.
""",
    "поиск-в-знаниях": """---
name: поиск-в-знаниях
title: Ответ по своим документам
when: что написано, регламент, инструкция, в документах, по базе знаний, политика
tags: знания, документы, регламент, база
---
1. Сначала `search_docs` по ключевым словам вопроса — это быстрее и точнее интернета.
2. Если фрагментов несколько — цитируй источники и номера.
3. Если в базе пусто, скажи прямо: «в базе знаний нет», и предложи индексировать папку.
4. Не выдумывай пункты документов.
""",
    "сдача-работы": """---
name: сдача-работы
title: Проверка перед сдачей
when: готово, проверь, сдай, финальная проверка, всё ли работает
tags: проверка, тест, качество
---
1. Прогони проверки проекта (`verify_project`).
2. Пробегись по списку изменений: ничего лишнего не закоммичено, нет временных файлов.
3. Проверь, что инструкция и настройки совпадают с тем, что реально работает.
4. В ответе: что проверено, что осталось, что не проверено и почему.
""",
    "поиск-по-файлам": """---
name: поиск-по-файлам
title: Найти нужное в проекте
when: найди, где используется, где лежит, поиск файла, поиск в коде
tags: поиск, файлы, grep
---
1. Маска по имени — `find` (например `*config*`), поиск по содержимому — `grep`.
2. Ищи от корня проекта, а не от домашней папки: так быстрее.
3. Если результатов много — сужай: сначала точное слово, потом регулярку.
4. Показывай путь и строку, а не пересказ.
""",
}


def skills_dir() -> pathlib.Path:
    path = pathlib.Path(str(config.load().get("skills_dir") or "")) if config.load().get("skills_dir") else config.DATA_DIR / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse(text: str) -> dict:
    meta = {}
    body = text
    found = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if found:
        for line in found.group(1).splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip().lower()] = value.strip()
        body = found.group(2)
    return {"meta": meta, "body": body.strip()}


def save(name: str, text: str) -> dict:
    """Записать навык: name — имя файла без расширения."""
    safe = re.sub(r"[^0-9A-Za-zА-Яа-я._-]", "-", str(name or "")).strip("-")[:60]
    if not safe:
        return {"ok": False, "error": "пустое имя навыка"}
    folder = skills_dir() / safe
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    CACHE["ts"] = 0.0
    return {"ok": True, "path": str(folder / "SKILL.md")}


def delete(name: str) -> dict:
    folder = skills_dir() / re.sub(r"[^0-9A-Za-zА-Яа-я._-]", "-", str(name or ""))
    file = folder / "SKILL.md"
    if not file.exists():
        return {"ok": False, "error": "навык не найден"}
    try:
        file.unlink()
        folder.rmdir()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    CACHE["ts"] = 0.0
    return {"ok": True}


def seed() -> int:
    """Разложить встроенные навыки, если папка пуста."""
    folder = skills_dir()
    if any(folder.glob("*/SKILL.md")):
        return 0
    count = 0
    for name, text in BUILTIN.items():
        save(name, text)
        count += 1
    return count


def index() -> list:
    """Все навыки: [{name, title, when, tags, body, path}]."""
    now = time.time()
    if CACHE["items"] and now - CACHE["ts"] < CACHE_TTL:
        return list(CACHE["items"])
    items = []
    for file in sorted(skills_dir().glob("*/SKILL.md")):
        try:
            raw = file.read_text(encoding="utf-8")
        except OSError as exc:
            diag.warn("skills/read", exc)
            continue
        parsed = _parse(raw)
        meta = parsed["meta"]
        items.append({
            "name": meta.get("name") or file.parent.name,
            "title": meta.get("title") or meta.get("name") or file.parent.name,
            "when": meta.get("when") or "",
            "tags": meta.get("tags") or "",
            "body": parsed["body"],
            "path": str(file),
        })
    CACHE.update({"ts": now, "items": items})
    return list(items)


def _score(task: str, item: dict) -> float:
    text = task.lower()
    words = [word.strip().lower() for word in re.split(r"[,;]", (item["when"] + "," + item["tags"])) if word.strip()]
    hits = sum(1 for word in words if word and word in text)
    return float(hits)


def _similar(task: str, items: list) -> dict:
    """Смысловая близость задачи и заголовков навыков (если есть модель эмбеддингов)."""
    try:
        from . import embed
        docs = embed.vectors([item["title"] + ". " + item["when"] for item in items], kind="document")
        query = embed.vectors([task], kind="query")
        if not docs or not query:
            return {}
        out = {}
        for item, vec in zip(items, docs):
            dot = sum(a * b for a, b in zip(query[0], vec))
            na = sum(a * a for a in query[0]) ** 0.5
            nb = sum(b * b for b in vec) ** 0.5
            out[item["name"]] = dot / (na * nb) if na and nb else 0.0
        return out
    except Exception as exc:
        diag.warn("skills/similar", exc)
        return {}


def select(task: str, limit: int = MAX_SKILLS) -> list:
    """Подобрать навыки под задачу: сначала слова, потом смысл."""
    items = index()
    if not items or not str(task or "").strip():
        return []
    text = str(task).lower()
    scored = [(item, _score(text, item)) for item in items]
    picked = [item for item, score in sorted(scored, key=lambda pair: -pair[1]) if score > 0]
    if not picked:
        sims = _similar(str(task), items)
        extra = sorted(sims.items(), key=lambda pair: -pair[1])
        for name, score in extra:
            if score < 0.78:
                continue
            found = next((item for item in items if item["name"] == name), None)
            if found and found not in picked:
                picked.append(found)
            if len(picked) >= limit:
                break
    return picked[:limit]


def context(task: str, limit: int = MAX_SKILLS) -> str:
    """Готовый блок для системного сообщения (пусто, если ничего не подошло)."""
    picked = select(task, limit)
    if not picked:
        return ""
    parts = ["Ниже — проверенные навыки под эту задачу. Следуй им, если они уместны."]
    for item in picked:
        body = item["body"][:MAX_BODY]
        parts.append("### Навык: " + item["title"] + "\n" + body)
    return "\n\n".join(parts)


def one(name: str) -> dict:
    """Один навык целиком — для редактора в интерфейсе."""
    wanted = str(name or "").strip()
    for item in index():
        if item["name"] == wanted or pathlib.Path(item["path"]).parent.name == wanted:
            return {"ok": True, "item": {key: item[key] for key in ("name", "title", "when", "tags", "body", "path")}}
    return {"ok": False, "error": "навык не найден"}


def overview() -> dict:
    items = index()
    return {
        "dir": str(skills_dir()),
        "items": [{"name": item["name"], "title": item["title"], "when": item["when"],
                   "tags": item["tags"], "chars": len(item["body"]), "path": item["path"]} for item in items],
    }
