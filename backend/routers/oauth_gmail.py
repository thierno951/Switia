"""
Gmail OAuth2 router — lets users connect their Gmail account via Google OAuth flow.
Stored credentials are then used by the Gmail API (list, send) and by IMAP/SMTP via XOAUTH2.
"""
import os
import uuid
import warnings
import logging
import base64
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from config import db
from utils import get_current_user
from cryptography.fernet import Fernet
import hashlib

logger = logging.getLogger(__name__)
router = APIRouter()

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
REDIRECT_URI = f"{PUBLIC_URL}/api/oauth/gmail/callback"

SCOPES = [
    "https://mail.google.com/",  # full IMAP/SMTP access (covers read + send + modify)
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


# --- Token encryption (reuse same Fernet seed pattern as channels.py) ---
def _get_fernet() -> Fernet:
    seed = (os.environ.get("CHANNELS_ENCRYPT_KEY") or os.environ.get("MONGO_URL") or "switia-fallback").encode()
    digest = hashlib.sha256(seed).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(value: str) -> str:
    return _get_fernet().encrypt((value or "").encode()).decode() if value else ""


def _decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except Exception:
        return ""


def _client_config() -> dict:
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }


@router.get("/oauth/gmail/login")
async def gmail_oauth_login(request: Request):
    """Start OAuth flow: generate state, redirect user to Google consent screen."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="OAuth Google non configuré côté serveur")
    user = await get_current_user(request)
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",  # force Google to always return a refresh_token
        include_granted_scopes="true",
    )
    # Persist state for 10 min to bind it to the user
    await db.oauth_states.insert_one({
        "state": state,
        "user_id": user["id"],
        "provider": "gmail",
        "created_at": datetime.now(timezone.utc),
    })
    return {"auth_url": auth_url}


@router.get("/oauth/gmail/callback")
async def gmail_oauth_callback(request: Request, code: str = None, state: str = None, error: str = None):
    """Handle Google's redirect: exchange code for tokens, persist them, redirect to /channels."""
    redirect_target = f"{PUBLIC_URL}/channels"
    if error:
        return RedirectResponse(f"{redirect_target}?gmail_error={error}")
    if not code or not state:
        return RedirectResponse(f"{redirect_target}?gmail_error=missing_params")

    state_doc = await db.oauth_states.find_one({"state": state, "provider": "gmail"})
    if not state_doc:
        return RedirectResponse(f"{redirect_target}?gmail_error=invalid_state")
    # Consume state
    await db.oauth_states.delete_one({"_id": state_doc["_id"]})
    user_id = state_doc["user_id"]

    try:
        flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # Google reorders scopes — ignore that warning
            flow.fetch_token(code=code)
        creds = flow.credentials

        # Fetch user's email address via Gmail API
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        email_address = profile.get("emailAddress", "")

        expires_at = creds.expiry
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        await db.gmail_tokens.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "user_id": user_id,
                    "email_address": email_address,
                    "access_token": _encrypt(creds.token),
                    "refresh_token": _encrypt(creds.refresh_token or ""),
                    "token_uri": creds.token_uri,
                    "expires_at": expires_at,
                    "scopes": SCOPES,
                    "connected_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )

        # Also mark the email channel as connected in the user's channel_configs
        await db.channel_configs.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "email.enabled": True,
                    "email.email_address": email_address,
                    "email.imap_host": "imap.gmail.com",
                    "email.imap_port": 993,
                    "email.smtp_host": "smtp.gmail.com",
                    "email.smtp_port": 587,
                    "email.oauth_provider": "gmail",
                    "email.updated_at": datetime.now(timezone.utc),
                },
                "$setOnInsert": {"user_id": user_id},
            },
            upsert=True,
        )
    except Exception as e:
        logger.error(f"Gmail OAuth callback error: {e}")
        return RedirectResponse(f"{redirect_target}?gmail_error=token_exchange_failed")

    return RedirectResponse(f"{redirect_target}?gmail_connected=1")


@router.get("/oauth/gmail/status")
async def gmail_oauth_status(request: Request):
    user = await get_current_user(request)
    tok = await db.gmail_tokens.find_one({"user_id": user["id"]})
    if not tok:
        return {"connected": False}
    return {
        "connected": True,
        "email_address": tok.get("email_address"),
        "connected_at": tok["connected_at"].isoformat() if isinstance(tok.get("connected_at"), datetime) else None,
    }


@router.post("/oauth/gmail/disconnect")
async def gmail_oauth_disconnect(request: Request):
    user = await get_current_user(request)
    await db.gmail_tokens.delete_one({"user_id": user["id"]})
    await db.channel_configs.update_one(
        {"user_id": user["id"]},
        {"$unset": {"email.oauth_provider": ""}, "$set": {"email.enabled": False}},
    )
    return {"ok": True}


async def get_gmail_credentials(user_id: str) -> Credentials | None:
    """Helper used by other routers — returns a fresh Credentials object or None."""
    tok = await db.gmail_tokens.find_one({"user_id": user_id})
    if not tok:
        return None
    access = _decrypt(tok.get("access_token", ""))
    refresh = _decrypt(tok.get("refresh_token", ""))
    creds = Credentials(
        token=access,
        refresh_token=refresh,
        token_uri=tok.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    expires = tok.get("expires_at")
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    needs_refresh = (not expires) or (datetime.now(timezone.utc) >= expires)
    if needs_refresh and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
            new_expiry = creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry and creds.expiry.tzinfo is None else creds.expiry
            await db.gmail_tokens.update_one(
                {"user_id": user_id},
                {"$set": {"access_token": _encrypt(creds.token), "expires_at": new_expiry}},
            )
        except Exception as e:
            logger.error(f"Gmail token refresh failed: {e}")
            return None
    return creds


@router.post("/oauth/gmail/test")
async def gmail_test(request: Request):
    """Ping Gmail API to confirm the connection works."""
    user = await get_current_user(request)
    creds = await get_gmail_credentials(user["id"])
    if not creds:
        raise HTTPException(status_code=400, detail="Gmail non connecté")
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        return {"ok": True, "email": profile.get("emailAddress"), "messages_total": profile.get("messagesTotal")}
    except Exception as e:
        logger.error(f"Gmail test error: {e}")
        raise HTTPException(status_code=400, detail=f"Erreur API Gmail : {str(e)[:160]}")
