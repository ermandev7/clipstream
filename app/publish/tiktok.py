"""TikTok Content Posting API (Direct Post) con inicio de sesión para escritorio (Login Kit Desktop + PKCE).

Referencias (consultadas en noviembre de 2026):
- https://developers.tiktok.com/doc/login-kit-desktop
- https://developers.tiktok.com/doc/oauth-user-access-token-management
- https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
- https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info
- https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status
- https://developers.tiktok.com/doc/content-sharing-guidelines

Notas:
- PKCE: la documentación de escritorio pide el SHA-256 del verificador en HEXADECIMAL (no base64url).
- El refresh token rota en cada renovación: siempre se guarda el último.
- Mientras la app no pase la auditoría de TikTok, todo lo publicado queda privado (SELF_ONLY).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

import httpx
from config.settings import settings

from app.publish.vault import Vault

log = logging.getLogger(__name__)
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
CREATOR_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
SCOPES = "user.info.basic,video.publish"
CALLBACK_PATH = "/api/publish/tiktok/callback/"
PRIVACY = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")
MUSIC_URL = "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en"
BC_POLICY_URL = "https://www.tiktok.com/legal/page/global/bc-policy/en"
DAILY_CAP = 15  # TikTok: ~15 publicaciones al día por cuenta, compartidas entre todas las apps
SINGLE_CHUNK_MAX = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024  # entre 5 y 64 MB; el último trozo absorbe el resto (≤ 128 MB)

vault = Vault(settings.storage_dir / "tiktok.vault")
_pending: dict[str, tuple[str, float]] = {}  # state -> (code_verifier, creado)
_lock = threading.Lock()


class TikTokError(RuntimeError):
    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def redirect_uri() -> str:
    return f"http://127.0.0.1:{settings.port}{CALLBACK_PATH}"


# ---- configuración ---------------------------------------------------------------------------

def set_keys(client_key: str, client_secret: str) -> None:
    data = vault.load()
    if data.get("client_key") and data["client_key"] != client_key.strip():
        data.pop("tokens", None)  # otra app de TikTok: la cuenta conectada ya no vale
    data.update(client_key=client_key.strip(), client_secret=client_secret.strip())
    vault.save(data)


def configured() -> bool:
    d = vault.load()
    return bool(d.get("client_key") and d.get("client_secret"))


def connected() -> bool:
    return bool((vault.load().get("tokens") or {}).get("refresh_token"))


def public_status() -> dict:
    d = vault.load()
    key = d.get("client_key") or ""
    tok = d.get("tokens") or {}
    return {"configured": configured(), "client_key_hint": (key[:4] + "…" + key[-3:]) if len(key) > 8 else key,
            "redirect_uri": redirect_uri(), "connected": connected(), "scope": tok.get("scope", ""),
            "open_id": tok.get("open_id", ""), "refresh_expires_at": tok.get("refresh_expires_at")}


# ---- OAuth (escritorio + PKCE) -----------------------------------------------------------------

def _challenge(verifier: str) -> str:
    return hashlib.sha256(verifier.encode()).hexdigest()  # TikTok escritorio: hexadecimal


def authorize_url() -> str:
    d = vault.load()
    if not configured():
        raise TikTokError("Primero introduce el Client Key y el Client Secret de tu app de TikTok")
    verifier = secrets.token_urlsafe(72)[:96]  # 43-128 caracteres de [A-Za-z0-9-._~]
    state = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        for k in [k for k, (_, t) in _pending.items() if now - t > 900]:
            _pending.pop(k, None)
        _pending[state] = (verifier, now)
    return AUTH_URL + "?" + urlencode({
        "client_key": d["client_key"], "scope": SCOPES, "response_type": "code", "redirect_uri": redirect_uri(),
        "state": state, "code_challenge": _challenge(verifier), "code_challenge_method": "S256"})


def _save_tokens(res: dict) -> None:
    now = time.time()
    vault.update(tokens={
        "access_token": res["access_token"], "refresh_token": res["refresh_token"],
        "expires_at": now + int(res.get("expires_in", 7200)),
        "refresh_expires_at": now + int(res.get("refresh_expires_in", 15552000)),
        "open_id": res.get("open_id", ""), "scope": res.get("scope", "")})


def _post_form(url: str, data: dict) -> dict:
    r = httpx.post(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded",
                                             "Cache-Control": "no-cache"}, timeout=30)
    try:
        res = r.json()
    except ValueError:
        res = {}
    if r.status_code >= 400 or res.get("error") not in (None, "", "ok"):
        raise TikTokError(res.get("error_description") or res.get("error") or f"HTTP {r.status_code}",
                          str(res.get("error") or r.status_code))
    return res


def handle_callback(code: str | None, state: str | None, error: str | None, error_description: str | None) -> None:
    if error:
        raise TikTokError(error_description or error, error)
    with _lock:
        entry = _pending.pop(state or "", None)
    if not entry or not code:
        raise TikTokError("La autorización no coincide con ninguna solicitud de esta app (state). Vuelve a conectar.")
    d = vault.load()
    res = _post_form(TOKEN_URL, {"client_key": d["client_key"], "client_secret": d["client_secret"], "code": code,
                                 "grant_type": "authorization_code", "redirect_uri": redirect_uri(),
                                 "code_verifier": entry[0]})
    if "video.publish" not in (res.get("scope") or ""):
        log.warning("TikTok no concedió video.publish (scope=%s)", res.get("scope"))
    _save_tokens(res)


def access_token() -> str:
    with _lock:
        d = vault.load()
        tok = d.get("tokens") or {}
        if not tok.get("refresh_token"):
            raise TikTokError("No hay ninguna cuenta de TikTok conectada", "not_connected")
        if tok.get("expires_at", 0) - time.time() > 300:
            return tok["access_token"]
        if tok.get("refresh_expires_at", 0) < time.time():
            vault.update(tokens={})
            raise TikTokError("La conexión con TikTok caducó: vuelve a conectar la cuenta", "refresh_expired")
        res = _post_form(TOKEN_URL, {"client_key": d["client_key"], "client_secret": d["client_secret"],
                                     "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        _save_tokens(res)  # el refresh token anterior deja de valer: se guarda el nuevo ya
        return res["access_token"]


def disconnect() -> None:
    d = vault.load()
    tok = (d.get("tokens") or {}).get("access_token")
    if tok and configured():
        try:
            _post_form(REVOKE_URL, {"client_key": d["client_key"], "client_secret": d["client_secret"], "token": tok})
        except TikTokError as e:
            log.warning("TikTok revoke: %s", e)
    d.pop("tokens", None)
    vault.save(d)


# ---- Content Posting API -------------------------------------------------------------------------

def _api(url: str, body: dict | None = None) -> dict:
    r = httpx.post(url, json=body if body is not None else None, timeout=30,
                   headers={"Authorization": f"Bearer {access_token()}", "Content-Type": "application/json; charset=UTF-8"})
    try:
        res = r.json()
    except ValueError:
        raise TikTokError(f"Respuesta no válida de TikTok (HTTP {r.status_code})") from None
    err = res.get("error") or {}
    if err.get("code") not in (None, "ok"):
        raise TikTokError(_friendly(err.get("code", ""), err.get("message", "")), err.get("code", ""))
    return res.get("data") or {}


FRIENDLY = {
    "spam_risk_too_many_posts": "TikTok limita las publicaciones diarias de esta cuenta: inténtalo más tarde",
    "spam_risk_user_banned_from_posting": "TikTok no permite publicar a esta cuenta ahora mismo",
    "reached_active_user_cap": "La app de TikTok alcanzó su límite diario de usuarios: inténtalo mañana",
    "unaudited_client_can_only_post_to_private_accounts":
        "Tu app de TikTok aún no está auditada: solo puede publicar en privado y la cuenta debe ser privada",
    "privacy_level_option_mismatch": "La opción de privacidad elegida no está disponible para esta cuenta",
    "access_token_invalid": "La conexión con TikTok no es válida: vuelve a conectar la cuenta",
    "scope_not_authorized": "Falta el permiso video.publish: vuelve a conectar la cuenta y acéptalo",
    "rate_limit_exceeded": "Demasiadas peticiones seguidas a TikTok: espera un minuto",
}


def _friendly(code: str, message: str) -> str:
    return FRIENDLY.get(code, f"TikTok: {message or code}")


def creator_info() -> dict:
    """Se consulta cada vez que se abre la ventana de publicar (lo exige TikTok: nada de caché)."""
    return _api(CREATOR_URL)


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) según la guía de transferencia de TikTok."""
    if size <= SINGLE_CHUNK_MAX:
        return size, 1
    return CHUNK, size // CHUNK  # el último trozo lleva el resto


def upload_video(mp4: Path, post_info: dict, progress: Callable[[float], None] | None = None) -> str:
    """Inicia la publicación directa, sube el archivo por trozos y devuelve el publish_id."""
    size = mp4.stat().st_size
    chunk, count = chunk_plan(size)
    data = _api(INIT_URL, {"post_info": post_info, "source_info": {
        "source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}})
    publish_id, url = data["publish_id"], data["upload_url"]
    with mp4.open("rb") as f:
        for i in range(count):
            first = i * chunk
            last = size - 1 if i == count - 1 else first + chunk - 1
            f.seek(first)
            body = f.read(last - first + 1)
            for attempt in range(3):
                r = httpx.put(url, content=body, timeout=300, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(body)),
                    "Content-Range": f"bytes {first}-{last}/{size}"})
                if r.status_code in (200, 201, 206):
                    break
                if r.status_code < 500 or attempt == 2:
                    raise TikTokError(f"La subida a TikTok falló (HTTP {r.status_code})", str(r.status_code))
                time.sleep(2 + attempt * 3)
            if progress:
                progress((last + 1) / size)
    return publish_id


def post_status(publish_id: str) -> dict:
    return _api(STATUS_URL, {"publish_id": publish_id})
