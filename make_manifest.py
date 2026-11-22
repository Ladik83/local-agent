"""Манифест обновления LocalAgent: sha256 архива, версия, ссылка и подпись.

  python make_manifest.py --zip dist/LocalAgent-0.7.0.zip --version 0.7.0 \
      --url https://example.com/LocalAgent-0.7.0.zip --notes "что нового" --sign

Пишет JSON {"version","url","sha256","notes"[,"signature"]} в UTF-8.
Код возврата: 0 — успех, 2 — ошибка (с понятным текстом).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PRIVATE_FILE = ROOT / "update_private.key"
PUBLIC_FILE = ROOT / "update_public.key"

VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")


def sha256_file(path: pathlib.Path) -> str:
    """SHA-256 файла, hex в нижнем регистре."""
    digest = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_version(text: str) -> str:
    """'v0.7.0' -> '0.7.0'; мусор -> ValueError."""
    value = str(text or "").strip()
    if value[:1] in ("v", "V"):
        value = value[1:].strip()
    if not VERSION_RE.match(value):
        raise ValueError("версия должна быть вида 0.7.0, получено: %r" % (text,))
    return value


def load_private_key(path: pathlib.Path | None = None) -> str:
    """Приватный ключ из файла (по умолчанию update_private.key рядом)."""
    key_path = pathlib.Path(path) if path else PRIVATE_FILE
    if not key_path.is_file():
        raise ValueError("нет файла %s — сначала выполните: python sign_update.py keygen" % key_path)
    value = key_path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("файл %s пустой" % key_path)
    return value


def build_manifest(zip_path, version: str, url: str, notes: str = "",
                   sign: bool = False, private_key: str = "",
                   key_path: pathlib.Path | None = None) -> dict:
    """Собрать словарь манифеста. Ошибки — ValueError/OSError."""
    package = pathlib.Path(zip_path)
    if not package.is_file():
        raise ValueError("архив не найден: %s" % package)
    if not package.name.lower().endswith(".zip"):
        raise ValueError("ожидается zip-архив, получено: %s" % package.name)
    clean_version = normalize_version(version)
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("не задан --url (ссылка на архив)")
    absolute = clean_url.startswith("https://") or clean_url.startswith("http://")
    relative = "/" not in clean_url.split("?")[0].split("#")[0] or not clean_url.startswith(("http", "//"))
    if not absolute and (clean_url.startswith("//") or ":" in clean_url.split("/")[0]):
        raise ValueError("ссылка должна быть https://… или именем файла рядом с манифестом")
    if not absolute and not clean_url.lower().endswith(".zip"):
        raise ValueError("относительная ссылка должна указывать на .zip")

    digest = sha256_file(package)
    data = {
        "version": clean_version,
        "url": clean_url,
        "sha256": digest,
        "notes": str(notes or ""),
    }
    if sign:
        from app import signature  # импорт только при подписи: на других ОС не нужен

        key = private_key or load_private_key(key_path)
        message = signature.manifest_message(data["version"], data["url"], data["sha256"])
        data["signature"] = signature.sign(key, message)
    return data


def write_manifest(data: dict, out_path) -> pathlib.Path:
    """Записать манифест в JSON (UTF-8, кириллица как есть)."""
    target = pathlib.Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    target.write_text(text, encoding="utf-8")
    return target


def verify_manifest(data: dict, public_key: str) -> bool:
    """Проверка подписи готового манифеста (для самоконтроля при выпуске)."""
    if not data.get("signature"):
        return False
    from app import signature

    message = signature.manifest_message(data.get("version", ""), data.get("url", ""),
                                        data.get("sha256", ""))
    return signature.verify(public_key, message, data["signature"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Манифест обновления LocalAgent")
    parser.add_argument("--zip", required=True, help="путь к архиву сборки (.zip)")
    parser.add_argument("--version", required=True, help="версия, например 0.7.0")
    parser.add_argument("--url", required=True, help="ссылка на архив для клиентов")
    parser.add_argument("--notes", default="", help="что нового (текст)")
    parser.add_argument("--out", default="manifest.json", help="куда записать манифест")
    parser.add_argument("--sign", action="store_true", help="подписать приватным ключом")
    parser.add_argument("--key", default="", help="путь к приватному ключу (по умолчанию update_private.key)")
    args = parser.parse_args(argv)

    try:
        data = build_manifest(args.zip, args.version, args.url, notes=args.notes,
                              sign=args.sign, key_path=args.key or None)
        target = write_manifest(data, args.out)
    except (ValueError, OSError) as exc:
        print("ошибка: %s" % exc, file=sys.stderr)
        return 2

    print("манифест: %s" % target)
    print("версия:   %s" % data["version"])
    print("sha256:   %s" % data["sha256"])
    print("подпись:  %s" % ("есть" if data.get("signature") else "нет (--sign не указан)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
