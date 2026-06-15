import bcrypt
import jwt
import asyncio
import logging
import resend
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from fastapi import HTTPException, Request
from config import db, JWT_SECRET, JWT_ALGORITHM, RESEND_API_KEY, SENDER_EMAIL

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id, "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=60),
        "type": "access"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "refresh"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return {
            "id": str(user["_id"]),
            "email": user["email"],
            "name": user["name"],
            "role": user.get("role", "user"),
            "created_at": user["created_at"],
            "onboarding_completed": user.get("onboarding_completed", False)
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def send_payment_confirmation_email(email: str, name: str, plan_name: str, amount: float, currency: str):
    if not RESEND_API_KEY:
        return
    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background-color: #060A14; color: #F8FAFC; padding: 40px; border-radius: 12px;">
        <div style="text-align: center; margin-bottom: 30px;">
            <div style="display: inline-block; background: linear-gradient(135deg, #2563EB, #7C3AED); padding: 12px 20px; border-radius: 12px;">
                <span style="font-size: 24px; font-weight: bold; color: white;">Switia</span>
            </div>
        </div>
        <h1 style="color: #F8FAFC; text-align: center; font-size: 22px;">Paiement confirmé</h1>
        <p style="color: #94A3B8; text-align: center;">Merci pour votre confiance, {name} !</p>
        <div style="background-color: #0C1222; border: 1px solid #1E293B; border-radius: 8px; padding: 24px; margin: 20px 0;">
            <table style="width: 100%; border-collapse: collapse;">
                <tr><td style="color: #94A3B8; padding: 8px 0;">Forfait</td><td style="color: #F8FAFC; text-align: right; font-weight: bold;">{plan_name}</td></tr>
                <tr><td style="color: #94A3B8; padding: 8px 0;">Montant</td><td style="color: #10B981; text-align: right; font-weight: bold;">{amount}€</td></tr>
                <tr><td style="color: #94A3B8; padding: 8px 0;">Date</td><td style="color: #F8FAFC; text-align: right;">{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}</td></tr>
            </table>
        </div>
        <p style="color: #64748B; text-align: center; font-size: 12px;">Switia - Plateforme IA pour entreprises</p>
    </div>
    """
    try:
        params = {"from": SENDER_EMAIL, "to": [email], "subject": f"Switia - Confirmation de paiement : Forfait {plan_name}", "html": html_content}
        await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"Confirmation email sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send confirmation email: {e}")


async def send_escalation_email(admin_email: str, admin_name: str, user_message: str, agent_response: str, session_id: str):
    """Send email notification when a conversation is escalated"""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not configured - skipping escalation email")
        return
    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background-color: #060A14; color: #F8FAFC; padding: 40px; border-radius: 12px;">
        <div style="text-align: center; margin-bottom: 30px;">
            <div style="display: inline-block; background: linear-gradient(135deg, #2563EB, #7C3AED); padding: 12px 20px; border-radius: 12px;">
                <span style="font-size: 24px; font-weight: bold; color: white;">Switia</span>
            </div>
        </div>
        <h1 style="color: #F59E0B; text-align: center; font-size: 20px;">Escalation détectée</h1>
        <p style="color: #94A3B8; text-align: center;">Une conversation nécessite une intervention humaine.</p>
        <div style="background-color: #0C1222; border: 1px solid #1E293B; border-radius: 8px; padding: 20px; margin: 20px 0;">
            <p style="color: #94A3B8; font-size: 12px; margin-bottom: 8px;">Message du client :</p>
            <p style="color: #F8FAFC; font-size: 14px; margin-bottom: 16px; padding: 10px; background: #121A2F; border-radius: 6px;">{user_message[:500]}</p>
            <p style="color: #94A3B8; font-size: 12px; margin-bottom: 8px;">Réponse de l'agent :</p>
            <p style="color: #F8FAFC; font-size: 14px; padding: 10px; background: #121A2F; border-radius: 6px;">{agent_response[:500]}</p>
        </div>
        <p style="color: #64748B; text-align: center; font-size: 12px;">Session: {session_id}</p>
    </div>
    """
    try:
        params = {"from": SENDER_EMAIL, "to": [admin_email], "subject": "Switia - Escalation détectée", "html": html_content}
        await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"Escalation email sent to {admin_email}")
    except Exception as e:
        logger.error(f"Failed to send escalation email: {e}")
