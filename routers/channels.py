"""
Channels router — users configure their own email/WhatsApp/phone channels.
Email: real IMAP + SMTP implementation (works with Gmail/Outlook via app passwords).
WhatsApp + Phone: number stored, actual send integration MOCKED until Twilio/Meta keys provided.
"""
import os
import imaplib
import smtplib
import email
import base64
from email.mime.text import MIMEText
from email.header import decode_header
from datetime import datetime, timezone
from bson import ObjectId
from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException, Request
from config import db
from models import ChannelsPayload, EmailChannelConfig, WhatsAppChannelConfig, PhoneChannelConfig
from utils import get_current_user
from emergentintegrations.llm.chat import LlmChat, UserMessage
import logging
import uuid

logger = logging.getLogger(__name__)
router = APIRouter()

# ---- Encryption for sensitive credentials (app passwords) ----
# Uses a derived key from MONGO_URL so it's stable across restarts.
def _get_fernet() -> Fernet:
    import base64
    import hashlib
    seed = (os.environ.get("CHANNELS_ENCRYPT_KEY") or os.environ.get("MONGO_URL") or "switia-fallback").encode()
    digest = hashlib.sha256(seed).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(value: str) -> str:
    if not value:
        return ""
    return _get_fernet().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except Exception:
        return ""


def _mask_password(value: str) -> str:
    if not value:
        return ""
    return "•" * 12


EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")


# ---- Storage helpers ----
async def _get_channels_doc(user_id: str) -> dict:
    doc = await db.channel_configs.find_one({"user_id": user_id})
    return doc or {"user_id": user_id, "email": {}, "whatsapp": {}, "phone": {}}


def _public_email(cfg: dict) -> dict:
    """Return email config for UI without exposing the password."""
    oauth = cfg.get("oauth_provider")
    return {
        "enabled": cfg.get("enabled", False),
        "imap_host": cfg.get("imap_host"),
        "imap_port": cfg.get("imap_port", 993),
        "smtp_host": cfg.get("smtp_host"),
        "smtp_port": cfg.get("smtp_port", 587),
        "email_address": cfg.get("email_address"),
        "app_password": _mask_password(cfg.get("app_password")),
        "auto_reply": cfg.get("auto_reply", False),
        "signature": cfg.get("signature"),
        "preferred_agent": cfg.get("preferred_agent", "support"),
        "oauth_provider": oauth,
        "connected": bool(oauth) or (bool(cfg.get("app_password")) and cfg.get("enabled", False)),
    }


@router.get("/channels")
async def get_channels(request: Request):
    user = await get_current_user(request)
    doc = await _get_channels_doc(user["id"])
    return {
        "email": _public_email(doc.get("email", {})),
        "whatsapp": doc.get("whatsapp", {}),
        "phone": doc.get("phone", {}),
    }


@router.put("/channels")
async def update_channels(payload: ChannelsPayload, request: Request):
    user = await get_current_user(request)
    doc = await _get_channels_doc(user["id"])
    update = {}

    if payload.email is not None:
        ec = payload.email.model_dump(exclude_unset=True)
        # Only overwrite password if a new one (not masked placeholder) was provided
        new_pwd = ec.get("app_password")
        if new_pwd and not new_pwd.startswith("•"):
            ec["app_password"] = _encrypt(new_pwd)
        else:
            ec.pop("app_password", None)
        merged = {**(doc.get("email") or {}), **ec}
        update["email"] = merged

    if payload.whatsapp is not None:
        wa = payload.whatsapp.model_dump(exclude_unset=True)
        merged = {**(doc.get("whatsapp") or {}), **wa}
        update["whatsapp"] = merged

    if payload.phone is not None:
        ph = payload.phone.model_dump(exclude_unset=True)
        merged = {**(doc.get("phone") or {}), **ph}
        update["phone"] = merged

    update["updated_at"] = datetime.now(timezone.utc)
    await db.channel_configs.update_one(
        {"user_id": user["id"]}, {"$set": update, "$setOnInsert": {"user_id": user["id"]}}, upsert=True
    )
    saved = await _get_channels_doc(user["id"])
    return {
        "email": _public_email(saved.get("email", {})),
        "whatsapp": saved.get("whatsapp", {}),
        "phone": saved.get("phone", {}),
    }


# ---- Email IMAP/SMTP ----

@router.post("/channels/email/test")
async def test_email_connection(request: Request):
    """Quickly test IMAP login with stored credentials."""
    user = await get_current_user(request)
    doc = await _get_channels_doc(user["id"])
    ec = doc.get("email") or {}
    host, port = ec.get("imap_host"), int(ec.get("imap_port") or 993)
    addr = ec.get("email_address")
    pwd = _decrypt(ec.get("app_password") or "")
    if not (host and addr and pwd):
        raise HTTPException(status_code=400, detail="Configuration email incomplète")
    try:
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(addr, pwd)
        conn.select("INBOX")
        conn.logout()
        return {"ok": True, "message": f"Connexion IMAP réussie vers {host}"}
    except Exception as e:
        err_str = str(e)
        logger.error(f"IMAP test error: {err_str}")
        # Microsoft Outlook/Office365 blocks basic auth since Oct 2022 → clear guidance
        if "BasicAuthBlocked" in err_str or "LogonDenied" in err_str:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Microsoft Outlook / Office 365 bloque la connexion par mot de passe depuis 2022. "
                    "Pour ce compte : 1) Gmail fonctionne avec un mot de passe d'application ; "
                    "2) Outlook nécessite OAuth2 (bientôt disponible). "
                    "En attendant, utilisez un alias Gmail qui récupère vos emails Outlook."
                ),
            )
        if "AUTHENTICATIONFAILED" in err_str.upper() or "Invalid credentials" in err_str:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Identifiants refusés. Pour Gmail : générez un mot de passe d'application "
                    "(Google Compte → Sécurité → Mots de passe des applications). Le mot de passe "
                    "principal est refusé par Google."
                ),
            )
        raise HTTPException(status_code=400, detail=f"Échec IMAP : {err_str[:160]}")


def _decode_subject(raw: bytes | str | None) -> str:
    if not raw:
        return "(sans objet)"
    try:
        parts = decode_header(raw if isinstance(raw, str) else raw.decode(errors="ignore"))
        out = ""
        for txt, enc in parts:
            if isinstance(txt, bytes):
                out += txt.decode(enc or "utf-8", errors="ignore")
            else:
                out += txt
        return out or "(sans objet)"
    except Exception:
        return "(sans objet)"


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                try:
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
                except Exception:
                    continue
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")


async def _load_agent_config(user_id: str, agent_type: str) -> dict:
    doc = await db.agent_configs.find_one({"user_id": user_id, "agent_type": agent_type})
    return doc or {}


async def _generate_ai_reply(user_id: str, agent_type: str, subject: str, body: str, sender: str) -> str:
    cfg = await _load_agent_config(user_id, agent_type)
    base = cfg.get("system_prompt") or (
        "Tu es un agent Support professionnel pour le compte de notre client. "
        "Réponds à l'email du client de façon concise (max 10 lignes), polie, et directe. "
        "Si la demande nécessite des informations internes (remboursement, changement de compte, litige grave), "
        "termine ta réponse par une indication claire que tu transmets à l'équipe humaine. "
        "Utilise le vouvoiement, tutoie uniquement si le client tutoie."
    )
    tone = cfg.get("tone", "friendly")
    signature = cfg.get("welcome_message") or ""
    prompt = f"{base}\n\nTon à adopter : {tone}.\nLangue de la réponse : même langue que le client."
    content = f"De : {sender}\nObjet : {subject}\n\nMessage client :\n{body[:3500]}"
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"email_reply_{user_id}_{uuid.uuid4().hex[:8]}",
            system_message=prompt,
        ).with_model("openai", "gpt-5.2")
        reply = await chat.send_message(UserMessage(text=content))
        return (reply or "").strip() + (f"\n\n{signature}" if signature else "")
    except Exception as e:
        logger.error(f"AI reply error: {e}")
        return "Bonjour,\n\nMerci pour votre message. Notre équipe l'a bien reçu et reviendra vers vous sous peu.\n\nCordialement."


def _send_smtp(ec: dict, to_addr: str, subject: str, body: str):
    host, port = ec.get("smtp_host"), int(ec.get("smtp_port") or 587)
    addr = ec.get("email_address")
    pwd = _decrypt(ec.get("app_password") or "")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
    msg["From"] = addr
    msg["To"] = to_addr
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(addr, pwd)
        s.sendmail(addr, [to_addr], msg.as_string())


@router.post("/channels/email/check")
async def check_inbox_and_draft(request: Request):
    """Fetch latest unread emails, generate AI drafts, and (if auto_reply) send them."""
    user = await get_current_user(request)
    doc = await _get_channels_doc(user["id"])
    ec = doc.get("email") or {}
    if not ec.get("enabled"):
        raise HTTPException(status_code=400, detail="Canal email désactivé")

    agent_type = ec.get("preferred_agent") or "support"
    auto_send = bool(ec.get("auto_reply"))

    if ec.get("oauth_provider") == "gmail":
        results = await _process_gmail_inbox(user["id"], agent_type, auto_send)
    else:
        results = await _process_imap_inbox(user["id"], ec, agent_type, auto_send)
    return {"fetched": len(results), "replies": results}


def _build_reply_record(user_id: str, agent_type: str, provider: str, message_id: str, sender: str, subject: str, body: str, draft: str) -> dict:
    """Build the common email_replies record dict shared by Gmail + IMAP paths."""
    return {
        "user_id": user_id,
        "message_id": message_id,
        "sender": sender,
        "subject": subject,
        "body": body[:2000],
        "draft": draft,
        "sent": False,
        "agent_type": agent_type,
        "provider": provider,
        "received_at": datetime.now(timezone.utc),
    }


async def _persist_and_serialize(record: dict) -> dict:
    """Insert record to DB and convert datetime fields to ISO strings for JSON response."""
    inserted = await db.email_replies.insert_one(record)
    record["_id"] = str(inserted.inserted_id)
    if isinstance(record.get("received_at"), datetime):
        record["received_at"] = record["received_at"].isoformat()
    if isinstance(record.get("sent_at"), datetime):
        record["sent_at"] = record["sent_at"].isoformat()
    return record


def _extract_recipient_address(sender_header: str) -> str:
    """Parse 'Name <email@x.com>' or raw email into a valid recipient address."""
    import re as _re
    m = _re.search(r"<([^>]+)>", sender_header) or _re.search(r"[\w.-]+@[\w.-]+", sender_header)
    if m and m.groups():
        return m.group(1)
    if m:
        return m.group(0)
    return sender_header


async def _process_gmail_inbox(user_id: str, agent_type: str, auto_send: bool) -> list:
    """Fetch + draft (+optionally send) unread Gmail messages using OAuth API."""
    from routers.oauth_gmail import get_gmail_credentials
    from googleapiclient.discovery import build

    creds = await get_gmail_credentials(user_id)
    if not creds:
        raise HTTPException(status_code=400, detail="Gmail OAuth non connecté ou expiré")

    results = []
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        res = service.users().messages().list(userId="me", q="is:unread -from:me", maxResults=10).execute()
        for item in res.get("messages", []):
            mid = item["id"]
            full = service.users().messages().get(userId="me", id=mid, format="full").execute()
            headers = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
            subject = headers.get("subject", "(sans objet)")
            sender = headers.get("from", "")
            body = _extract_body_gmail(full.get("payload", {}))[:6000]
            draft = await _generate_ai_reply(user_id, agent_type, subject, body, sender)
            record = _build_reply_record(user_id, agent_type, "gmail_oauth", mid, sender, subject, body, draft)
            if auto_send and sender:
                _try_send_gmail(service, mid, sender, subject, draft, record)
            results.append(await _persist_and_serialize(record))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Gmail check error: {e}")
        raise HTTPException(status_code=400, detail=f"Erreur Gmail : {str(e)[:160]}")
    return results


def _try_send_gmail(service, mid: str, sender: str, subject: str, draft: str, record: dict) -> None:
    """Attempt Gmail send + mark original as read; mutates record with sent/error state."""
    try:
        _send_gmail_api(service, sender, subject, draft)
        service.users().messages().modify(userId="me", id=mid, body={"removeLabelIds": ["UNREAD"]}).execute()
        record["sent"] = True
        record["sent_at"] = datetime.now(timezone.utc)
    except Exception as se:
        logger.error(f"Gmail send error: {se}")
        record["send_error"] = str(se)[:200]


async def _process_imap_inbox(user_id: str, ec: dict, agent_type: str, auto_send: bool) -> list:
    """Fetch + draft (+optionally send) unread emails via classical IMAP/SMTP."""
    host, port = ec.get("imap_host"), int(ec.get("imap_port") or 993)
    addr = ec.get("email_address")
    pwd = _decrypt(ec.get("app_password") or "")
    if not (host and addr and pwd):
        raise HTTPException(status_code=400, detail="Configuration email incomplète")

    results = []
    try:
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(addr, pwd)
        conn.select("INBOX")
        _, data = conn.search(None, "UNSEEN")
        ids = (data[0] or b"").split()[-10:]
        for num in ids:
            record = await _process_one_imap_message(conn, num, user_id, agent_type, ec, auto_send)
            if record is not None:
                results.append(record)
        conn.logout()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"IMAP check error: {e}")
        raise HTTPException(status_code=400, detail=f"Erreur IMAP : {str(e)[:140]}")
    return results


async def _process_one_imap_message(conn, num, user_id: str, agent_type: str, ec: dict, auto_send: bool):
    """Process a single IMAP message id — returns the serialized record or None on fetch failure."""
    typ, mdata = conn.fetch(num, "(RFC822)")
    if typ != "OK" or not mdata or not mdata[0]:
        return None
    msg = email.message_from_bytes(mdata[0][1])
    subject = _decode_subject(msg.get("Subject"))
    sender = msg.get("From", "")
    body = _extract_body(msg)[:6000]
    draft = await _generate_ai_reply(user_id, agent_type, subject, body, sender)
    record = _build_reply_record(user_id, agent_type, "imap", msg.get("Message-ID", str(num)), sender, subject, body, draft)
    if auto_send and sender:
        _try_send_smtp(ec, sender, subject, draft, record)
    return await _persist_and_serialize(record)


def _try_send_smtp(ec: dict, sender: str, subject: str, draft: str, record: dict) -> None:
    """Attempt SMTP send; mutates record with sent/error state."""
    try:
        to_addr = _extract_recipient_address(sender)
        _send_smtp(ec, to_addr, subject, draft)
        record["sent"] = True
        record["sent_at"] = datetime.now(timezone.utc)
    except Exception as se:
        logger.error(f"SMTP send error: {se}")
        record["send_error"] = str(se)[:200]


def _extract_body_gmail(payload: dict) -> str:
    """Walk Gmail API payload parts to find text/plain content."""
    if not payload:
        return ""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            except Exception:
                return ""
    for part in payload.get("parts", []) or []:
        txt = _extract_body_gmail(part)
        if txt:
            return txt
    return ""


def _send_gmail_api(service, to_addr: str, subject: str, body: str):
    import re as _re
    m = _re.search(r"<([^>]+)>", to_addr) or _re.search(r"[\w\.-]+@[\w\.-]+", to_addr)
    clean_to = m.group(1) if m and m.groups() else (m.group(0) if m else to_addr)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
    msg["To"] = clean_to
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


@router.get("/channels/email/replies")
async def list_email_replies(request: Request):
    user = await get_current_user(request)
    items = await db.email_replies.find({"user_id": user["id"]}).sort("received_at", -1).to_list(50)
    out = []
    for d in items:
        d["_id"] = str(d["_id"])
        for k, v in list(d.items()):
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        out.append(d)
    return {"replies": out}


@router.post("/channels/email/replies/{reply_id}/send")
async def send_email_reply(reply_id: str, request: Request):
    """Manually send a previously-drafted reply (used when auto_reply is OFF)."""
    user = await get_current_user(request)
    try:
        oid = ObjectId(reply_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    rec = await db.email_replies.find_one({"_id": oid, "user_id": user["id"]})
    if not rec:
        raise HTTPException(status_code=404, detail="Brouillon introuvable")
    if rec.get("sent"):
        return {"ok": True, "already_sent": True}
    doc = await _get_channels_doc(user["id"])
    ec = doc.get("email") or {}
    import re as _re
    sender = rec.get("sender", "")
    m = _re.search(r"<([^>]+)>", sender) or _re.search(r"[\w\.-]+@[\w\.-]+", sender)
    to_addr = m.group(1) if m and m.groups() else (m.group(0) if m else sender)
    try:
        _send_smtp(ec, to_addr, rec.get("subject", ""), rec.get("draft", ""))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erreur SMTP : {str(e)[:140]}")
    await db.email_replies.update_one(
        {"_id": oid}, {"$set": {"sent": True, "sent_at": datetime.now(timezone.utc)}}
    )
    return {"ok": True}


# ---- WhatsApp / Phone test endpoints moved to routers/twilio_channels.py (real Twilio integration) ----
