"""Роли агентов: «кто что делает» — основа командного агента."""
from __future__ import annotations

from . import config
from . import tools as tools_mod

THINKING = ["none", "low", "medium", "high", "xhigh"]

DEFAULT_ROLES = [
    {
        "id": "analytics", "title": "Аналитик", "thinking": "xhigh",
        "tools": ["read_file", "list_files", "find", "grep", "run_command"],
        "prompt": "Ты аналитик. Твоя работа — разобраться в проблеме: собрать факты, найти причину, "
                  "оценить архитектуру и предложить план. Код не пишешь, файлы не меняешь. "
                  "Отвечай структурно: факты → причина → что делать.",
    },
    {
        "id": "architect", "title": "Архитектор", "thinking": "high",
        "tools": ["read_file", "list_files", "find", "grep", "run_command"],
        "prompt": "Ты проектируешь архитектуру: границы модулей, интерфейсы, потоки данных, "
                  "масштабируемость, конкурентность, миграции. Даёшь конкретные решения и компромиссы.",
    },
    {
        "id": "database", "title": "Базы данных", "thinking": "high",
        "tools": ["read_file", "list_files", "find", "grep", "run_command"],
        "prompt": "Ты специалист по базам данных (SQLite/PostgreSQL): схемы, индексы, запросы, "
                  "транзакции, миграции, блокировки, целостность. Предлагаешь конкретный SQL.",
    },
    {
        "id": "dev", "title": "Разработчик", "thinking": "low", "tools": "all", "prompt": "Ты разработчик. Реализуешь, отлаживаешь и рефакторишь код. Пишешь рабочие файлы "
                  "через инструменты, проверяешь результат, не оставляешь заглушек.",
    },
    {
        "id": "devops", "title": "DevOps", "thinking": "high",
        "tools": ["read_file", "list_files", "find", "grep", "run_command"],
        "prompt": "Ты DevOps: Windows/Linux, Docker, сети, reverse-proxy, CI/CD, деплой, мониторинг. "
                  "Даёшь команды и конфиги, которые можно выполнить как есть.",
    },
    {
        "id": "hacker", "title": "Безопасник", "thinking": "high", "tools": "all", "prompt": "Ты специалист по безопасности и мыслишь как атакующий: ищешь уязвимости "
                  "(auth/authz, повышение привилегий, IDOR/BOLA, SSRF, инъекции, секреты в коде). "
                  "основа находку с уровнем риска и способом закрыть.",
    },
    {
        "id": "performance", "title": "Производительность", "thinking": "high",
        "tools": ["read_file", "list_files", "find", "grep", "run_command"],
        "prompt": "Ты инженер по производительности: латентность, throughput, память, CPU, запросы к БД, "
                  "кэширование. Работаешь по измерениям и фактам, а не по догадкам.",
    },
    {
        "id": "product-manager", "title": "Продакт", "thinking": "medium",
        "tools": ["read_file", "list_files", "find", "grep"],
        "prompt": "Ты продакт-менеджер: превращаешь идею в чёткое требование — что делаем, для кого, "
                  "критерии готовности, что не делаем. Код не пишешь.",
    },
    {
        "id": "researcher", "title": "Исследователь", "thinking": "high",
        "tools": ["read_file", "list_files", "find", "grep", "web_search", "fetch_url"],
        "prompt": "Ты исследователь: ищешь документацию, API, стандарты, примеры, оцениваешь чужие решения. "
                  "Всегда указываешь источники ссылками.",
    },
    {
        "id": "reviewer", "title": "Ревьюер", "thinking": "high",
        "tools": ["read_file", "list_files", "find", "grep", "run_command"],
        "prompt": "Ты независимый ревьюер: проверяешь корректность, поддерживаемость, типы, архитектуру, "
                  "производительность. Ищешь проблемы, а не хвалишь. Пишешь замечания списком с приоритетом.",
    },
    {
        "id": "tester", "title": "Тестировщик", "thinking": "high",
        "tools": ["read_file", "list_files", "find", "grep", "run_command"],
        "prompt": "Ты тестировщик: проверяешь поведение, граничные случаи, регрессии. "
                  "Продакшн-код не меняешь без явной просьбы. Пишешь проверяемые шаги и ожидаемый результат.",
    },
]

ORCHESTRATOR_PROMPT = (
    "Ты оркестратор команды. Тебе дают задачу. Ты не решаешь её сам, а составляешь план: "
    "какие роли из списка нужны, в каком порядке и что именно каждая делает. "
    "Отвечай строго JSON без пояснений: "
    '{"mode": "pipeline", "steps": [{"role": "analytics", "task": "что сделать"}]}. '
    "mode = single, если хватает одной роли. Ролей не больше пяти."
)


def merge(cfg: dict) -> list:
    """Роли с учётом правок пользователя (по id)."""
    saved = {item.get("id"): item for item in (cfg.get("roles") or []) if isinstance(item, dict)}
    result = []
    for base in DEFAULT_ROLES:
        item = dict(base)
        item.update(saved.get(base["id"], {}))
        result.append(item)
    for extra in (cfg.get("roles") or []):
        if isinstance(extra, dict) and extra.get("title") and extra.get("id") not in {r["id"] for r in result}:
            result.append(extra)
    return result


def get(cfg: dict, role_id: str):
    for item in merge(cfg):
        if item["id"] == role_id:
            return item
    return None


def save(cfg: dict, rows: list) -> None:
    clean = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        clean.append({
            "id": str(row["id"]),
            "title": str(row.get("title") or row["id"]),
            "thinking": row.get("thinking") if row.get("thinking") in THINKING else "medium",
            "tools": row.get("tools") if isinstance(row.get("tools"), list) else "all",
            "prompt": str(row.get("prompt") or ""),
        })
    config.save({"roles": clean})


def tools_of(role: dict) -> list:
    tools = role.get("tools")
    if tools == "all" or tools is None:
        return [item["name"] for item in tools_mod.SCHEMAS]
    return [str(item) for item in tools]


def prompt_for(cfg: dict, role: dict) -> str:
    """Системный промпт роли + общие правила безопасности."""
    level = role.get("thinking") or "medium"
    hints = {
        "none": "",
        "low": "Думай коротко, отвечай сразу делом.",
        "medium": "Перед ответом наметь два-три шага.",
        "high": "Сначала разберись в фактах, потом предлагай решение; проверь себя на ошибки.",
        "xhigh": "Работай как следователь: факты, гипотезы, проверка каждой, затем вывод. "
                 "Не выдавай догадку за факт.",
    }
    parts = [str(role.get("prompt") or "").strip()]
    if hints.get(level):
        parts.append(hints[level])
    parts.append(
        "Работаешь на компьютере пользователя. Файлы называй полными путями. "
        "Не выдумывай содержимое файлов и результаты команд — если нужны данные, используй инструменты."
    )
    return "\n\n".join(part for part in parts if part)
