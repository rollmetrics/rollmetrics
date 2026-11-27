"""Google Sign-In OAuth2 ID token verification and email allowlist enforcement."""

from __future__ import annotations

import contextlib
import json
import time
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, WebSocket, status
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from rollmetrics.config import Settings, get_settings


def _extract_bearer_token(auth_header: str | None, query_token: str | None = None) -> str:
    if auth_header:
        parts = auth_header.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    if query_token and query_token.strip():
        return query_token.strip()
    return ""


def verify_google_token_and_allowlist(token: str, settings: Settings) -> dict[str, Any]:
    """Verify a Google OAuth2 ID token and enforce the RollMetrics email allowlist."""
    audience = settings.google_client_id.strip() or None
    try:
        claims = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=audience,
        )
    except (ValueError, GoogleAuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired Google ID token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if not isinstance(claims, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token claims.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if claims.get("email_verified") is False:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google account email is unverified.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google ID token does not contain an email claim.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    allowlist = settings.parsed_allowlist()
    if not settings.allow_open_signup and email not in allowlist:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account '{email}' is outside the authorized RollMetrics allowlist.",
        )

    return {
        "email": email,
        "name": str(claims.get("name") or email.split("@")[0]),
        "picture": str(claims.get("picture") or ""),
        "auth_mode": "google_id_token",
    }


def load_user_profiles(settings: Settings) -> dict[str, dict[str, Any]]:
    """Load persisted multi-user profiles from data/runtime/users.json."""
    path = settings.runtime_dir / "users.json"
    if not path.is_file():
        return {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k).lower(): v for k, v in raw.items() if isinstance(v, dict)}
    return {}


DEMO_SEBASTIAN_TOKEN = "demo-sebastian-bussieres"
DEMO_SEBASTIAN_EMAIL = "sebastian.demo@rollmetrics.local"
DEMO_SEBASTIAN_NAME = "Sebastian Bussieres"


def build_demo_sebastian_user() -> dict[str, Any]:
    """Return the sandboxed non-admin Sebastian Bussieres demo athlete user."""
    return {
        "email": DEMO_SEBASTIAN_EMAIL,
        "name": DEMO_SEBASTIAN_NAME,
        "picture": "",
        "auth_mode": "demo_session",
        "role": "demo",
        "teams": ["Plateau BJJ", "Caio Terra Association"],
        "academy_id": "cta",
        "academy_name": "Caio Terra Association",
        "is_admin": False,
    }


def is_admin_user(user: dict[str, Any], settings: Settings) -> bool:
    """Return True when the authenticated user is on the admin allowlist or local dev bypass."""
    if user.get("auth_mode") == "demo_session" or user.get("role") == "demo":
        return False
    if (
        settings.environment in ("local", "test")
        and settings.allow_local_dev_bypass
        and user.get("auth_mode") == "local_bypass"
    ):
        return True
    email = str(user.get("email") or "").strip().lower()
    return bool(email) and email in settings.parsed_allowlist()


def upsert_user_profile(settings: Settings, user: dict[str, Any]) -> dict[str, Any]:
    """Persist or update a signed-in user profile (including role, teams, and academy) in users.json."""
    email = str(user.get("email") or "").strip().lower()
    if not email:
        return user
    from rollmetrics.knowledge.academies import default_academy_for_email, get_academy

    default_acad_id, default_acad_name = default_academy_for_email(email, settings=settings)
    now_str = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    resolved_acad_id = default_acad_id
    resolved_acad_name = default_acad_name
    is_admin_flag = is_admin_user({**user, "email": email}, settings)
    role = (
        "demo"
        if user.get("auth_mode") == "demo_session" or email == DEMO_SEBASTIAN_EMAIL
        else ("admin" if is_admin_flag else "member")
    )
    default_teams: list[str] = (
        ["Plateau BJJ", "Caio Terra Association"]
        if (is_admin_flag or role == "demo")
        else []
    )
    resolved_teams: list[str] = list(default_teams)
    with contextlib.suppress(OSError):
        profiles = load_user_profiles(settings)
        existing = profiles.get(email, {})
        resolved_acad_id = str(
            user.get("academy_id") or existing.get("academy_id") or default_acad_id
        ).strip()
        acad_record = get_academy(settings, resolved_acad_id)
        resolved_acad_name = str(
            user.get("academy_name")
            or existing.get("academy_name")
            or acad_record.get("academy_name")
            or default_acad_name
        ).strip()
        raw_teams = user.get("teams") if isinstance(user.get("teams"), list) else existing.get("teams")
        if isinstance(raw_teams, list) and raw_teams:
            resolved_teams = [str(t).strip() for t in raw_teams if str(t).strip()]
        profiles[email] = {
            "email": email,
            "name": str(user.get("name") or existing.get("name") or email.split("@")[0]),
            "picture": str(user.get("picture") or existing.get("picture") or ""),
            "role": role,
            "teams": resolved_teams,
            "academy_id": resolved_acad_id,
            "academy_name": resolved_acad_name,
            "created_at": str(existing.get("created_at") or now_str),
            "last_seen": now_str,
        }
        out_path = settings.runtime_dir / "users.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    return {
        **user,
        "role": role,
        "teams": resolved_teams,
        "academy_id": resolved_acad_id,
        "academy_name": resolved_acad_name,
        "is_admin": is_admin_flag,
    }


def update_user_academy(
    settings: Settings,
    email: str,
    academy_id: str,
    academy_name: str,
) -> dict[str, Any]:
    """Updates a user's active academy_id and academy_name in data/runtime/users.json."""
    clean_email = (email or "").strip().lower()
    profiles = load_user_profiles(settings)
    existing = profiles.get(clean_email, {"email": clean_email, "name": clean_email.split("@")[0]})
    now_str = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    updated = {
        **existing,
        "email": clean_email,
        "academy_id": academy_id,
        "academy_name": academy_name,
        "created_at": str(existing.get("created_at") or now_str),
        "last_seen": now_str,
    }
    profiles[clean_email] = updated
    out_path = settings.runtime_dir / "users.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    return updated


def authenticate_token_or_bypass(
    token: str,
    settings: Settings,
) -> dict[str, Any]:
    """Authenticate an incoming token or permit local bypass strictly in local/test modes."""
    if token == DEMO_SEBASTIAN_TOKEN:
        return upsert_user_profile(settings, build_demo_sebastian_user())
    if token:
        return upsert_user_profile(settings, verify_google_token_and_allowlist(token, settings))

    if settings.environment in ("local", "test") and settings.allow_local_dev_bypass:
        owner_email = settings.owner_email.strip().lower()
        allowlist = sorted(settings.parsed_allowlist())
        resolved_email = owner_email or (allowlist[0] if allowlist else "coach@example.com")
        display_name = resolved_email.split("@")[0].replace(".", " ").replace("_", " ").title()
        return upsert_user_profile(
            settings,
            {
                "email": resolved_email,
                "name": display_name,
                "picture": "",
                "auth_mode": "local_bypass",
            },
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required: provide a valid Google Sign-In Bearer token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_app_settings(request: Request) -> Settings:
    """Resolve Settings bound to FastAPI app.state or load defaults."""
    bound = getattr(request.app.state, "settings", None)
    if isinstance(bound, Settings):
        return bound
    return get_settings()


async def require_user(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, Any]:
    """FastAPI dependency protecting /api/* routes."""
    token = _extract_bearer_token(
        request.headers.get("authorization"),
        request.query_params.get("token"),
    )
    return authenticate_token_or_bypass(token, settings)


async def authenticate_websocket(websocket: WebSocket, settings: Settings) -> dict[str, Any]:
    """Authenticate /ws/live via ?token= query parameter or Authorization Bearer header."""
    token = _extract_bearer_token(
        websocket.headers.get("authorization"),
        websocket.query_params.get("token"),
    )
    return authenticate_token_or_bypass(token, settings)
