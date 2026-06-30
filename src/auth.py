"""Site-wide identity: anonymous guest cookies + optional Discord OAuth.

Pattern copied from SharpLab (itsdangerous signed cookies, httpx token exchange).
Every visitor gets a `games_guest` cookie (opaque id) so their games are saved
even as a guest; logging in with Discord links that guest id to an account and
sets a signed `games_session` cookie.
"""
from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from src import store
from src.config import settings

DISCORD_API = "https://discord.com/api"
GUEST_COOKIE = "games_guest"
SESSION_COOKIE = "games_session"
SESSION_TTL = 30 * 24 * 3600  # 30 days
STATE_TTL = 600

_session = URLSafeTimedSerializer(settings.session_secret, salt="games-session")
_state = URLSafeTimedSerializer(settings.session_secret, salt="games-oauth-state")


def oauth_configured() -> bool:
    return bool(settings.discord_oauth_client_id and settings.discord_oauth_client_secret)


def _redirect_uri() -> str:
    return settings.discord_oauth_redirect_uri or f"{settings.web_base_url}/auth/discord/callback"


# --- guest id -------------------------------------------------------------
def new_guest_id() -> str:
    return secrets.token_urlsafe(12)


def ensure_guest_cookie(request: Request, response: Response) -> str:
    """Return the request's guest id, minting + setting one if absent."""
    gid = request.cookies.get(GUEST_COOKIE)
    if not gid:
        gid = new_guest_id()
        response.set_cookie(
            GUEST_COOKIE, gid, max_age=5 * 365 * 24 * 3600,
            httponly=True, samesite="lax", secure=settings.web_base_url.startswith("https"),
        )
    return gid


# --- sessions -------------------------------------------------------------
def make_session_cookie(user: dict) -> str:
    return _session.dumps({
        "id": str(user["id"]),
        "username": user.get("global_name") or user.get("username") or "Player",
        "avatar": user.get("avatar"),
    })


def read_session(cookies: dict) -> dict | None:
    raw = cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        return _session.loads(raw, max_age=SESSION_TTL)
    except (BadSignature, SignatureExpired):
        return None


def avatar_url(discord_id: str, avatar: str | None) -> str | None:
    return f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar}.png" if avatar else None


def identity(cookies: dict) -> dict:
    """Resolve a connection's identity from its cookies. Always returns a guest
    id; discord_id/username present only when logged in. Use at WS connect."""
    gid = cookies.get(GUEST_COOKIE) or new_guest_id()
    sess = read_session(cookies)
    return {
        "guest_id": gid,
        "discord_id": sess["id"] if sess else None,
        "name": sess["username"] if sess else None,
    }


# --- OAuth flow -----------------------------------------------------------
async def _exchange_code(code: str) -> dict:
    data = {
        "client_id": settings.discord_oauth_client_id,
        "client_secret": settings.discord_oauth_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(),
    }
    async with httpx.AsyncClient(timeout=10) as client:
        tok = await client.post(f"{DISCORD_API}/oauth2/token", data=data,
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
        tok.raise_for_status()
        access = tok.json()["access_token"]
        user = (await client.get(f"{DISCORD_API}/users/@me",
                                 headers={"Authorization": f"Bearer {access}"})).json()
    return user


router = APIRouter()


@router.get("/auth/discord/login")
async def discord_login():
    if not oauth_configured():
        return JSONResponse({"error": "oauth_not_configured"}, status_code=503)
    q = urlencode({
        "client_id": settings.discord_oauth_client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "identify",
        "state": _state.dumps(secrets.token_urlsafe(8)),
    })
    return RedirectResponse(f"{DISCORD_API}/oauth2/authorize?{q}")


@router.get("/auth/discord/callback")
async def discord_callback(request: Request, code: str = "", state: str = ""):
    base = settings.web_base_url
    try:
        _state.loads(state, max_age=STATE_TTL)
    except (BadSignature, SignatureExpired):
        return RedirectResponse(f"{base}/?error=bad_state")
    if not code:
        return RedirectResponse(f"{base}/?error=no_code")
    try:
        user = await _exchange_code(code)
    except Exception:
        return RedirectResponse(f"{base}/?error=oauth_failed")
    did = str(user["id"])
    uname = user.get("global_name") or user.get("username") or "Player"
    store.upsert_account(did, uname, user.get("avatar"))
    # Reconcile this browser's guest history onto the account.
    gid = request.cookies.get(GUEST_COOKIE)
    if gid:
        store.link_guest(gid, did)
    resp = RedirectResponse(f"{base}/")
    resp.set_cookie(SESSION_COOKIE, make_session_cookie(user), max_age=SESSION_TTL,
                    httponly=True, samesite="lax", secure=base.startswith("https"))
    return resp


@router.get("/auth/logout")
async def logout():
    resp = RedirectResponse(f"{settings.web_base_url}/")
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("/auth/me")
async def me(request: Request):
    sess = read_session(request.cookies)
    if not sess:
        return {"authenticated": False, "oauth": oauth_configured()}
    return {
        "authenticated": True,
        "user": {"id": sess["id"], "username": sess["username"],
                 "avatar": avatar_url(sess["id"], sess.get("avatar"))},
    }


@router.get("/api/leaderboard")
async def api_leaderboard(game: str = "", limit: int = 25, sort: str = "wins"):
    return {"leaderboard": store.leaderboard(game or None, limit=limit, sort=sort)}


@router.get("/api/profile")
async def api_profile(request: Request, game: str = ""):
    gid = request.cookies.get(GUEST_COOKIE)
    if not gid:
        return {"profile": None}
    return {"profile": store.profile(gid, game or None)}
