"""
Agent Configuration router — each user can customize each agent (support, commercial, etc.)
with a custom system prompt, tone, language, quick actions, FAQs, auto-escalate, etc.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from config import db
from models import AgentConfigPayload
from utils import get_current_user

router = APIRouter()

VALID_AGENTS = {"support", "analysis", "commercial", "rh", "finance", "marketing"}

DEFAULT_CONFIG = {
    "display_name": None,
    "tone": "friendly",
    "language": "fr",
    "system_prompt": "",
    "welcome_message": "",
    "quick_actions": [],
    "faqs": [],
    "auto_escalate": True,
    "restricted_topics": [],
}


def _serialize(doc: dict) -> dict:
    out = {**DEFAULT_CONFIG, **{k: v for k, v in (doc or {}).items() if k != "_id"}}
    if isinstance(out.get("updated_at"), datetime):
        out["updated_at"] = out["updated_at"].isoformat()
    return out


@router.get("/agent-configs/{agent_type}")
async def get_agent_config(agent_type: str, request: Request):
    if agent_type not in VALID_AGENTS:
        raise HTTPException(status_code=400, detail="Unknown agent type")
    user = await get_current_user(request)
    doc = await db.agent_configs.find_one({"user_id": user["id"], "agent_type": agent_type})
    return _serialize(doc or {"agent_type": agent_type})


@router.put("/agent-configs/{agent_type}")
async def update_agent_config(agent_type: str, payload: AgentConfigPayload, request: Request):
    if agent_type not in VALID_AGENTS:
        raise HTTPException(status_code=400, detail="Unknown agent type")
    user = await get_current_user(request)
    data = payload.model_dump(exclude_unset=True)
    # Convert nested pydantic models
    if "quick_actions" in data and data["quick_actions"] is not None:
        data["quick_actions"] = [qa if isinstance(qa, dict) else qa.model_dump() for qa in data["quick_actions"]]
    if "faqs" in data and data["faqs"] is not None:
        data["faqs"] = [f if isinstance(f, dict) else f.model_dump() for f in data["faqs"]]
    data["updated_at"] = datetime.now(timezone.utc)
    await db.agent_configs.update_one(
        {"user_id": user["id"], "agent_type": agent_type},
        {"$set": data, "$setOnInsert": {"user_id": user["id"], "agent_type": agent_type}},
        upsert=True,
    )
    doc = await db.agent_configs.find_one({"user_id": user["id"], "agent_type": agent_type})
    return _serialize(doc)


@router.get("/agent-configs")
async def list_agent_configs(request: Request):
    user = await get_current_user(request)
    docs = await db.agent_configs.find({"user_id": user["id"]}).to_list(50)
    return {"configs": {d.get("agent_type"): _serialize(d) for d in docs}}
