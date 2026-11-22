"""Проверка зрения: поиск проекторов, запуск слота, ответ по картинке."""
import pathlib
import struct
import sys
import time
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import engine, vision

TEXT_MODEL = pathlib.Path(
    "D:/Strata/cache/hub/models--IFM--K2-Horizon-MoVA-36B-A4B-GGUF/snapshots/"
    "00cce3151bdce384661cb33603853ccfe6209de9/K2-Horizon-MoVA-36B-A4B-Q4_K_M.gguf"
)
PNG_SIGN = bytes.fromhex("89504e470d0a1a0a")


def make_png(path: pathlib.Path, size=256, square=110) -> pathlib.Path:
    """Белый фон и красный квадрат по центру — без сторонних библиотек."""
    rows = []
    left = (size - square) // 2
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            inside = left <= x < left + square and left <= y < left + square
            row += bytes((200, 40, 40)) if inside else bytes((245, 245, 245))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = PNG_SIGN
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)
    return path


def main() -> int:
    items = vision.candidates()
    print("1. моделей с проектором:", len(items))
    for item in items[:5]:
        print("   %-42s %5.1f ГБ  vl=%s  %s" % (item["name"][:42], item["size_gb"],
                                                item["vision_hint"], pathlib.Path(item["projector"]).name))
    if not items:
        print("НЕТ моделей с mmproj — проверка невозможна")
        return 1

    pick = vision.pick()
    print("2. выбор по умолчанию:", pathlib.Path(pick).name,
          "| признак зрения:", any(item["id"] == pick and item["vision_hint"] for item in items))
    print("3. проектор для выбранной:", pathlib.Path(engine.find_projector(pick)).name)
    if TEXT_MODEL.exists():
        print("4. у текстовой модели проектор:", repr(engine.find_projector(str(TEXT_MODEL))))

    started = vision.start(pick)
    print("5. запуск слота:", started.get("ok"), started.get("error") or "")
    if not started.get("ok"):
        return 1
    info = vision.status()
    print("   память:", info["memory_mb"], "МБ, порт", info["port"], "| модель:", info["model"])

    reprise = vision.start(pick)
    print("6. повторный запуск берёт готовый слот:", reprise.get("reused") is True)

    shot = make_png(ROOT / "tests" / "vision_probe.png")
    t0 = time.time()
    answer = vision.ask([str(shot)], "Что за фигура в центре картинки и какого она цвета? Ответь одним предложением.")
    print("7. ответ за %.1f с:" % (time.time() - t0), answer)
    ok = False
    if answer.get("ok"):
        low = answer["text"].lower()
        ok = ("красн" in low) and ("квадрат" in low or "прямоуголь" in low)
    print("   признаки распознаны:", ok)

    vision.stop()
    print("8. слот остановлен, pid-файл остался:", vision.PID_FILE.exists(), "| статус:", vision.status()["running"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
