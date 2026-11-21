"""Проверка контраста палитр скинов по WCAG: текст на фоне, панель, акцент."""
import pathlib
import re
import sys

CSS = pathlib.Path(__file__).resolve().parent.parent / "app" / "ui" / "style.css"
text = CSS.read_text(encoding="utf-8")


def block(selector: str) -> dict:
    pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
    found = re.search(pattern, text)
    if not found:
        return {}
    pairs = {}
    for name, value in re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", found.group(1)):
        pairs[name] = value
    return pairs


def rgb(hex_value: str) -> tuple:
    value = hex_value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def luminance(hex_value: str) -> float:
    def channel(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb(hex_value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(first: str, second: str) -> float:
    a, b = luminance(first), luminance(second)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


SCOPES = [
    ("Графит тёмный", ":root", {}),
    ("Графит светлый", "body.light", {}),
    ("Океан тёмный", "body.skin-ocean", {}),
    ("Океан светлый", "body.skin-ocean.light", {}),
    ("Песок тёмный", "body.skin-sand", {}),
    ("Песок светлый", "body.skin-sand.light", {}),
]

PAIRS = [("text", "bg"), ("text", "panel"), ("muted", "panel"), ("accent", "bg"), ("ok", "panel"), ("err", "panel")]
BUTTON_TEXT = "#08101f"

problems = []
print("%-16s %s" % ("палитра", "text/bg  text/panel  muted/panel  accent/bg  ok/panel  err/panel"))
for label, selector, _ in SCOPES:
    palette = block(selector)
    if not palette:
        problems.append("%s: палитра не найдена" % label)
        continue
    ratios = []
    for first, second in PAIRS:
        if first not in palette or second not in palette:
            problems.append("%s: нет переменной %s или %s" % (label, first, second))
            ratios.append(0.0)
            continue
        value = contrast(palette[first], palette[second])
        ratios.append(value)
        need = 4.5 if first in ("text", "muted") else 3.0
        if value < need:
            problems.append("%s: %s на %s = %.2f (нужно %.1f)" % (label, first, second, value, need))
    button = contrast(palette.get("accent", "#000000"), BUTTON_TEXT) if palette.get("accent") else 0.0
    if button and button < 4.5:
        problems.append("%s: тёмный текст на акценте = %.2f (нужно 4.5)" % (label, button))
    print("%-16s %s  кнопка %.2f" % (label, "  ".join("%.2f" % r for r in ratios), button))

print()
if problems:
    print("ПРОБЛЕМЫ:")
    for item in problems:
        print(" -", item)
    sys.exit(1)
print("все пары проходят порог: текст 4.5, акцент 3.0")
