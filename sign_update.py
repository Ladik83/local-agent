"""Ключи и подпись манифеста обновлений (ECDSA P-256 через Windows CNG).

  python sign_update.py keygen            — создать пару ключей
  python sign_update.py sign manifest.json — подписать манифест

Публичный ключ вставляется в «Настройки» → «Открытый ключ обновлений».
Приватный ключ лежит в update_private.key и никому не передаётся.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import signature

PRIVATE_FILE = ROOT / "update_private.key"
PUBLIC_FILE = ROOT / "update_public.key"


def keygen() -> int:
    public, private = signature.generate_pair()
    PRIVATE_FILE.write_text(private, encoding="utf-8")
    PUBLIC_FILE.write_text(public, encoding="utf-8")
    print("Пара ключей создана.")
    print("Публичный ключ (вставьте в Настройки):")
    print(public)
    print("Приватный ключ: %s — не публикуйте его." % PRIVATE_FILE.name)
    return 0


def sign(path: pathlib.Path) -> int:
    if not PRIVATE_FILE.exists():
        print("Нет файла %s — сначала выполните: python sign_update.py keygen" % PRIVATE_FILE.name)
        return 2
    private = PRIVATE_FILE.read_text(encoding="utf-8").strip()
    data = json.loads(path.read_text(encoding="utf-8"))
    version = str(data.get("version") or "").strip()
    url = str(data.get("url") or "").strip()
    digest = str(data.get("sha256") or "").strip().lower()
    if not (version and url and digest):
        print("В манифесте нужны version, url и sha256")
        return 2
    message = signature.manifest_message(version, url, digest)
    data["signature"] = signature.sign(private, message)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Подпись добавлена в %s" % path.name)
    if PUBLIC_FILE.exists():
        public = PUBLIC_FILE.read_text(encoding="utf-8").strip()
        ok = signature.verify(public, message, data["signature"])
        print("Проверка публичным ключом:", "ок" if ok else "не совпало")
        return 0 if ok else 3
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    action = sys.argv[1].lower()
    if action == "keygen":
        return keygen()
    if action == "sign":
        if len(sys.argv) < 3:
            print("Укажите файл манифеста")
            return 1
        return sign(pathlib.Path(sys.argv[2]))
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
