"""Secretos en disco cifrados con DPAPI de Windows (solo el mismo usuario de Windows puede leerlos).

Se usan para el Client Secret de la app de TikTok y los tokens de la cuenta conectada. En otros
sistemas (tests en CI) se guarda en claro con una marca, para no romper nada.
"""
from __future__ import annotations

import base64
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(_blob(data)), None, None, None, None, 0x1, ctypes.byref(out))  # 0x1 = sin interfaz
    if not ok:
        raise OSError("DPAPI no pudo " + ("cifrar" if protect else "descifrar") + " el dato")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def seal(value: dict) -> str:
    raw = json.dumps(value).encode()
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode()
    return "plain:" + base64.b64encode(raw).decode()


def unseal(token: str) -> dict:
    kind, _, data = token.partition(":")
    raw = base64.b64decode(data)
    if kind == "dpapi":
        raw = _dpapi(raw, False)
    return json.loads(raw)


class Vault:
    """Un archivo JSON con campos sellados. Escritura atómica (los refresh tokens de TikTok rotan)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return unseal(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - archivo de otro usuario o dañado: como si no hubiera nada
            return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(seal(data), encoding="utf-8")
        tmp.replace(self.path)

    def update(self, **fields) -> dict:
        data = {**self.load(), **fields}
        self.save(data)
        return data
