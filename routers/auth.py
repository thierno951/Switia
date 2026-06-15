from fastapi import APIRouter, HTTPException, Request, Response
import secrets
import pyotp
from datetime import datetime, timezone
from bson import ObjectId
from slowapi import Limiter
from slowapi.util import get_remote_address
from config import db, COOKIE_SECURE, COOKIE_SAMESITE
from models import UserCreate, UserLogin, UserResponse, UserProfileUpdate, TeamMemberInvite
from utils import hash_password, verify_password, create_access_token, create_refresh_token, get_current_user

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.post("/auth/register", response_model=UserResponse)
@limiter.limit("10/minute")
async def register(user_data: UserCreate, request: Request, response: Response):
    email = user_data.email.lower()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user_doc = {
        "email": email, "password_hash": hash_password(user_data.password),
        "name": user_data.name, "role": "user", "created_at": datetime.now(timezone.utc)
    }
    result = await db.users.insert_one(user_doc)
    user_id = str(result.inserted_id)
    access_token = create_access_token(user_id, email)
    refresh_token = create_refresh_token(user_id)
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE, max_age=3600, path="/")
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True, secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE, max_age=604800, path="/")
    return UserResponse(id=user_id, email=email, name=user_data.name, role="user", created_at=user_doc["created_at"])

@router.post("/auth/login", response_model=UserResponse)
@limiter.limit("10/minute")
async def login(user_data: UserLogin, request: Request, response: Response):
    email = user_data.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(user_data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    # 2FA step: if enabled on this account, a totp_code must be provided
    if user.get("totp_enabled"):
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        totp_code = (body.get("totp_code") or "").strip()
        if not totp_code:
            raise HTTPException(status_code=401, detail="2FA_REQUIRED")
        if not pyotp.TOTP(user["totp_secret"]).verify(totp_code, valid_window=1):
            raise HTTPException(status_code=401, detail="Code 2FA invalide")
    user_id = str(user["_id"])
    access_token = create_access_token(user_id, email)
    refresh_token = create_refresh_token(user_id)
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE, max_age=3600, path="/")
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True, secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE, max_age=604800, path="/")
    return UserResponse(id=user_id, email=user["email"], name=user["name"], role=user.get("role", "user"), created_at=user["created_at"], onboarding_completed=user.get("onboarding_completed", False))

@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/", secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE)
    response.delete_cookie("refresh_token", path="/", secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE)
    return {"message": "Logged out successfully"}


@router.post("/auth/refresh")
async def refresh(request: Request, response: Response):
    """Exchange a valid refresh_token cookie for a fresh access_token.
    Used by the frontend axios interceptor when a 401 is received."""
    import jwt as _jwt
    from config import JWT_SECRET, JWT_ALGORITHM
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = _jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user_id = str(user["_id"])
        new_access = create_access_token(user_id, user["email"])
        response.set_cookie(
            key="access_token", value=new_access, httponly=True,
            secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE, max_age=3600, path="/",
        )
        return {"ok": True}
    except _jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except _jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

@router.get("/auth/me", response_model=UserResponse)
async def get_me(request: Request):
    user = await get_current_user(request)
    return UserResponse(**user)

@router.put("/profile")
async def update_profile(profile_data: UserProfileUpdate, request: Request):
    user = await get_current_user(request)
    updates = {}
    if profile_data.name and profile_data.name.strip():
        updates["name"] = profile_data.name.strip()
    if profile_data.email:
        new_email = profile_data.email.lower()
        if new_email != user["email"]:
            existing = await db.users.find_one({"email": new_email})
            if existing:
                raise HTTPException(status_code=400, detail="Cet email est déjà utilisé")
            updates["email"] = new_email
    if profile_data.new_password:
        if not profile_data.current_password:
            raise HTTPException(status_code=400, detail="Mot de passe actuel requis")
        user_doc = await db.users.find_one({"_id": ObjectId(user["id"])})
        if not verify_password(profile_data.current_password, user_doc["password_hash"]):
            raise HTTPException(status_code=400, detail="Mot de passe actuel incorrect")
        updates["password_hash"] = hash_password(profile_data.new_password)
    if not updates:
        raise HTTPException(status_code=400, detail="Aucune modification")
    updates["updated_at"] = datetime.now(timezone.utc)
    await db.users.update_one({"_id": ObjectId(user["id"])}, {"$set": updates})
    updated = await db.users.find_one({"_id": ObjectId(user["id"])})
    return {"id": str(updated["_id"]), "email": updated["email"], "name": updated["name"], "role": updated.get("role", "user"), "message": "Profil mis à jour"}

@router.post("/onboarding/complete")
async def complete_onboarding(request: Request):
    user = await get_current_user(request)
    await db.users.update_one({"_id": ObjectId(user["id"])}, {"$set": {"onboarding_completed": True}})
    return {"message": "Onboarding completed"}

@router.post("/onboarding/reset")
async def reset_onboarding(request: Request):
    user = await get_current_user(request)
    await db.users.update_one({"_id": ObjectId(user["id"])}, {"$set": {"onboarding_completed": False}})
    return {"message": "Onboarding reset"}

@router.post("/team/invite")
async def invite_team_member(invite: TeamMemberInvite, request: Request):
    user = await get_current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Seuls les administrateurs peuvent inviter des membres")
    email = invite.email.lower()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Cet email est déjà enregistré")
    if invite.role not in ["admin", "member"]:
        raise HTTPException(status_code=400, detail="Rôle invalide")
    temp_password = secrets.token_urlsafe(10)
    member_doc = {"email": email, "password_hash": hash_password(temp_password), "name": invite.name.strip(), "role": invite.role, "invited_by": user["id"], "team_owner": user["id"], "created_at": datetime.now(timezone.utc)}
    result = await db.users.insert_one(member_doc)
    return {"id": str(result.inserted_id), "email": email, "name": invite.name.strip(), "role": invite.role, "temp_password": temp_password, "message": f"Membre invité avec le mot de passe temporaire: {temp_password}"}

@router.get("/team/members")
async def list_team_members(request: Request):
    user = await get_current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    members = await db.users.find({"$or": [{"team_owner": user["id"]}, {"_id": ObjectId(user["id"])}]}, {"_id": 1, "email": 1, "name": 1, "role": 1, "created_at": 1}).sort("created_at", 1).to_list(50)
    return {"members": [{"id": str(m["_id"]), "email": m["email"], "name": m["name"], "role": m.get("role", "member"), "created_at": m["created_at"].isoformat() if isinstance(m.get("created_at"), datetime) else str(m.get("created_at", ""))} for m in members]}

@router.delete("/team/members/{member_id}")
async def remove_team_member(member_id: str, request: Request):
    user = await get_current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    if member_id == user["id"]:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas vous retirer")
    result = await db.users.delete_one({"_id": ObjectId(member_id), "team_owner": user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Membre non trouvé")
    return {"message": "Membre supprimé"}

# White-label settings
@router.get("/settings/whitelabel")
async def get_whitelabel(request: Request):
    user = await get_current_user(request)
    settings = await db.whitelabel.find_one({"user_id": user["id"]}, {"_id": 0})
    return settings or {"brand_name": "Switia", "primary_color": "#2563EB", "logo_url": ""}

@router.put("/settings/whitelabel")
async def update_whitelabel(request: Request):
    user = await get_current_user(request)
    body = await request.json()
    updates = {}
    if "brand_name" in body and body["brand_name"]:
        updates["brand_name"] = body["brand_name"][:50]
    if "primary_color" in body and body["primary_color"]:
        updates["primary_color"] = body["primary_color"][:7]
    if "logo_url" in body:
        updates["logo_url"] = body["logo_url"][:500]
    if not updates:
        raise HTTPException(status_code=400, detail="Aucune modification")
    updates["user_id"] = user["id"]
    updates["updated_at"] = datetime.now(timezone.utc)
    await db.whitelabel.update_one({"user_id": user["id"]}, {"$set": updates}, upsert=True)
    return {"message": "Paramètres mis à jour", **updates}
