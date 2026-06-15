"""Public showcase / ROI page.
Exposes anonymized aggregate stats from all users (total hours, messages, companies)
and a per-user 'share my ROI' card with pre-built LinkedIn text.
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse
from datetime import datetime, timezone, timedelta
from config import db
from utils import get_current_user

router = APIRouter()

MINUTES_PER_MSG = 3
COST_PER_HOUR_EUR = 25

# Map dominant agent → anonymized sector label (for testimonials)
SECTOR_BY_AGENT = {
    "support": "Services B2C",
    "commercial": "E-commerce",
    "rh": "PME — Ressources humaines",
    "finance": "Cabinet comptable",
    "marketing": "Agence marketing",
    "analysis": "Startup SaaS",
}

# Collections that store an AI-generated reply (role == 'agent')
AGENT_COLLECTIONS = [
    ("support_conversations", "user_id", "support"),
    ("widget_conversations", "owner_id", "support"),
    ("agent_commercial_conversations", "user_id", "commercial"),
    ("agent_rh_conversations", "user_id", "rh"),
    ("agent_finance_conversations", "user_id", "finance"),
    ("agent_marketing_conversations", "user_id", "marketing"),
]


async def _count_replies_for_user(uid: str) -> dict:
    """Return total & per-agent-type reply counts for a given user."""
    per_agent = {}
    total = 0
    for coll, uid_field, agent_label in AGENT_COLLECTIONS:
        n = await db[coll].count_documents({uid_field: uid, "role": "agent"})
        per_agent[agent_label] = per_agent.get(agent_label, 0) + n
        total += n
    emails_sent = await db.email_replies.count_documents({"user_id": uid, "sent": True})
    total += emails_sent
    per_agent["email"] = emails_sent
    return {"total": total, "per_agent": per_agent}


def _sector_from_breakdown(per_agent: dict) -> str:
    """Infer an anonymized sector label from dominant agent used."""
    if not per_agent:
        return "Entreprise"
    dominant = max(per_agent.items(), key=lambda kv: kv[1])[0]
    return SECTOR_BY_AGENT.get(dominant, "Entreprise")


def _stats_from_messages(total_msgs: int) -> dict:
    minutes = total_msgs * MINUTES_PER_MSG
    hours = round(minutes / 60, 1)
    days = round(hours / 8, 1)
    cost = round(hours * COST_PER_HOUR_EUR)
    return {"messages": total_msgs, "hours": hours, "workdays": days, "cost_eur": cost}


@router.get("/public/showcase")
async def public_showcase(limit: int = 8):
    """Public endpoint (no auth) — aggregate platform stats + anonymized (or opted-in) testimonials."""
    limit = max(3, min(20, limit))

    # Gather per-user totals
    users_cur = db.users.find({}, {"_id": 1, "created_at": 1, "showcase_opt_in": 1, "showcase_company": 1, "showcase_logo_url": 1, "showcase_quote": 1, "showcase_sector": 1})
    per_user: list[dict] = []
    async for u in users_cur:
        uid = str(u["_id"])
        counts = await _count_replies_for_user(uid)
        if counts["total"] <= 0:
            continue
        per_user.append({
            "uid": uid,
            "total": counts["total"],
            "per_agent": counts["per_agent"],
            "joined_at": u.get("created_at"),
            "opt_in": bool(u.get("showcase_opt_in")),
            "company": u.get("showcase_company") or "",
            "logo_url": u.get("showcase_logo_url") or "",
            "quote": u.get("showcase_quote") or "",
            "sector_override": u.get("showcase_sector") or "",
        })

    per_user.sort(key=lambda x: x["total"], reverse=True)

    # Platform-wide aggregates
    total_msgs = sum(p["total"] for p in per_user)
    aggregate = _stats_from_messages(total_msgs)
    active_companies = len(per_user)

    # Build testimonials: prefer opted-in companies (real name/logo), fallback to anonymized
    testimonials = []
    anon_idx = 0
    for p in per_user[:limit]:
        stats = _stats_from_messages(p["total"])
        sector = p["sector_override"] or _sector_from_breakdown(p["per_agent"])
        if p["opt_in"] and p["company"]:
            alias = p["company"]
            logo_url = p["logo_url"]
            custom_quote = p["quote"]
            is_opted = True
        else:
            alias = f"Entreprise {chr(65 + anon_idx)}"
            anon_idx += 1
            logo_url = ""
            custom_quote = ""
            is_opted = False
        joined = p.get("joined_at")
        since = None
        if isinstance(joined, datetime):
            if joined.tzinfo is None:
                joined = joined.replace(tzinfo=timezone.utc)
            since = max(1, (datetime.now(timezone.utc) - joined).days)
        testimonials.append({
            "alias": alias,
            "sector": sector,
            "messages": stats["messages"],
            "hours_saved": stats["hours"],
            "cost_saved_eur": stats["cost_eur"],
            "days_since_joined": since,
            "logo_url": logo_url,
            "custom_quote": custom_quote,
            "opted_in": is_opted,
        })

    return {
        "platform": {
            "active_companies": active_companies,
            "total_messages_handled": aggregate["messages"],
            "total_hours_saved": aggregate["hours"],
            "total_workdays_saved": aggregate["workdays"],
            "total_cost_saved_eur": aggregate["cost_eur"],
        },
        "testimonials": testimonials,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/showcase/my-card")
async def my_share_card(request: Request):
    """Authenticated — returns the current user's ROI share card + pre-built LinkedIn text."""
    user = await get_current_user(request)
    uid = user["id"]
    counts = await _count_replies_for_user(uid)
    stats = _stats_from_messages(counts["total"])
    sector = _sector_from_breakdown(counts["per_agent"])

    # Month-over-month delta (last 30 days vs previous 30 days)
    now = datetime.now(timezone.utc)
    last30_start = now - timedelta(days=30)
    prev30_start = now - timedelta(days=60)

    async def _range_count(start: datetime, end: datetime) -> int:
        total = 0
        for coll, uid_field, _ in AGENT_COLLECTIONS:
            total += await db[coll].count_documents({uid_field: uid, "role": "agent", "timestamp": {"$gte": start, "$lt": end}})
        total += await db.email_replies.count_documents({"user_id": uid, "sent": True, "created_at": {"$gte": start, "$lt": end}})
        return total

    current = await _range_count(last30_start, now)
    previous = await _range_count(prev30_start, last30_start)
    delta_pct = None
    if previous > 0:
        delta_pct = round(((current - previous) / previous) * 100, 1)

    share_text = (
        f"Grâce à Switia, notre équipe a automatisé {stats['messages']} messages clients "
        f"= {stats['hours']} heures gagnées et ~{stats['cost_eur']}€ économisés. "
        f"L'IA qui répond pendant qu'on dort.\n\n#IA #productivité #Switia"
    )

    return {
        "sector": sector,
        "messages": stats["messages"],
        "hours_saved": stats["hours"],
        "workdays_saved": stats["workdays"],
        "cost_saved_eur": stats["cost_eur"],
        "last_30_days_messages": current,
        "previous_30_days_messages": previous,
        "delta_pct": delta_pct,
        "share_text": share_text,
    }


# ============ Showcase opt-in (let users appear with real company on /showcase) ============

@router.get("/showcase/opt-in")
async def get_opt_in_status(request: Request):
    user = await get_current_user(request)
    from bson import ObjectId
    u = await db.users.find_one({"_id": ObjectId(user["id"])}, {"_id": 0, "showcase_opt_in": 1, "showcase_company": 1, "showcase_logo_url": 1, "showcase_quote": 1, "showcase_sector": 1})
    return {
        "opt_in": bool((u or {}).get("showcase_opt_in")),
        "company": (u or {}).get("showcase_company") or "",
        "logo_url": (u or {}).get("showcase_logo_url") or "",
        "quote": (u or {}).get("showcase_quote") or "",
        "sector": (u or {}).get("showcase_sector") or "",
    }


@router.put("/showcase/opt-in")
async def update_opt_in(payload: dict, request: Request):
    user = await get_current_user(request)
    from bson import ObjectId
    opt_in = bool(payload.get("opt_in"))
    company = (payload.get("company") or "").strip()
    logo_url = (payload.get("logo_url") or "").strip()
    quote = (payload.get("quote") or "").strip()
    sector = (payload.get("sector") or "").strip()
    if opt_in and not company:
        raise HTTPException(status_code=400, detail="Le nom de société est requis pour apparaître publiquement")
    if len(quote) > 280:
        raise HTTPException(status_code=400, detail="Le témoignage est limité à 280 caractères")
    await db.users.update_one(
        {"_id": ObjectId(user["id"])},
        {"$set": {
            "showcase_opt_in": opt_in,
            "showcase_company": company,
            "showcase_logo_url": logo_url,
            "showcase_quote": quote,
            "showcase_sector": sector,
        }},
    )
    return {"ok": True, "opt_in": opt_in}


# ============ Embeddable "social proof" ROI badge ============

@router.get("/public/roi-badge.js")
async def roi_badge_js(request: Request):
    """Serves a self-contained JS snippet to embed a live ROI badge on any site.

    Usage:
        <script src="https://<host>/api/public/roi-badge.js" async></script>
    """
    # Fetch fresh aggregates (simple inline call)
    users_cur = db.users.find({}, {"_id": 1})
    total_msgs = 0
    active = 0
    async for u in users_cur:
        uid = str(u["_id"])
        c = await _count_replies_for_user(uid)
        if c["total"] > 0:
            active += 1
            total_msgs += c["total"]
    stats = _stats_from_messages(total_msgs)

    # Build the origin: prefer forwarded proto/host (behind ingress), fallback to request.url
    import os
    public = os.environ.get("PUBLIC_URL")
    if public:
        origin = public.rstrip("/")
    else:
        fwd_proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        fwd_host = request.headers.get("x-forwarded-host") or request.url.netloc
        origin = f"{fwd_proto}://{fwd_host}"
    js = f"""(function(){{
  var data = {{
    hours: {stats['hours']},
    messages: {stats['messages']},
    cost: {stats['cost_eur']},
    companies: {active}
  }};
  var css = "position:fixed;bottom:20px;right:20px;z-index:2147483000;background:#0C1222;color:#F8FAFC;"+
            "border:1px solid #1E293B;border-radius:14px;padding:14px 16px;font-family:-apple-system,Segoe UI,Roboto,sans-serif;"+
            "box-shadow:0 10px 40px rgba(0,0,0,.35);max-width:280px;font-size:13px;line-height:1.4;";
  var html = '<div style="'+css+'" data-switia-roi-badge>'+
             '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'+
               '<div style="width:8px;height:8px;border-radius:50%;background:#10B981;box-shadow:0 0 8px #10B981;animation:switia-pulse 2s infinite"></div>'+
               '<span style="color:#10B981;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px">En direct · Switia</span>'+
             '</div>'+
             '<div style="color:#F8FAFC;font-weight:600;margin-bottom:2px">'+data.companies+' entreprises économisent du temps</div>'+
             '<div style="color:#94A3B8;font-size:12px">'+data.hours+'h gagnées · '+data.cost.toLocaleString('fr-FR')+'€ économisés</div>'+
             '<a href="{origin}/showcase" target="_blank" rel="noopener" style="display:inline-block;margin-top:8px;color:#3B82F6;font-size:12px;text-decoration:none;font-weight:500">Voir les cas clients →</a>'+
             '<button onclick="this.parentNode.remove()" aria-label="Fermer" style="position:absolute;top:6px;right:8px;background:none;border:none;color:#64748B;cursor:pointer;font-size:16px;line-height:1;padding:2px 6px">×</button>'+
             '</div>';
  var style = document.createElement('style');
  style.textContent = '@keyframes switia-pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}';
  document.head.appendChild(style);
  var wrap = document.createElement('div');
  wrap.innerHTML = html;
  document.body.appendChild(wrap.firstChild);
}})();
"""
    return PlainTextResponse(content=js, media_type="application/javascript; charset=utf-8")
