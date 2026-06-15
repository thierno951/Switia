"""
Security router — GDPR compliance + admin 2FA + audit logging.
"""
import io
import base64
import json
import logging
import pyotp
import qrcode
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Response
from config import db, COOKIE_SECURE, COOKIE_SAMESITE
from models import AccountDeletionConfirm, TwoFactorVerify, TwoFactorDisable
from utils import get_current_user, verify_password

logger = logging.getLogger(__name__)
router = APIRouter()


async def record_audit(actor: dict, action: str, target: str = "", meta: dict | None = None):
    """Record an admin action in the audit trail (fire-and-forget)."""
    try:
        await db.admin_audit_logs.insert_one({
            "actor_id": actor.get("id"),
            "actor_email": actor.get("email"),
            "actor_role": actor.get("role"),
            "action": action,
            "target": target,
            "meta": meta or {},
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.error(f"Audit log failed: {e}")


# ============ GDPR : data export & account deletion ============

COLLECTIONS_USER_SCOPED = [
    "support_conversations",
    "agent_commercial_conversations",
    "agent_rh_conversations",
    "agent_finance_conversations",
    "agent_marketing_conversations",
    "tickets",
    "analytics_data",
    "payment_transactions",
    "training_data",
    "widget_keys",
    "widget_conversations",
    "notifications",
    "agent_activity",
    "sav_tickets",
]


@router.get("/security/export")
async def export_my_data(request: Request):
    """GDPR — user can download all their personal data."""
    user = await get_current_user(request)
    user_doc = await db.users.find_one({"_id": ObjectId(user["id"])}, {"password_hash": 0, "totp_secret": 0})
    if user_doc:
        user_doc["_id"] = str(user_doc["_id"])
        for k, v in list(user_doc.items()):
            if isinstance(v, datetime):
                user_doc[k] = v.isoformat()

    export = {"user": user_doc, "collections": {}}
    for coll_name in COLLECTIONS_USER_SCOPED:
        items = []
        # owner_id for widget_keys/widget_conversations, user_id for the rest
        cursor = db[coll_name].find({"$or": [{"user_id": user["id"]}, {"owner_id": user["id"]}]})
        async for item in cursor:
            item["_id"] = str(item["_id"])
            for k, v in list(item.items()):
                if isinstance(v, datetime):
                    item[k] = v.isoformat()
                elif isinstance(v, ObjectId):
                    item[k] = str(v)
            items.append(item)
        export["collections"][coll_name] = items

    return export


@router.delete("/security/account")
async def delete_my_account(payload: AccountDeletionConfirm, request: Request, response: Response):
    """GDPR — user deletes account and all associated data. Requires current password."""
    user = await get_current_user(request)
    user_doc = await db.users.find_one({"_id": ObjectId(user["id"])})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(payload.password, user_doc["password_hash"]):
        raise HTTPException(status_code=403, detail="Mot de passe incorrect")
    # Safety: never allow deleting the last remaining admin
    if user_doc.get("role") == "admin":
        admin_count = await db.users.count_documents({"role": "admin"})
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Impossible de supprimer le dernier administrateur")

    uid = user["id"]
    for coll in COLLECTIONS_USER_SCOPED:
        await db[coll].delete_many({"$or": [{"user_id": uid}, {"owner_id": uid}]})
    await db.users.delete_one({"_id": ObjectId(uid)})

    response.delete_cookie("access_token", path="/", secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE)
    response.delete_cookie("refresh_token", path="/", secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE)
    return {"message": "Compte et données supprimés définitivement."}


# ============ 2FA (TOTP) — admins strongly encouraged ============

def _make_qr_data_uri(uri: str) -> str:
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@router.post("/security/2fa/setup")
async def setup_2fa(request: Request):
    """Generate a TOTP secret + QR code. Does NOT enable 2FA yet — user must verify first."""
    user = await get_current_user(request)
    secret = pyotp.random_base32()
    # Persist as "pending" until verified
    await db.users.update_one(
        {"_id": ObjectId(user["id"])},
        {"$set": {"totp_pending_secret": secret, "totp_pending_created_at": datetime.now(timezone.utc)}},
    )
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name="Switia")
    return {"secret": secret, "qr_data_uri": _make_qr_data_uri(uri), "otpauth_uri": uri}


@router.post("/security/2fa/verify")
async def verify_2fa(payload: TwoFactorVerify, request: Request):
    """Verify the TOTP code against the pending secret and enable 2FA."""
    user = await get_current_user(request)
    user_doc = await db.users.find_one({"_id": ObjectId(user["id"])})
    pending = user_doc.get("totp_pending_secret")
    if not pending:
        raise HTTPException(status_code=400, detail="Aucune configuration 2FA en cours")
    if not pyotp.TOTP(pending).verify(payload.code.strip(), valid_window=1):
        raise HTTPException(status_code=400, detail="Code invalide")
    await db.users.update_one(
        {"_id": ObjectId(user["id"])},
        {
            "$set": {"totp_secret": pending, "totp_enabled": True, "totp_enabled_at": datetime.now(timezone.utc)},
            "$unset": {"totp_pending_secret": "", "totp_pending_created_at": ""},
        },
    )
    if user.get("role") == "admin":
        await record_audit(user, "2fa_enabled")
    return {"enabled": True}


@router.post("/security/2fa/disable")
async def disable_2fa(payload: TwoFactorDisable, request: Request):
    user = await get_current_user(request)
    user_doc = await db.users.find_one({"_id": ObjectId(user["id"])})
    if not user_doc.get("totp_enabled"):
        raise HTTPException(status_code=400, detail="2FA non activé")
    if not verify_password(payload.password, user_doc["password_hash"]):
        raise HTTPException(status_code=403, detail="Mot de passe incorrect")
    if not pyotp.TOTP(user_doc["totp_secret"]).verify(payload.code.strip(), valid_window=1):
        raise HTTPException(status_code=400, detail="Code 2FA invalide")
    await db.users.update_one(
        {"_id": ObjectId(user["id"])},
        {"$set": {"totp_enabled": False}, "$unset": {"totp_secret": "", "totp_enabled_at": ""}},
    )
    if user.get("role") == "admin":
        await record_audit(user, "2fa_disabled")
    return {"enabled": False}


@router.get("/security/2fa/status")
async def status_2fa(request: Request):
    user = await get_current_user(request)
    user_doc = await db.users.find_one({"_id": ObjectId(user["id"])})
    return {
        "enabled": bool(user_doc.get("totp_enabled")),
        "pending_setup": bool(user_doc.get("totp_pending_secret")),
        "role": user_doc.get("role", "user"),
    }


# ============ Admin audit log viewer ============

@router.get("/security/audit")
async def list_audit_log(request: Request, limit: int = 100):
    user = await get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    limit = max(1, min(500, limit))
    cursor = db.admin_audit_logs.find().sort("created_at", -1).limit(limit)
    items = []
    async for d in cursor:
        items.append({
            "id": str(d["_id"]),
            "actor_id": d.get("actor_id"),
            "actor_email": d.get("actor_email"),
            "actor_role": d.get("actor_role"),
            "action": d.get("action"),
            "target": d.get("target"),
            "meta": d.get("meta", {}),
            "created_at": d["created_at"].isoformat() if isinstance(d.get("created_at"), datetime) else d.get("created_at"),
        })
    return {"items": items}
