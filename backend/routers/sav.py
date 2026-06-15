"""
SAV router — After-sales customer support for Switia users.
End-users (our SaaS customers) open tickets to reach the Switia support team.
"""
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone
from bson import ObjectId
from config import db
from models import SavTicketCreate, SavReplyCreate
from utils import get_current_user
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_STATUSES = {"open", "in_progress", "resolved", "closed"}


def _ticket_to_dict(t: dict) -> dict:
    return {
        "id": str(t["_id"]),
        "subject": t["subject"],
        "category": t.get("category", "general"),
        "status": t.get("status", "open"),
        "user_id": t["user_id"],
        "user_name": t.get("user_name", ""),
        "user_email": t.get("user_email", ""),
        "created_at": t["created_at"].isoformat() if isinstance(t.get("created_at"), datetime) else t.get("created_at"),
        "updated_at": t["updated_at"].isoformat() if isinstance(t.get("updated_at"), datetime) else t.get("updated_at"),
        "last_message_at": t.get("last_message_at").isoformat() if isinstance(t.get("last_message_at"), datetime) else t.get("last_message_at"),
        "messages": [
            {
                "id": m.get("id"),
                "author_id": m.get("author_id"),
                "author_name": m.get("author_name"),
                "author_role": m.get("author_role", "user"),
                "message": m.get("message", ""),
                "created_at": m["created_at"].isoformat() if isinstance(m.get("created_at"), datetime) else m.get("created_at"),
            }
            for m in t.get("messages", [])
        ],
    }


@router.post("/sav/tickets")
async def create_sav_ticket(payload: SavTicketCreate, request: Request):
    user = await get_current_user(request)
    now = datetime.now(timezone.utc)
    first_message = {
        "id": str(ObjectId()),
        "author_id": user["id"],
        "author_name": user["name"],
        "author_role": "user",
        "message": payload.message.strip()[:5000],
        "created_at": now,
    }
    doc = {
        "user_id": user["id"],
        "user_name": user["name"],
        "user_email": user["email"],
        "subject": payload.subject.strip()[:200],
        "category": (payload.category or "general").strip()[:40],
        "status": "open",
        "messages": [first_message],
        "created_at": now,
        "updated_at": now,
        "last_message_at": now,
    }
    res = await db.sav_tickets.insert_one(doc)
    # Notify user (own notification)
    await db.notifications.insert_one({
        "user_id": user["id"],
        "title": "Ticket SAV créé",
        "message": f"Votre demande « {doc['subject']} » a bien été enregistrée. Notre équipe vous répondra rapidement.",
        "read": False,
        "created_at": now,
    })
    doc["_id"] = res.inserted_id
    return _ticket_to_dict(doc)


@router.get("/sav/tickets")
async def list_my_sav_tickets(request: Request):
    user = await get_current_user(request)
    tickets = await db.sav_tickets.find({"user_id": user["id"]}).sort("last_message_at", -1).to_list(100)
    return {"tickets": [_ticket_to_dict(t) for t in tickets]}


@router.get("/sav/tickets/{ticket_id}")
async def get_sav_ticket(ticket_id: str, request: Request):
    user = await get_current_user(request)
    try:
        oid = ObjectId(ticket_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ticket id")
    t = await db.sav_tickets.find_one({"_id": oid})
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    is_admin = user.get("role") == "admin"
    if not is_admin and t["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    # Mark as read for the ticket owner
    if t["user_id"] == user["id"]:
        now = datetime.now(timezone.utc)
        await db.sav_tickets.update_one({"_id": oid}, {"$set": {"user_last_read_at": now}})
        t["user_last_read_at"] = now
    return _ticket_to_dict(t)


@router.get("/sav/unread-count")
async def sav_unread_count(request: Request):
    """Count admin replies the user has NOT yet seen. Used by the float SAV widget."""
    user = await get_current_user(request)
    tickets = await db.sav_tickets.find({"user_id": user["id"]}).to_list(500)
    total = 0
    for t in tickets:
        last_read = t.get("user_last_read_at")
        for m in t.get("messages", []):
            if m.get("author_role") != "admin":
                continue
            created = m.get("created_at")
            if not last_read or (isinstance(created, datetime) and created > last_read):
                total += 1
    return {"unread": total}


@router.post("/sav/tickets/{ticket_id}/reply")
async def reply_sav_ticket(ticket_id: str, payload: SavReplyCreate, request: Request):
    user = await get_current_user(request)
    try:
        oid = ObjectId(ticket_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ticket id")
    t = await db.sav_tickets.find_one({"_id": oid})
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")

    is_admin = user.get("role") == "admin"
    if not is_admin and t["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    now = datetime.now(timezone.utc)
    new_message = {
        "id": str(ObjectId()),
        "author_id": user["id"],
        "author_name": user["name"],
        "author_role": "admin" if is_admin else "user",
        "message": payload.message.strip()[:5000],
        "created_at": now,
    }
    # If admin replies, auto-set status to in_progress (unless resolved/closed)
    new_status = t.get("status", "open")
    if is_admin and new_status == "open":
        new_status = "in_progress"

    await db.sav_tickets.update_one(
        {"_id": oid},
        {
            "$push": {"messages": new_message},
            "$set": {"updated_at": now, "last_message_at": now, "status": new_status},
        },
    )
    # Notify the counterparty (user if admin replied, admins if user replied)
    if is_admin:
        await db.notifications.insert_one({
            "user_id": t["user_id"],
            "title": "Réponse de l'équipe Switia",
            "message": f"Nouvelle réponse sur « {t['subject']} »",
            "read": False,
            "created_at": now,
        })

    t = await db.sav_tickets.find_one({"_id": oid})
    return _ticket_to_dict(t)


@router.patch("/sav/tickets/{ticket_id}/status")
async def update_sav_status(ticket_id: str, payload: dict, request: Request):
    user = await get_current_user(request)
    is_admin = user.get("role") == "admin"
    new_status = (payload.get("status") or "").strip()
    if new_status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    try:
        oid = ObjectId(ticket_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ticket id")
    t = await db.sav_tickets.find_one({"_id": oid})
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    # End-users can only close/reopen their own ticket; admins can do anything
    if not is_admin:
        if t["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden")
        if new_status not in {"closed", "open"}:
            raise HTTPException(status_code=403, detail="Seuls les administrateurs peuvent changer ce statut")
    await db.sav_tickets.update_one(
        {"_id": oid},
        {"$set": {"status": new_status, "updated_at": datetime.now(timezone.utc)}},
    )
    t = await db.sav_tickets.find_one({"_id": oid})
    return _ticket_to_dict(t)


# ----- Admin listing -----
@router.get("/admin/sav/tickets")
async def admin_list_sav_tickets(request: Request, status: str = ""):
    user = await get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    query = {}
    if status and status in VALID_STATUSES:
        query["status"] = status
    tickets = await db.sav_tickets.find(query).sort("last_message_at", -1).to_list(500)
    return {"tickets": [_ticket_to_dict(t) for t in tickets]}
