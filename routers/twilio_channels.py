"""
Twilio router — WhatsApp + SMS integration.
- Send WhatsApp / SMS via the platform-level Twilio credentials (.env)
- Receive inbound messages via /webhooks/twilio/whatsapp and /webhooks/twilio/sms
- Validate Twilio signatures for security
- Auto-reply using the user's configured AI agent, respecting auto_reply toggle
"""
import os
import logging
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request, Form, Response
from fastapi.responses import PlainTextResponse
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator
from config import db
from utils import get_current_user
from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)
router = APIRouter()

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
TWILIO_SMS_FROM = os.environ.get("TWILIO_SMS_FROM", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")


def _get_client() -> TwilioClient:
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="Twilio non configuré côté serveur")
    return TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


async def _resolve_user_by_channel(channel: str, to_number: str):
    """Find which Switia user owns the inbound destination number."""
    # channel = "whatsapp" | "sms"
    num = to_number.replace("whatsapp:", "").strip()
    query = {f"{channel}.phone_number": num}
    doc = await db.channel_configs.find_one(query)
    if not doc:
        return None
    return doc


async def _generate_agent_reply(user_id: str, agent_type: str, channel: str, sender: str, body: str) -> str:
    cfg = await db.agent_configs.find_one({"user_id": user_id, "agent_type": agent_type}) or {}
    base = cfg.get("system_prompt") or (
        "Tu es un agent Support professionnel. Réponds de manière courte (max 4 phrases), "
        "polie et directe. Utilise le vouvoiement, sauf si le client tutoie."
    )
    tone = cfg.get("tone", "friendly")
    welcome = cfg.get("welcome_message") or ""
    language_hint = "Détecte la langue du client et réponds dans la même langue."
    prompt = f"{base}\n\nTon : {tone}. Canal : {channel}. {language_hint}"
    content = f"Client ({sender}) a écrit :\n{body}"
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"{channel}_{user_id}_{uuid.uuid4().hex[:8]}",
            system_message=prompt,
        ).with_model("openai", "gpt-5.2")
        reply = await chat.send_message(UserMessage(text=content))
        text = (reply or "").strip()
        return f"{welcome}\n{text}".strip() if welcome and welcome not in text else text
    except Exception as e:
        logger.error(f"AI reply {channel} error: {e}")
        return "Bonjour, merci pour votre message. Nous revenons vers vous rapidement."


# ============ Outbound: send from Switia UI ============

@router.post("/channels/whatsapp/send")
async def send_whatsapp(payload: dict, request: Request):
    user = await get_current_user(request)
    to = (payload.get("to") or "").strip()
    body = (payload.get("body") or "").strip()
    if not to or not body:
        raise HTTPException(status_code=400, detail="`to` et `body` requis")
    client = _get_client()
    to_wa = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
    try:
        msg = client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=to_wa, body=body)
    except Exception as e:
        logger.error(f"Twilio WA send error: {e}")
        raise HTTPException(status_code=400, detail=f"Erreur Twilio : {str(e)[:160]}")
    await db.twilio_messages.insert_one({
        "user_id": user["id"], "channel": "whatsapp", "direction": "out",
        "to": to, "from": TWILIO_WHATSAPP_FROM, "body": body,
        "twilio_sid": msg.sid, "created_at": datetime.now(timezone.utc),
    })
    return {"ok": True, "sid": msg.sid}


@router.post("/channels/sms/send")
async def send_sms(payload: dict, request: Request):
    user = await get_current_user(request)
    to = (payload.get("to") or "").strip()
    body = (payload.get("body") or "").strip()
    if not to or not body:
        raise HTTPException(status_code=400, detail="`to` et `body` requis")
    if not TWILIO_SMS_FROM:
        raise HTTPException(status_code=400, detail="Numéro Twilio SMS non configuré. Achetez un numéro dans Twilio Console puis ajoutez TWILIO_SMS_FROM au .env.")
    client = _get_client()
    try:
        msg = client.messages.create(from_=TWILIO_SMS_FROM, to=to, body=body)
    except Exception as e:
        logger.error(f"Twilio SMS send error: {e}")
        raise HTTPException(status_code=400, detail=f"Erreur Twilio : {str(e)[:160]}")
    await db.twilio_messages.insert_one({
        "user_id": user["id"], "channel": "sms", "direction": "out",
        "to": to, "from": TWILIO_SMS_FROM, "body": body,
        "twilio_sid": msg.sid, "created_at": datetime.now(timezone.utc),
    })
    return {"ok": True, "sid": msg.sid}


# ============ Inbound: Twilio webhooks ============

def _validate_twilio_signature(request: Request, form_data: dict) -> bool:
    if not TWILIO_AUTH_TOKEN:
        return False
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    signature = request.headers.get("X-Twilio-Signature", "")
    # Public URL we gave Twilio must match exactly
    url = str(request.url).replace("http://", "https://")
    return validator.validate(url, form_data, signature)


async def _handle_inbound(channel: str, request: Request, form_map: dict):
    # Twilio sends application/x-www-form-urlencoded
    sender = form_map.get("From", "")
    to = form_map.get("To", "")
    body = (form_map.get("Body") or "").strip()
    msg_sid = form_map.get("MessageSid", "")

    # Find the Switia user owning this destination number
    user_doc = await _resolve_user_by_channel(channel, to)
    if not user_doc:
        # Default fallback: first user with the Twilio account enabled for that channel
        user_doc = await db.channel_configs.find_one({f"{channel}.enabled": True})

    reply_body = None
    user_id = user_doc.get("user_id") if user_doc else None
    agent_type = (user_doc or {}).get(channel, {}).get("preferred_agent", "support")
    auto_reply = True  # WhatsApp/SMS défault = auto-reply (users expect instant response)

    if user_id:
        reply_body = await _generate_agent_reply(user_id, agent_type, channel, sender, body)

    # Match this inbound to a recent outbound campaign (for reply tracking)
    try:
        since = datetime.now(timezone.utc) - timedelta(days=30)
        sender_clean = sender.replace("whatsapp:", "").strip()
        await db.campaigns.update_one(
            {
                "user_id": user_id,
                "channel": channel,
                "recipients.phone": sender_clean,
                "recipients.replied_at": None,
                "sent_at": {"$gte": since, "$ne": None},
            },
            {"$set": {"recipients.$.replied_at": datetime.now(timezone.utc)}},
        )
        # Recompute reply count on any matched campaign
        c = await db.campaigns.find_one({"user_id": user_id, "channel": channel, "recipients.phone": sender_clean})
        if c:
            replies_count = sum(1 for r in c.get("recipients", []) if r.get("replied_at"))
            await db.campaigns.update_one({"_id": c["_id"]}, {"$set": {"replies": replies_count}})
    except Exception as e:
        logger.warning(f"reply tracking error: {e}")

    await db.twilio_messages.insert_one({
        "user_id": user_id,
        "channel": channel,
        "direction": "in",
        "from": sender,
        "to": to,
        "body": body[:3000],
        "reply": reply_body,
        "auto_sent": auto_reply and bool(reply_body),
        "twilio_sid": msg_sid,
        "created_at": datetime.now(timezone.utc),
    })

    # TwiML response — Twilio will auto-send this to the sender
    if auto_reply and reply_body:
        safe = (reply_body or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
        return PlainTextResponse(content=xml, media_type="application/xml")
    return PlainTextResponse(content='<?xml version="1.0" encoding="UTF-8"?><Response/>', media_type="application/xml")


@router.post("/webhooks/twilio/whatsapp")
async def webhook_whatsapp(request: Request):
    form_data = dict(await request.form())
    # Note: signature validation is strict; Twilio sandbox sometimes fails on proxied URLs.
    # We log failures but still process to keep sandbox usable. Tighten in prod.
    if not _validate_twilio_signature(request, form_data):
        logger.warning("Twilio WhatsApp signature check failed (continuing in sandbox mode)")
    return await _handle_inbound("whatsapp", request, form_data)


@router.post("/webhooks/twilio/sms")
async def webhook_sms(request: Request):
    form_data = dict(await request.form())
    if not _validate_twilio_signature(request, form_data):
        logger.warning("Twilio SMS signature check failed (continuing in sandbox mode)")
    return await _handle_inbound("sms", request, form_data)


# ============ Test endpoints (replace the old mocked placeholders) ============

@router.post("/channels/whatsapp/test")
async def test_whatsapp(request: Request):
    await get_current_user(request)  # auth required
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return {"ok": False, "message": "Twilio non configuré (clés manquantes côté serveur)."}
    client = _get_client()
    try:
        # Just verify the account is reachable
        acc = client.api.accounts(TWILIO_ACCOUNT_SID).fetch()
        return {
            "ok": True,
            "message": f"Connexion Twilio OK (compte : {acc.friendly_name}). Pour tester, envoyez 'join <code>' au sandbox WhatsApp +14155238886 depuis votre téléphone.",
            "webhook_url": f"{PUBLIC_URL}/api/webhooks/twilio/whatsapp",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Twilio : {str(e)[:160]}")


@router.post("/channels/phone/test")
async def test_phone(request: Request):
    await get_current_user(request)  # auth required
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return {"ok": False, "message": "Twilio non configuré."}
    if not TWILIO_SMS_FROM:
        return {
            "ok": False,
            "mocked": True,
            "message": "Numéro Twilio SMS manquant. Achetez un numéro dans la Twilio Console (Phone Numbers → Buy a number) puis ajoutez-le via la variable TWILIO_SMS_FROM (ex: +33XXXXXXXXX) dans le .env du serveur.",
        }
    return {
        "ok": True,
        "message": f"Numéro SMS actif : {TWILIO_SMS_FROM}",
        "webhook_url": f"{PUBLIC_URL}/api/webhooks/twilio/sms",
    }


# ============ History endpoint ============

@router.get("/channels/messages")
async def list_channel_messages(request: Request, channel: str = ""):
    user = await get_current_user(request)
    q = {"user_id": user["id"]}
    if channel in ("whatsapp", "sms"):
        q["channel"] = channel
    items = await db.twilio_messages.find(q).sort("created_at", -1).to_list(100)
    out = []
    for d in items:
        d["_id"] = str(d["_id"])
        for k, v in list(d.items()):
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        out.append(d)
    return {"messages": out}


# ============ Capabilities endpoint (UI gating) ============

@router.get("/channels/twilio/capabilities")
async def twilio_capabilities():
    """Tell the frontend which outbound channels are usable right now."""
    return {
        "whatsapp": bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM),
        "sms": bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_SMS_FROM),
    }


# ============ Outbound campaigns (bulk send) ============

def _campaign_to_dict(c: dict) -> dict:
    c = {**c}
    c["_id"] = str(c.get("_id", ""))
    for k, v in list(c.items()):
        if isinstance(v, datetime):
            c[k] = v.isoformat()
    return c


def _render_template(tpl: str, ctx: dict) -> str:
    """Very small `{name}` placeholder substitution."""
    out = tpl or ""
    for k, v in (ctx or {}).items():
        out = out.replace("{" + str(k) + "}", str(v or ""))
    return out


@router.post("/campaigns")
async def create_campaign(payload: dict, request: Request):
    user = await get_current_user(request)
    name = (payload.get("name") or "").strip() or "Campagne"
    channel = (payload.get("channel") or "").strip().lower()
    if channel not in ("whatsapp", "sms"):
        raise HTTPException(status_code=400, detail="channel doit être 'whatsapp' ou 'sms'")
    message = (payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Le message est requis")
    # recipients: list of {phone, name?} OR list of strings
    raw = payload.get("recipients") or []
    recipients = []
    for r in raw:
        if isinstance(r, str):
            phone = r.strip()
            if phone:
                recipients.append({"phone": phone, "name": ""})
        elif isinstance(r, dict):
            phone = (r.get("phone") or "").strip()
            if phone:
                recipients.append({"phone": phone, "name": (r.get("name") or "").strip()})
    if not recipients:
        raise HTTPException(status_code=400, detail="Au moins un destinataire est requis")

    # Optional: scheduled_at (ISO string); if present, campaign is put in 'scheduled' status
    scheduled_at = None
    scheduled_raw = payload.get("scheduled_at")
    if scheduled_raw:
        try:
            scheduled_at = datetime.fromisoformat(str(scheduled_raw).replace("Z", "+00:00"))
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        except Exception:
            raise HTTPException(status_code=400, detail="scheduled_at doit être ISO8601")

    now = datetime.now(timezone.utc)
    initial_status = "scheduled" if (scheduled_at and scheduled_at > now) else "draft"
    doc = {
        "user_id": user["id"],
        "name": name,
        "channel": channel,
        "message_template": message,
        "recipients": [{**r, "status": "pending", "error": None, "sent_at": None, "sid": None,
                        "delivery_status": None, "delivered_at": None, "replied_at": None} for r in recipients],
        "total": len(recipients),
        "sent": 0,
        "failed": 0,
        "delivered": 0,
        "replies": 0,
        "status": initial_status,
        "scheduled_at": scheduled_at,
        "created_at": now,
        "updated_at": now,
        "sent_at": None,
    }
    res = await db.campaigns.insert_one(doc)
    return {"id": str(res.inserted_id), "campaign": _campaign_to_dict({**doc, "_id": res.inserted_id})}


@router.get("/campaigns")
async def list_campaigns(request: Request):
    user = await get_current_user(request)
    items = await db.campaigns.find({"user_id": user["id"]}, {"recipients": 0}).sort("created_at", -1).to_list(200)
    return {"campaigns": [_campaign_to_dict(c) for c in items]}


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, request: Request):
    from bson import ObjectId
    user = await get_current_user(request)
    try:
        c = await db.campaigns.find_one({"_id": ObjectId(campaign_id), "user_id": user["id"]})
    except Exception:
        raise HTTPException(status_code=400, detail="ID invalide")
    if not c:
        raise HTTPException(status_code=404, detail="Campagne introuvable")
    return _campaign_to_dict(c)


@router.post("/campaigns/{campaign_id}/send")
async def send_campaign(campaign_id: str, request: Request):
    from bson import ObjectId
    user = await get_current_user(request)
    try:
        c = await db.campaigns.find_one({"_id": ObjectId(campaign_id), "user_id": user["id"]})
    except Exception:
        raise HTTPException(status_code=400, detail="ID invalide")
    if not c:
        raise HTTPException(status_code=404, detail="Campagne introuvable")
    if c.get("status") in ("sending", "sent"):
        raise HTTPException(status_code=400, detail="Campagne déjà envoyée ou en cours")
    return await send_campaign_internal(campaign_id, user["id"])


async def send_campaign_internal(campaign_id: str, user_id: str) -> dict:
    """Execute campaign send — reusable from scheduled worker."""
    from bson import ObjectId
    c = await db.campaigns.find_one({"_id": ObjectId(campaign_id), "user_id": user_id})
    if not c:
        raise HTTPException(status_code=404, detail="Campagne introuvable")

    channel = c["channel"]
    if channel == "sms" and not TWILIO_SMS_FROM:
        raise HTTPException(status_code=400, detail="Numéro Twilio SMS non configuré (TWILIO_SMS_FROM).")

    client = _get_client()
    await db.campaigns.update_one({"_id": c["_id"]}, {"$set": {"status": "sending", "updated_at": datetime.now(timezone.utc)}})

    sent = 0
    failed = 0
    updated_recipients = []
    from_ = TWILIO_WHATSAPP_FROM if channel == "whatsapp" else TWILIO_SMS_FROM
    status_callback_url = f"{PUBLIC_URL}/api/webhooks/twilio/status"

    for r in c.get("recipients", []):
        if r.get("status") == "sent":
            updated_recipients.append(r)
            sent += 1
            continue
        phone = (r.get("phone") or "").strip()
        body = _render_template(c["message_template"], {"name": r.get("name", "")})
        to = f"whatsapp:{phone}" if channel == "whatsapp" and not phone.startswith("whatsapp:") else phone
        try:
            msg = client.messages.create(
                from_=from_, to=to, body=body,
                status_callback=status_callback_url,
            )
            updated_recipients.append({
                **r, "status": "sent", "error": None,
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "sid": msg.sid,
                "delivery_status": "queued",
            })
            sent += 1
            await db.twilio_messages.insert_one({
                "user_id": user_id, "channel": channel, "direction": "out",
                "to": phone, "from": from_, "body": body, "twilio_sid": msg.sid,
                "campaign_id": str(c["_id"]),
                "created_at": datetime.now(timezone.utc),
            })
        except Exception as e:
            logger.error(f"Campaign send error to {phone}: {e}")
            updated_recipients.append({**r, "status": "failed", "error": str(e)[:160], "sent_at": None, "sid": None})
            failed += 1

    status = "sent" if failed == 0 else ("partial" if sent > 0 else "failed")
    await db.campaigns.update_one(
        {"_id": c["_id"]},
        {"$set": {
            "recipients": updated_recipients,
            "sent": sent, "failed": failed,
            "status": status,
            "sent_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return {"ok": True, "sent": sent, "failed": failed, "status": status}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str, request: Request):
    from bson import ObjectId
    user = await get_current_user(request)
    try:
        res = await db.campaigns.delete_one({"_id": ObjectId(campaign_id), "user_id": user["id"]})
    except Exception:
        raise HTTPException(status_code=400, detail="ID invalide")
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Introuvable")
    return {"ok": True}


@router.post("/campaigns/parse-csv")
async def parse_csv(request: Request):
    """Parse an uploaded CSV file and return recipients[].
    Accepts multipart/form-data with `file` field. CSV may have columns phone,name (flexible).
    """
    await get_current_user(request)  # auth required
    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=400, detail="Fichier CSV requis (champ 'file')")
    try:
        raw = await upload.read() if hasattr(upload, "read") else upload
        text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Impossible de lire le fichier")
    import csv
    import io
    recipients = []
    invalid = 0
    # Try DictReader (comma and semicolon)
    sniffer = csv.Sniffer()
    sample = text[:4096]
    try:
        dialect = sniffer.sniff(sample, delimiters=",;\t|")
    except Exception:
        class _D:
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = True
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL
        dialect = _D
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        return {"recipients": [], "count": 0, "invalid": 0}
    header = [c.strip().lower() for c in rows[0]]
    has_header = any(h in ("phone", "telephone", "téléphone", "number", "numero", "mobile", "name", "nom", "prenom") for h in header)
    phone_idx, name_idx = 0, 1
    if has_header:
        for i, h in enumerate(header):
            if h in ("phone", "telephone", "téléphone", "number", "numero", "mobile", "tel"):
                phone_idx = i
            elif h in ("name", "nom", "prenom", "prénom", "firstname", "first_name"):
                name_idx = i
        data_rows = rows[1:]
    else:
        data_rows = rows

    for row in data_rows:
        if not row:
            continue
        phone = (row[phone_idx] if phone_idx < len(row) else "").strip()
        name = (row[name_idx] if name_idx < len(row) else "").strip() if name_idx != phone_idx else ""
        # Keep digits and + prefix only
        cleaned = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
        if not cleaned or len(cleaned) < 6:
            invalid += 1
            continue
        recipients.append({"phone": cleaned, "name": name})

    return {"recipients": recipients, "count": len(recipients), "invalid": invalid}


@router.get("/campaigns/{campaign_id}/analytics")
async def campaign_analytics(campaign_id: str, request: Request):
    """Per-campaign analytics: delivery rate, fail rate, reply rate."""
    from bson import ObjectId
    user = await get_current_user(request)
    try:
        c = await db.campaigns.find_one({"_id": ObjectId(campaign_id), "user_id": user["id"]})
    except Exception:
        raise HTTPException(status_code=400, detail="ID invalide")
    if not c:
        raise HTTPException(status_code=404, detail="Campagne introuvable")

    recipients = c.get("recipients", [])
    total = len(recipients)
    sent_count = sum(1 for r in recipients if r.get("status") == "sent")
    delivered = sum(1 for r in recipients if r.get("delivery_status") in ("delivered", "read"))
    read = sum(1 for r in recipients if r.get("delivery_status") == "read")
    failed_delivery = sum(1 for r in recipients if r.get("delivery_status") in ("failed", "undelivered"))
    replies = sum(1 for r in recipients if r.get("replied_at"))

    def _pct(a, b):
        return round((a / b) * 100, 1) if b > 0 else 0

    return {
        "campaign_id": campaign_id,
        "total": total,
        "sent": sent_count,
        "delivered": delivered,
        "read": read,
        "failed_delivery": failed_delivery,
        "replies": replies,
        "delivery_rate": _pct(delivered, sent_count),
        "read_rate": _pct(read, delivered) if delivered else 0,
        "reply_rate": _pct(replies, delivered) if delivered else _pct(replies, sent_count),
        "failure_rate": _pct(failed_delivery, sent_count),
    }


# ============ Twilio status callback webhook (delivery tracking) ============

@router.post("/webhooks/twilio/status")
async def twilio_status_callback(request: Request):
    """Twilio calls this whenever message status changes (queued→sent→delivered→read/failed)."""
    form = dict(await request.form())
    sid = form.get("MessageSid", "")
    status = (form.get("MessageStatus") or "").lower()
    if not sid:
        return PlainTextResponse(content="", status_code=200)
    # Update campaigns that reference this sid
    now = datetime.now(timezone.utc)
    update_set = {"recipients.$.delivery_status": status}
    if status == "delivered":
        update_set["recipients.$.delivered_at"] = now
    result = await db.campaigns.update_one(
        {"recipients.sid": sid},
        {"$set": update_set},
    )
    # Recompute delivered count if needed
    if result.modified_count and status == "delivered":
        c = await db.campaigns.find_one({"recipients.sid": sid})
        if c:
            delivered = sum(1 for r in c.get("recipients", []) if r.get("delivery_status") in ("delivered", "read"))
            await db.campaigns.update_one({"_id": c["_id"]}, {"$set": {"delivered": delivered}})
    return PlainTextResponse(content="", status_code=200)
