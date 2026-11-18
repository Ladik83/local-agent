"""Keystore: API keys are stored encrypted on disk."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import sys

from . import config

PREFIX = "dpapi:"
LEGACY = "b64:"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi(raw: bytes) -> bytes:
    buf = ctypes.create_string_buffer(raw, len(raw))
    src = _Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst))
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def _undpapi(blob: bytes) -> bytes:
    buf = ctypes.create_string_buffer(blob, len(blob))
    src = _Blob(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    dst = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst))
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


STORE = config.DATA_DIR / "keys.json"
SALT = config.DATA_DIR / "keys.salt"


def _salt() -> bytes:
    try:
        return SALT.read_bytes()
    except OSError:
        raw = os.urandom(16)
        try:
            SALT.write_bytes(raw)
        except OSError:
            pass
        return raw


def _key() -> bytes:
    seed = (_salt() + os.environ.get("USERNAME", "").encode("utf-8", "replace") +
            os.environ.get("COMPUTERNAME", "").encode("utf-8", "replace"))
    return hashlib.pbkdf2_hmac("sha256", seed, b"local-agent-keys", 200000, dklen=32)


def _keystream(length: int, key: bytes, nonce: bytes) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        out += hashlib.blake2b(nonce + counter.to_bytes(4, "big"), key=key, digest_size=32).digest()
        counter += 1
    return out[:length]


def protect(text: str) -> str:
    raw = text.encode("utf-8")
    if dpapi_available():
        try:
            return PREFIX + base64.b64encode(_dpapi(raw)).decode("ascii")
        except Exception:
            pass
    key = _key()
    nonce = os.urandom(12)
    body = bytes(a ^ b for a, b in zip(raw, _keystream(len(raw), key, nonce)))
    return LEGACY + base64.b64encode(nonce + body).decode("ascii")


def unprotect(packed: str) -> str:
    packed = (packed or "").strip()
    if packed.startswith(PREFIX):
        blob = base64.b64decode(packed[len(PREFIX):].encode("ascii"))
        return _undpapi(blob).decode("utf-8")
    if packed.startswith(LEGACY):
        packed = packed[len(LEGACY):]
    key = _key()
    data = base64.b64decode(packed.encode("ascii"))
    nonce, body = data[:12], data[12:]
    raw = bytes(a ^ b for a, b in zip(body, _keystream(len(body), key, nonce)))
    return raw.decode("utf-8")


def mode() -> str:
    return "dpapi" if dpapi_available() else "legacy"


def load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(data: dict) -> None:
    try:
        STORE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def put(name: str, value: str) -> None:
    data = load()
    data[name] = protect(value)
    save(data)


def get(name: str) -> str:
    packed = load().get(name)
    if not packed:
        return ""
    try:
        return unprotect(packed)
    except Exception:
        return ""


def drop(name: str) -> None:
    data = load()
    if name in data:
        data.pop(name)
        save(data)


def names() -> list:
    return sorted(load().keys())
