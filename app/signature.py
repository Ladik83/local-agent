"""Подпись обновлений: ECDSA P-256 через Windows CNG (bcrypt.dll), без сторонних библиотек."""
from __future__ import annotations

import base64
import ctypes
import hashlib

from . import diag

ALG = "ECDSA_P256"
BCRYPT_ECCPUBLIC_BLOB = "ECCPUBLICBLOB"
BCRYPT_ECCPRIVATE_BLOB = "ECCPRIVATEBLOB"
PUBLIC_MAGIC = 0x31534345
PRIVATE_MAGIC = 0x32534345
KEY_BYTES = 32


def _lib():
    try:
        return ctypes.WinDLL("bcrypt")
    except OSError as exc:
        diag.warn("signature/bcrypt", exc)
        return None


def available() -> bool:
    lib = _lib()
    if lib is None:
        return False
    alg = ctypes.c_void_p()
    try:
        status = lib.BCryptOpenAlgorithmProvider(ctypes.byref(alg), ALG, None, 0)
        return status >= 0
    finally:
        if alg:
            lib.BCryptCloseAlgorithmProvider(alg, 0)


def _ok(status: int) -> None:
    if status < 0:
        raise OSError("CNG: 0x%08X" % (status & 0xFFFFFFFF))


def _open():
    lib = _lib()
    if lib is None:
        raise OSError("bcrypt.dll недоступна")
    alg = ctypes.c_void_p()
    _ok(lib.BCryptOpenAlgorithmProvider(ctypes.byref(alg), ALG, None, 0))
    return lib, alg


def _blob(magic: int, raw: bytes) -> bytes:
    return magic.to_bytes(4, "little") + KEY_BYTES.to_bytes(4, "little") + raw


def _import(lib, alg, kind: str, blob: bytes):
    key = ctypes.c_void_p()
    buf = ctypes.create_string_buffer(blob, len(blob))
    _ok(lib.BCryptImportKeyPair(alg, None, kind, ctypes.byref(key), buf, len(blob), 0))
    return key


def _export(lib, key, kind: str) -> bytes:
    size = ctypes.c_ulong(0)
    _ok(lib.BCryptExportKey(key, None, kind, None, 0, ctypes.byref(size), 0))
    buf = ctypes.create_string_buffer(size.value)
    _ok(lib.BCryptExportKey(key, None, kind, buf, size.value, ctypes.byref(size), 0))
    return buf.raw[:size.value]


def verify(public_b64: str, message: bytes, signature_b64: str) -> bool:
    """Проверить подпись манифеста открытым ключом (base64 X||Y, 64 байта)."""
    try:
        raw_key = base64.b64decode(public_b64 or "")
        signature = base64.b64decode(signature_b64 or "")
    except Exception as exc:
        diag.warn("signature/decode", exc)
        return False
    if len(raw_key) != 2 * KEY_BYTES or len(signature) != 2 * KEY_BYTES:
        return False
    try:
        lib, alg = _open()
    except OSError as exc:
        diag.warn("signature/open", exc)
        return False
    key = None
    try:
        key = _import(lib, alg, BCRYPT_ECCPUBLIC_BLOB, _blob(PUBLIC_MAGIC, raw_key))
        digest = hashlib.sha256(message).digest()
        status = lib.BCryptVerifySignature(key, None, digest, len(digest), signature, len(signature), 0)
        return status == 0
    except OSError as exc:
        diag.warn("signature/verify", exc)
        return False
    finally:
        if key:
            lib.BCryptDestroyKey(key)
        lib.BCryptCloseAlgorithmProvider(alg, 0)


def sign(private_b64: str, message: bytes) -> str:
    """Подписать сообщение приватным ключом (для инструмента сборки релиза)."""
    blob = base64.b64decode(private_b64 or "")
    lib, alg = _open()
    key = None
    try:
        key = _import(lib, alg, BCRYPT_ECCPRIVATE_BLOB, blob)
        digest = hashlib.sha256(message).digest()
        size = ctypes.c_ulong(0)
        _ok(lib.BCryptSignHash(key, None, digest, len(digest), None, 0, ctypes.byref(size), 0))
        buf = ctypes.create_string_buffer(size.value)
        _ok(lib.BCryptSignHash(key, None, digest, len(digest), buf, size.value, ctypes.byref(size), 0))
        return base64.b64encode(buf.raw[:size.value]).decode("ascii")
    finally:
        if key:
            lib.BCryptDestroyKey(key)
        lib.BCryptCloseAlgorithmProvider(alg, 0)


def generate_pair() -> tuple[str, str]:
    """Новая пара ключей: (публичный base64 X||Y, приватный blob base64)."""
    lib, alg = _open()
    key = ctypes.c_void_p()
    try:
        _ok(lib.BCryptGenerateKeyPair(alg, ctypes.byref(key), 256, 0))
        _ok(lib.BCryptFinalizeKeyPair(key, 0))
        public_blob = _export(lib, key, BCRYPT_ECCPUBLIC_BLOB)
        private_blob = _export(lib, key, BCRYPT_ECCPRIVATE_BLOB)
        return (base64.b64encode(public_blob[8:]).decode("ascii"),
                base64.b64encode(private_blob).decode("ascii"))
    finally:
        if key:
            lib.BCryptDestroyKey(key)
        lib.BCryptCloseAlgorithmProvider(alg, 0)


def manifest_message(version: str, url: str, sha256: str) -> bytes:
    """Что именно подписываем: версия, ссылка и хеш пакета."""
    return ("%s\n%s\n%s" % (version or "", url or "", sha256 or "")).encode("utf-8")
