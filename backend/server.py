from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, APIRouter, HTTPException, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import Response as FastAPIResponse
from starlette.middleware.cors import CORSMiddleware
from bson import ObjectId
import os
import logging
from pathlib import Path
from typing import List, Dict
import uuid
from datetime import datetime, timezone, timedelta
import pandas as pd
import io
import json
import stripe
import asyncio
import secrets
import jwt

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from emergentintegrations.llm.chat import LlmChat, UserMessage
from emergentintegrations.payments.stripe.checkout import StripeCheckout, CheckoutSessionRequest

from config import db, EMERGENT_LLM_KEY, STRIPE_API_KEY, SUBSCRIPTION_PLANS, COOKIE_SECURE, COOKIE_SAMESITE, IS_PRODUCTION, JWT_SECRET, JWT_ALGORITHM
from models import (
    DataAnalysisRequest, DataAnalysisResponse, SubscriptionPlan, CheckoutRequest,
    CheckoutResponse, UserSubscription, WidgetKeyCreate
)
from utils import hash_password, get_current_user, send_payment_confirmation_email

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("server")

# App & Router
app = FastAPI(title="Switia AI Platform")
api_router = APIRouter(prefix="/api")

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ============ Import & include routers ============
from routers.auth import router as auth_router
from routers.support import router as support_router
from routers.sav import router as sav_router
from routers.security import router as security_router
from routers.channels import router as channels_router
from routers.agent_config import router as agent_config_router
from routers.oauth_gmail import router as oauth_gmail_router
from routers.showcase import router as showcase_router

api_router.include_router(auth_router)
api_router.include_router(support_router)
api_router.include_router(sav_router)
api_router.include_router(security_router)
api_router.include_router(channels_router)
api_router.include_router(agent_config_router)
api_router.include_router(oauth_gmail_router)
api_router.include_router(showcase_router)

# ============ Data Analysis Agent ============

DATA_ANALYSIS_SYSTEM_PROMPT = """You are a professional data analyst AI assistant for Switia. You help users understand their data by:
1. Providing clear, insightful summaries of datasets
2. Identifying key patterns, trends, and anomalies
3. Answering questions about the data in natural language
4. Suggesting relevant visualizations
Format your responses clearly with sections when appropriate."""

data_sessions: Dict[str, Dict] = {}

@api_router.post("/analysis/upload")
async def upload_data(file: UploadFile = File(...), request: Request = None):
    user = await get_current_user(request)
    session_id = str(uuid.uuid4())
    content = await file.read()
    filename = file.filename.lower()
    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(content))
        elif filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise HTTPException(status_code=400, detail="Format non supporté. Utilisez CSV ou Excel.")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"Erreur de lecture: {str(e)}")
    summary = {"rows": len(df), "columns": len(df.columns), "column_names": list(df.columns), "dtypes": {k: str(v) for k, v in df.dtypes.items()}, "missing_values": {k: int(v) for k, v in df.isnull().sum().items() if v > 0}, "numeric_columns": list(df.select_dtypes(include=['number']).columns), "categorical_columns": list(df.select_dtypes(include=['object', 'category']).columns)}
    numeric_stats = {}
    for col in summary["numeric_columns"]:
        stats = df[col].describe()
        numeric_stats[col] = {"mean": float(stats.get("mean", 0)), "median": float(df[col].median()), "std": float(stats.get("std", 0)), "min": float(stats.get("min", 0)), "max": float(stats.get("max", 0))}
    summary["numeric_stats"] = numeric_stats
    charts = []
    for col in summary["categorical_columns"][:2]:
        vc = df[col].value_counts().head(10)
        charts.append({"type": "bar", "title": f"Distribution de {col}", "data": [{"name": str(k), "value": int(v)} for k, v in vc.items()]})
    if summary["categorical_columns"]:
        col = summary["categorical_columns"][0]
        vc = df[col].value_counts().head(8)
        charts.append({"type": "pie", "title": f"Répartition de {col}", "data": [{"name": str(k), "value": int(v)} for k, v in vc.items()]})
    for col in summary["numeric_columns"][:2]:
        sample = df[col].dropna().head(50).tolist()
        charts.append({"type": "line", "title": f"Tendance de {col}", "data": [{"index": i, "value": v} for i, v in enumerate(sample)]})
    if len(summary["numeric_columns"]) >= 2:
        cx, cy = summary["numeric_columns"][0], summary["numeric_columns"][1]
        sdf = df[[cx, cy]].dropna().head(100)
        charts.append({"type": "scatter", "title": f"{cx} vs {cy}", "xKey": cx, "yKey": cy, "data": [{"x": float(r[cx]), "y": float(r[cy])} for _, r in sdf.iterrows()]})
    data_sessions[session_id] = {"df": df, "summary": summary, "user_id": user["id"], "filename": file.filename}
    await db.agent_activity.insert_one({"agent_type": "analysis", "user_id": user["id"], "session_id": session_id, "action": "upload", "filename": file.filename, "timestamp": datetime.now(timezone.utc)})
    return {"session_id": session_id, "summary": summary, "charts": charts}

@api_router.post("/analysis/query", response_model=DataAnalysisResponse)
async def query_data(query_data: DataAnalysisRequest, request: Request):
    user = await get_current_user(request)
    if query_data.session_id not in data_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    session = data_sessions[query_data.session_id]
    if session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    summary = session["summary"]
    df = session["df"]
    sample_data = df.head(5).to_string()
    context = f"Dataset: {summary['rows']} rows, {summary['columns']} columns\nColumns: {', '.join(summary['column_names'])}\nSample:\n{sample_data}\n\nNumeric stats: {json.dumps(summary.get('numeric_stats', {}), indent=2)}"
    try:
        chat = LlmChat(api_key=EMERGENT_LLM_KEY, session_id=f"analysis_{query_data.session_id}", system_message=DATA_ANALYSIS_SYSTEM_PROMPT).with_model("anthropic", "claude-sonnet-4-5-20250514")
        response_text = await chat.send_message(UserMessage(text=f"Dataset context:\n{context}\n\nQuestion: {query_data.question}"))
    except Exception as e:
        logger.error(f"Analysis LLM error: {e}")
        response_text = "I apologize, but I'm having trouble analyzing the data right now."
    await db.agent_activity.insert_one({"agent_type": "analysis", "user_id": user["id"], "session_id": query_data.session_id, "action": "query", "timestamp": datetime.now(timezone.utc)})
    return DataAnalysisResponse(answer=response_text, summary=summary)

@api_router.get("/analysis/export/{session_id}")
async def export_analysis(session_id: str, request: Request):
    user = await get_current_user(request)
    if session_id not in data_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    session = data_sessions[session_id]
    if session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    sample_rows = session["df"].head(20).to_dict(orient='records')
    for row in sample_rows:
        for k, v in row.items():
            if pd.isna(v):
                row[k] = None
            elif hasattr(v, 'item'):
                row[k] = v.item()
    return {"filename": session["filename"], "summary": session["summary"], "sample_data": sample_rows, "exported_at": datetime.now(timezone.utc).isoformat()}

# ============ Dashboard ============

@api_router.get("/dashboard/stats")
async def get_dashboard_stats(request: Request, period: str = "all"):
    user = await get_current_user(request)
    now = datetime.now(timezone.utc)
    date_filter = {}
    if period == "7d":
        date_filter = {"timestamp": {"$gte": now - timedelta(days=7)}}
    elif period == "30d":
        date_filter = {"timestamp": {"$gte": now - timedelta(days=30)}}
    elif period == "90d":
        date_filter = {"timestamp": {"$gte": now - timedelta(days=90)}}
    conv_filter = {"user_id": user["id"], "role": "user", **date_filter}
    ticket_filter = {"user_id": user["id"]}
    if date_filter:
        ticket_filter["created_at"] = date_filter.get("timestamp")
    total_conversations = await db.support_conversations.count_documents(conv_filter)
    total_tickets = await db.tickets.count_documents({"user_id": user["id"]} if not date_filter else ticket_filter)
    total_analyses = await db.agent_activity.count_documents({"user_id": user["id"], "agent_type": "analysis", "action": "upload", **date_filter})
    resolved_tickets = await db.tickets.count_documents({"user_id": user["id"], "status": {"$in": ["resolved", "closed"]}})
    escalated_count = await db.agent_activity.count_documents({"user_id": user["id"], "agent_type": "support", "escalated": True, **date_filter})
    support_total = await db.agent_activity.count_documents({"user_id": user["id"], "agent_type": "support", **date_filter})
    escalation_rate = round((escalated_count / support_total * 100), 1) if support_total > 0 else 0
    widget_sessions = await db.widget_conversations.count_documents({"owner_id": user["id"], "role": "user", **date_filter})
    timeline_start = now - timedelta(days=30)
    pipeline = [{"$match": {"user_id": user["id"], "timestamp": {"$gte": timeline_start}}}, {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}, "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}, {"$limit": 30}]
    timeline_data = await db.agent_activity.aggregate(pipeline).to_list(30)
    support_activity = await db.agent_activity.find({"user_id": user["id"], "agent_type": "support", **date_filter}, {"_id": 0, "action": 1, "timestamp": 1, "escalated": 1}).sort("timestamp", -1).limit(10).to_list(10)
    data_activity = await db.agent_activity.find({"user_id": user["id"], "agent_type": "analysis", **date_filter}, {"_id": 0, "action": 1, "timestamp": 1, "filename": 1}).sort("timestamp", -1).limit(10).to_list(10)
    for item in support_activity + data_activity:
        item["timestamp"] = item["timestamp"].isoformat() if isinstance(item["timestamp"], datetime) else item["timestamp"]
    return {"total_conversations": total_conversations, "total_tickets": total_tickets, "total_analyses": total_analyses, "resolved_tickets": resolved_tickets, "escalation_rate": escalation_rate, "widget_sessions": widget_sessions, "activity_timeline": [{"date": t["_id"], "count": t["count"]} for t in timeline_data], "support_agent_activity": support_activity, "data_agent_activity": data_activity, "period": period}


@api_router.get("/dashboard/time-saved")
async def get_time_saved(request: Request):
    """Estimate time & money saved by AI automation across all channels."""
    user = await get_current_user(request)
    uid = user["id"]

    # Count automated AI responses across all sources (each = one human message saved)
    support_replies = await db.support_conversations.count_documents({"user_id": uid, "role": "agent"})
    widget_replies = await db.widget_conversations.count_documents({"owner_id": uid, "role": "agent"})
    agent_colls = ["agent_commercial_conversations", "agent_rh_conversations", "agent_finance_conversations", "agent_marketing_conversations"]
    agent_replies = 0
    for c in agent_colls:
        agent_replies += await db[c].count_documents({"user_id": uid, "role": "agent"})
    email_replies_sent = await db.email_replies.count_documents({"user_id": uid, "sent": True})
    email_replies_drafted = await db.email_replies.count_documents({"user_id": uid})

    total = support_replies + widget_replies + agent_replies + email_replies_sent
    # Average time saved per handled message (minutes of human work)
    minutes_per_msg = 3
    minutes_total = total * minutes_per_msg
    hours = round(minutes_total / 60, 1)
    days = round(hours / 8, 1)
    # Estimated cost saved (assume 25€/h — average salary cost of a support agent)
    cost_eur = round(hours * 25)

    return {
        "messages_handled": total,
        "minutes_saved": minutes_total,
        "hours_saved": hours,
        "workdays_saved": days,
        "cost_saved_eur": cost_eur,
        "breakdown": {
            "support": support_replies,
            "widget": widget_replies,
            "other_agents": agent_replies,
            "emails_sent": email_replies_sent,
            "emails_drafted": email_replies_drafted,
        },
    }


@api_router.get("/dashboard/time-saved/history")
async def get_time_saved_history(request: Request, days: int = 30):
    """Return daily time-saved evolution for a chart + month-over-month comparison."""
    user = await get_current_user(request)
    uid = user["id"]
    days = max(7, min(180, days))
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    def _daily_pipeline(match):
        return [
            {"$match": match},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}, "count": {"$sum": 1}}},
        ]

    # Helper to accumulate per-day counts across collections
    daily = {}
    # 1) support + all agent-type conversations (role == 'agent')
    base_conv = [
        ("support_conversations", {"user_id": uid, "role": "agent", "timestamp": {"$gte": start}}),
        ("widget_conversations", {"owner_id": uid, "role": "agent", "timestamp": {"$gte": start}}),
        ("agent_commercial_conversations", {"user_id": uid, "role": "agent", "timestamp": {"$gte": start}}),
        ("agent_rh_conversations", {"user_id": uid, "role": "agent", "timestamp": {"$gte": start}}),
        ("agent_finance_conversations", {"user_id": uid, "role": "agent", "timestamp": {"$gte": start}}),
        ("agent_marketing_conversations", {"user_id": uid, "role": "agent", "timestamp": {"$gte": start}}),
    ]
    for coll, match in base_conv:
        async for row in db[coll].aggregate(_daily_pipeline(match)):
            d = row["_id"]
            daily[d] = daily.get(d, 0) + row["count"]

    # 2) email replies sent
    async for row in db.email_replies.aggregate([
        {"$match": {"user_id": uid, "sent": True, "received_at": {"$gte": start}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$received_at"}}, "count": {"$sum": 1}}},
    ]):
        d = row["_id"]
        daily[d] = daily.get(d, 0) + row["count"]

    # Build complete timeline (fill zero days)
    series = []
    for i in range(days):
        day = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        msgs = int(daily.get(day, 0))
        series.append({"date": day, "messages": msgs, "minutes": msgs * 3, "hours": round(msgs * 3 / 60, 2)})

    # Month-over-month cost
    thirty_days_ago = now - timedelta(days=30)
    sixty_days_ago = now - timedelta(days=60)
    async def _count_range(coll, match_extra, start_dt, end_dt):
        match = {**match_extra, "timestamp": {"$gte": start_dt, "$lt": end_dt}}
        return await db[coll].count_documents(match)
    
    def _sum_month(start_dt, end_dt):
        return sum(1 for d in series if start_dt.strftime("%Y-%m-%d") <= d["date"] < end_dt.strftime("%Y-%m-%d") for _ in range(d["messages"]))

    # Simpler: aggregate series for last 30 vs previous 30
    cur_msgs = sum(d["messages"] for d in series if d["date"] >= thirty_days_ago.strftime("%Y-%m-%d"))
    prev_msgs = sum(d["messages"] for d in series if sixty_days_ago.strftime("%Y-%m-%d") <= d["date"] < thirty_days_ago.strftime("%Y-%m-%d"))
    delta_pct = None
    if prev_msgs > 0:
        delta_pct = round(((cur_msgs - prev_msgs) / prev_msgs) * 100, 1)

    return {
        "days": days,
        "series": series,
        "current_month_messages": cur_msgs,
        "previous_month_messages": prev_msgs,
        "delta_pct": delta_pct,
        "current_month_hours": round(cur_msgs * 3 / 60, 1),
        "current_month_cost": round(cur_msgs * 3 / 60 * 25),
    }


@api_router.get("/dashboard/agents")
async def get_agent_stats(request: Request):
    """Per-agent stats for all 6 agents"""
    user = await get_current_user(request)
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    agents_stats = []
    
    # Support agent
    support_convs = await db.support_conversations.count_documents({"user_id": user["id"], "role": "user", "timestamp": {"$gte": month_start}})
    support_last = await db.support_conversations.find_one({"user_id": user["id"]}, sort=[("timestamp", -1)])
    agents_stats.append({"type": "support", "name": "Agent Support", "conversations": support_convs, "last_activity": support_last["timestamp"].isoformat() if support_last and isinstance(support_last.get("timestamp"), datetime) else None})
    
    # Analysis agent
    analysis_count = await db.agent_activity.count_documents({"user_id": user["id"], "agent_type": "analysis", "timestamp": {"$gte": month_start}})
    analysis_last = await db.agent_activity.find_one({"user_id": user["id"], "agent_type": "analysis"}, sort=[("timestamp", -1)])
    agents_stats.append({"type": "analysis", "name": "Analyse Données", "conversations": analysis_count, "last_activity": analysis_last["timestamp"].isoformat() if analysis_last and isinstance(analysis_last.get("timestamp"), datetime) else None})
    
    # Generic agents
    for agent_type, name in [("commercial", "Agent Commercial"), ("rh", "Agent RH"), ("finance", "Agent Finance"), ("marketing", "Agent Marketing")]:
        collection = f"agent_{agent_type}_conversations"
        count = await db[collection].count_documents({"user_id": user["id"], "role": "user", "timestamp": {"$gte": month_start}})
        last = await db[collection].find_one({"user_id": user["id"]}, sort=[("timestamp", -1)])
        agents_stats.append({"type": agent_type, "name": name, "conversations": count, "last_activity": last["timestamp"].isoformat() if last and isinstance(last.get("timestamp"), datetime) else None})
    
    return {"agents": agents_stats}

# ============ Subscription & Pricing ============

@api_router.get("/plans", response_model=List[SubscriptionPlan])
async def get_plans():
    return [SubscriptionPlan(**p) for p in SUBSCRIPTION_PLANS.values()]

@api_router.get("/subscription", response_model=UserSubscription)
async def get_user_subscription(request: Request):
    user = await get_current_user(request)
    sub = await db.subscriptions.find_one({"user_id": user["id"]}, {"_id": 0})
    plan_id = sub["plan_id"] if sub else "free"
    plan = SUBSCRIPTION_PLANS.get(plan_id, SUBSCRIPTION_PLANS["free"])
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    conversations_used = await db.support_conversations.count_documents({"user_id": user["id"], "role": "user", "timestamp": {"$gte": month_start}})
    analyses_used = await db.agent_activity.count_documents({"user_id": user["id"], "agent_type": "analysis", "action": "upload", "timestamp": {"$gte": month_start}})
    return UserSubscription(plan_id=plan_id, plan_name=plan["name"], conversations_used=conversations_used, conversations_limit=plan["conversations_limit"], analyses_used=analyses_used, analyses_limit=plan["analyses_limit"], valid_until=sub.get("valid_until") if sub else None)

@api_router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(checkout_data: CheckoutRequest, request: Request):
    user = await get_current_user(request)
    plan = SUBSCRIPTION_PLANS.get(checkout_data.plan_id)
    if not plan or plan["price"] <= 0:
        raise HTTPException(status_code=400, detail="Invalid plan")
    if plan.get("contact_only"):
        raise HTTPException(status_code=400, detail="This plan requires contacting sales")
    host_url = str(request.base_url)
    webhook_url = f"{host_url}api/webhook/stripe"
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    checkout_request = CheckoutSessionRequest(amount=plan["price"], currency=plan["currency"], product_name=f"Switia - Forfait {plan['name']}", success_url=f"{checkout_data.origin_url}/pricing?session_id={{CHECKOUT_SESSION_ID}}", cancel_url=f"{checkout_data.origin_url}/pricing?cancelled=true", metadata={"user_id": user["id"], "plan_id": plan["id"]})
    checkout_response = await stripe_checkout.create_checkout_session(checkout_request)
    await db.payment_transactions.insert_one({"session_id": checkout_response.session_id, "user_id": user["id"], "plan_id": plan["id"], "plan_name": plan["name"], "amount": plan["price"], "currency": plan["currency"], "status": "created", "payment_status": "pending", "created_at": datetime.now(timezone.utc)})
    return CheckoutResponse(checkout_url=checkout_response.url, session_id=checkout_response.session_id)

@api_router.get("/checkout/status/{session_id}")
async def get_checkout_status(session_id: str, request: Request):
    user = await get_current_user(request)
    transaction = await db.payment_transactions.find_one({"session_id": session_id, "user_id": user["id"]})
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if transaction.get("payment_status") == "paid":
        return {"status": "complete", "payment_status": "paid", "plan_id": transaction["plan_id"]}
    try:
        stripe.api_key = STRIPE_API_KEY
        if "sk_test_emergent" in STRIPE_API_KEY:
            stripe.api_base = "https://integrations.emergentagent.com/stripe"
        session = stripe.checkout.Session.retrieve(session_id)
        payment_status = session.payment_status
        session_status = session.status
        await db.payment_transactions.update_one({"session_id": session_id}, {"$set": {"status": session_status, "payment_status": payment_status, "updated_at": datetime.now(timezone.utc)}})
        if payment_status == "paid" and transaction.get("payment_status") != "paid":
            valid_until = datetime.now(timezone.utc) + timedelta(days=30)
            await db.subscriptions.update_one({"user_id": user["id"]}, {"$set": {"plan_id": transaction["plan_id"], "plan_name": transaction["plan_name"], "valid_until": valid_until, "activated_at": datetime.now(timezone.utc)}}, upsert=True)
            await db.payment_transactions.update_one({"session_id": session_id}, {"$set": {"payment_status": "paid"}})
            asyncio.create_task(send_payment_confirmation_email(email=user["email"], name=user["name"], plan_name=transaction["plan_name"], amount=transaction.get("amount", 0), currency=transaction.get("currency", "eur")))
        return {"status": session_status, "payment_status": payment_status, "plan_id": transaction["plan_id"]}
    except stripe.error.InvalidRequestError:
        return {"status": "expired", "payment_status": "unpaid", "plan_id": transaction["plan_id"]}
    except Exception as e:
        logger.error(f"Checkout status error: {e}")
        raise HTTPException(status_code=500, detail="Unable to verify payment status")

@api_router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    try:
        body = await request.body()
        signature = request.headers.get("Stripe-Signature")
        host_url = str(request.base_url)
        stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=f"{host_url}api/webhook/stripe")
        webhook_response = await stripe_checkout.handle_webhook(body, signature)
        if webhook_response.payment_status == "paid":
            await db.payment_transactions.update_one({"session_id": webhook_response.session_id}, {"$set": {"payment_status": "paid", "event_id": webhook_response.event_id, "updated_at": datetime.now(timezone.utc)}})
            transaction = await db.payment_transactions.find_one({"session_id": webhook_response.session_id})
            if transaction:
                await db.subscriptions.update_one({"user_id": transaction["user_id"]}, {"$set": {"plan_id": transaction["plan_id"], "plan_name": transaction["plan_name"], "valid_until": datetime.now(timezone.utc) + timedelta(days=30), "activated_at": datetime.now(timezone.utc)}}, upsert=True)
                user_doc = await db.users.find_one({"_id": ObjectId(transaction["user_id"])})
                if user_doc:
                    asyncio.create_task(send_payment_confirmation_email(email=user_doc["email"], name=user_doc.get("name", "Client"), plan_name=transaction["plan_name"], amount=transaction.get("amount", 0), currency=transaction.get("currency", "eur")))
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}

@api_router.get("/transactions")
async def get_transactions(request: Request):
    user = await get_current_user(request)
    transactions = await db.payment_transactions.find({"user_id": user["id"]}, {"_id": 0, "session_id": 1, "plan_id": 1, "plan_name": 1, "amount": 1, "currency": 1, "payment_status": 1, "created_at": 1}).sort("created_at", -1).to_list(50)
    for t in transactions:
        if isinstance(t.get("created_at"), datetime):
            t["created_at"] = t["created_at"].isoformat()
    return {"transactions": transactions}

# ============ Widget ============

@api_router.post("/widget/keys")
async def create_widget_key(key_data: WidgetKeyCreate, request: Request):
    user = await get_current_user(request)
    api_key = f"switia_wk_{secrets.token_hex(24)}"
    doc = {"key_id": str(uuid.uuid4()), "api_key": api_key, "name": key_data.name, "user_id": user["id"], "allowed_origins": key_data.allowed_origins, "active": True, "created_at": datetime.now(timezone.utc)}
    await db.widget_keys.insert_one(doc)
    return {"key_id": doc["key_id"], "api_key": api_key, "name": doc["name"], "allowed_origins": doc["allowed_origins"], "created_at": doc["created_at"].isoformat()}

@api_router.get("/widget/keys")
async def list_widget_keys(request: Request):
    user = await get_current_user(request)
    keys = await db.widget_keys.find({"user_id": user["id"], "active": True}, {"_id": 0, "key_id": 1, "api_key": 1, "name": 1, "allowed_origins": 1, "created_at": 1}).sort("created_at", -1).to_list(20)
    for k in keys:
        if isinstance(k.get("created_at"), datetime):
            k["created_at"] = k["created_at"].isoformat()
        full_key = k["api_key"]
        k["api_key_masked"] = full_key[:16] + "..." + full_key[-4:]
        k["api_key_full"] = full_key
    return {"keys": keys}

@api_router.delete("/widget/keys/{key_id}")
async def revoke_widget_key(key_id: str, request: Request):
    user = await get_current_user(request)
    result = await db.widget_keys.update_one({"key_id": key_id, "user_id": user["id"]}, {"$set": {"active": False}})
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"message": "Key revoked"}

SUPPORT_SYSTEM_PROMPT_FOR_WIDGET = """Tu es l'agent Support IA intégré sur le site/app de notre client.
Ton rôle : répondre aux questions des visiteurs de façon courte, claire et utile.
Règles :
- 1 seule action / réponse simple à la fois. Pas de pavé.
- Pas de jargon technique (pas de "règles", "déclencheurs", "workflows").
- Ton amical, direct, tutoiement, phrases courtes.
- Détecte la langue de l'utilisateur et réponds dans la même langue.
- Si tu ne sais pas, propose de laisser ses coordonnées pour qu'un humain réponde.
- Formatage : Markdown léger (gras, listes courtes). Jamais de titres H1/H2."""

@api_router.post("/widget/chat")
async def widget_chat(request: Request):
    api_key = request.headers.get("X-Widget-Key", "")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing widget API key")
    key_doc = await db.widget_keys.find_one({"api_key": api_key, "active": True})
    if not key_doc:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    body = await request.json()
    message = body.get("message", "").strip()
    session_id = body.get("session_id") or str(uuid.uuid4())
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    owner_id = key_doc["user_id"]
    await db.widget_conversations.insert_one({"session_id": session_id, "owner_id": owner_id, "message": message, "role": "user", "timestamp": datetime.now(timezone.utc)})
    history = await db.widget_conversations.find({"session_id": session_id}, {"_id": 0, "message": 1, "role": 1}).sort("timestamp", 1).to_list(20)
    context = "\n".join(["User: " + h['message'] if h['role'] == 'user' else "Agent: " + h['message'] for h in history[:-1]])
    training_entries = await db.training_data.find({"user_id": owner_id}, {"_id": 0, "question": 1, "answer": 1}).to_list(100)
    training_context = ""
    if training_entries:
        training_context = "\n\nCustom Knowledge Base:\n" + "\n".join([f"Q: {e['question']}\nA: {e['answer']}" for e in training_entries]) + "\n"
    try:
        chat = LlmChat(api_key=EMERGENT_LLM_KEY, session_id=f"widget_{session_id}", system_message=SUPPORT_SYSTEM_PROMPT_FOR_WIDGET + training_context).with_model("openai", "gpt-5.2")
        full_message = f"Previous conversation:\n{context}\n\nUser's new message: {message}" if context else message
        response_text = await chat.send_message(UserMessage(text=full_message))
    except Exception as e:
        logger.error(f"Widget LLM error: {e}")
        response_text = "I apologize, but I'm experiencing technical difficulties."
    escalated = response_text.strip().startswith("[ESCALATE]")
    if escalated:
        response_text = response_text.replace("[ESCALATE]", "").strip()
    await db.widget_conversations.insert_one({"session_id": session_id, "owner_id": owner_id, "message": response_text, "role": "agent", "escalated": escalated, "timestamp": datetime.now(timezone.utc)})
    return {"response": response_text, "session_id": session_id, "escalated": escalated}

WIDGET_EMBED_JS = """
(function() {
  var scriptTag = document.querySelector('script[data-api-key]');
  if (!scriptTag) return;
  var apiKey = scriptTag.getAttribute('data-api-key');
  var apiBase = scriptTag.src.replace('/api/widget/embed.js', '');
  var sessionId = null, isOpen = false;
  var brandName = 'Switia', brandColor = '#2563EB';

  function init(bn, bc) {
    brandName = bn || 'Switia';
    brandColor = bc || '#2563EB';
    var style = document.createElement('style');
    style.textContent = '#switia-widget-btn{position:fixed;bottom:24px;right:24px;width:56px;height:56px;border-radius:50%;background:' + brandColor + ';border:none;cursor:pointer;z-index:99999;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 20px rgba(0,0,0,.3);transition:transform .2s}#switia-widget-btn:hover{transform:scale(1.08)}#switia-widget-btn svg{width:28px;height:28px;fill:#fff}#switia-widget-panel{position:fixed;bottom:92px;right:24px;width:380px;max-height:520px;background:#0C1222;border:1px solid #1E293B;border-radius:16px;z-index:99999;display:none;flex-direction:column;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,.5);font-family:-apple-system,BlinkMacSystemFont,sans-serif}#switia-widget-panel.open{display:flex}.sw-hdr{background:' + brandColor + ';padding:14px 18px;color:#fff;font-weight:600;font-size:15px}.sw-msgs{flex:1;overflow-y:auto;padding:14px;min-height:280px;max-height:340px}.sw-msg{margin-bottom:10px;display:flex}.sw-msg.user{justify-content:flex-end}.sw-msg .bbl{max-width:80%;padding:10px 14px;border-radius:14px;font-size:13px;line-height:1.5;word-wrap:break-word}.sw-msg.user .bbl{background:' + brandColor + ';color:#fff;border-bottom-right-radius:4px}.sw-msg.agent .bbl{background:#1E293B;color:#F8FAFC;border-bottom-left-radius:4px}.sw-inp{padding:10px 14px;border-top:1px solid #1E293B;display:flex;gap:8px}.sw-inp input{flex:1;background:#121A2F;border:1px solid #1E293B;border-radius:8px;padding:8px 12px;color:#F8FAFC;font-size:13px;outline:none}.sw-inp button{background:' + brandColor + ';color:#fff;border:none;border-radius:8px;padding:8px 16px;cursor:pointer;font-size:13px}.sw-inp button:disabled{opacity:.5}';
    document.head.appendChild(style);
    var btn = document.createElement('button');
    btn.id = 'switia-widget-btn';
    btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H6l-2 2V4h16v12z"/></svg>';
    document.body.appendChild(btn);
    var panel = document.createElement('div');
    panel.id = 'switia-widget-panel';
    panel.innerHTML = '<div class="sw-hdr">' + brandName + ' Support</div><div class="sw-msgs" id="sw-msgs"><div class="sw-msg agent"><div class="bbl">Bonjour ! Comment puis-je vous aider ?</div></div></div><div class="sw-inp"><input id="sw-input" placeholder="Votre message..." /><button id="sw-send">Envoyer</button></div>';
    document.body.appendChild(panel);
    btn.onclick = function() { isOpen = !isOpen; panel.classList.toggle('open', isOpen); };
    var input = document.getElementById('sw-input'), sendBtn = document.getElementById('sw-send'), msgs = document.getElementById('sw-msgs');
    function addMsg(r, t) { var d = document.createElement('div'); d.className = 'sw-msg ' + r; d.innerHTML = '<div class="bbl">' + t.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</div>'; msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight; }
    function send() { var t = input.value.trim(); if (!t) return; addMsg('user', t); input.value = ''; sendBtn.disabled = true; fetch(apiBase + '/api/widget/chat', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Widget-Key': apiKey }, body: JSON.stringify({ message: t, session_id: sessionId }) }).then(function(r) { return r.json(); }).then(function(d) { sessionId = d.session_id; addMsg('agent', d.response); sendBtn.disabled = false; }).catch(function() { addMsg('agent', 'Erreur de connexion.'); sendBtn.disabled = false; }); }
    sendBtn.onclick = send;
    input.onkeydown = function(e) { if (e.key === 'Enter') send(); };
  }

  // Fetch white-label settings from API
  fetch(apiBase + '/api/widget/branding?key=' + apiKey)
    .then(function(r) { return r.json(); })
    .then(function(d) { init(d.brand_name, d.primary_color); })
    .catch(function() { init('Switia', '#2563EB'); });
})();
"""

@api_router.get("/widget/embed.js")
async def serve_embed_js():
    return FastAPIResponse(content=WIDGET_EMBED_JS, media_type="application/javascript", headers={"Cache-Control": "public, max-age=3600", "Access-Control-Allow-Origin": "*"})

@api_router.get("/widget/branding")
async def get_widget_branding(key: str = ""):
    """Public endpoint to fetch white-label settings for a widget key"""
    if not key:
        return {"brand_name": "Switia", "primary_color": "#2563EB"}
    key_doc = await db.widget_keys.find_one({"api_key": key, "active": True})
    if not key_doc:
        return {"brand_name": "Switia", "primary_color": "#2563EB"}
    settings = await db.whitelabel.find_one({"user_id": key_doc["user_id"]}, {"_id": 0})
    return {
        "brand_name": settings.get("brand_name", "Switia") if settings else "Switia",
        "primary_color": settings.get("primary_color", "#2563EB") if settings else "#2563EB"
    }

@api_router.get("/widget/analytics")
async def get_widget_analytics(request: Request, period: str = "30d"):
    user = await get_current_user(request)
    now = datetime.now(timezone.utc)
    date_filter = {}
    if period == "7d":
        date_filter = {"timestamp": {"$gte": now - timedelta(days=7)}}
    elif period == "30d":
        date_filter = {"timestamp": {"$gte": now - timedelta(days=30)}}
    elif period == "90d":
        date_filter = {"timestamp": {"$gte": now - timedelta(days=90)}}
    total_messages = await db.widget_conversations.count_documents({"owner_id": user["id"], **date_filter})
    user_messages = await db.widget_conversations.count_documents({"owner_id": user["id"], "role": "user", **date_filter})
    pipeline_sessions = [{"$match": {"owner_id": user["id"], "role": "user", **date_filter}}, {"$group": {"_id": "$session_id"}}, {"$count": "total"}]
    session_result = await db.widget_conversations.aggregate(pipeline_sessions).to_list(1)
    unique_sessions = session_result[0]["total"] if session_result else 0
    timeline_start = now - timedelta(days=30)
    pipeline_timeline = [{"$match": {"owner_id": user["id"], "role": "user", "timestamp": {"$gte": timeline_start}}}, {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}, "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}, {"$limit": 30}]
    timeline_data = await db.widget_conversations.aggregate(pipeline_timeline).to_list(30)
    avg_per_session = round(user_messages / unique_sessions, 1) if unique_sessions > 0 else 0
    active_keys = await db.widget_keys.count_documents({"user_id": user["id"], "active": True})
    return {"total_messages": total_messages, "user_messages": user_messages, "unique_sessions": unique_sessions, "avg_messages_per_session": avg_per_session, "active_keys": active_keys, "timeline": [{"date": t["_id"], "count": t["count"]} for t in timeline_data], "period": period}

# ============ WebSocket Notifications ============

class NotificationManager:
    def __init__(self):
        self.connections: Dict[str, list] = {}
    
    async def connect(self, user_id: str, ws: WebSocket):
        await ws.accept()
        if user_id not in self.connections:
            self.connections[user_id] = []
        self.connections[user_id].append(ws)
    
    def disconnect(self, user_id: str, ws: WebSocket):
        if user_id in self.connections:
            self.connections[user_id] = [c for c in self.connections[user_id] if c != ws]
    
    async def send_notification(self, user_id: str, notification: dict):
        # Store in DB
        notification["user_id"] = user_id
        notification["read"] = False
        notification["created_at"] = datetime.now(timezone.utc)
        await db.notifications.insert_one(notification)
        # Push to connected clients
        if user_id in self.connections:
            message = json.dumps({"type": "notification", "data": {**notification, "created_at": notification["created_at"].isoformat(), "_id": str(notification.get("_id", ""))}})
            dead = []
            for ws in self.connections[user_id]:
                try:
                    await ws.send_text(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.connections[user_id].remove(ws)

notif_manager = NotificationManager()

@app.websocket("/ws/notifications/{token}")
async def websocket_notifications(websocket: WebSocket, token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload["sub"]
    except Exception:
        await websocket.close(code=4001)
        return
    
    await notif_manager.connect(user_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        notif_manager.disconnect(user_id, websocket)

@api_router.get("/notifications")
async def get_notifications(request: Request):
    user = await get_current_user(request)
    notifs = await db.notifications.find(
        {"user_id": user["id"]},
        {"_id": 0, "type": 1, "title": 1, "message": 1, "read": 1, "created_at": 1}
    ).sort("created_at", -1).limit(20).to_list(20)
    for n in notifs:
        if isinstance(n.get("created_at"), datetime):
            n["created_at"] = n["created_at"].isoformat()
    unread = await db.notifications.count_documents({"user_id": user["id"], "read": False})
    return {"notifications": notifs, "unread_count": unread}

@api_router.post("/notifications/read")
async def mark_notifications_read(request: Request):
    user = await get_current_user(request)
    await db.notifications.update_many({"user_id": user["id"], "read": False}, {"$set": {"read": True}})
    return {"message": "All notifications marked as read"}

# ============ Startup ============

@app.on_event("startup")
async def startup_event():
    logger.info(f"Cookie settings - Secure: {COOKIE_SECURE}, SameSite: {COOKIE_SAMESITE}, Production: {IS_PRODUCTION}")
    logger.info(f"Stripe API Key configured: {bool(STRIPE_API_KEY)}")
    await db.users.create_index("email", unique=True)
    await db.support_conversations.create_index([("session_id", 1), ("timestamp", 1)])
    await db.support_conversations.create_index([("user_id", 1), ("timestamp", -1)])
    await db.tickets.create_index("user_id")
    await db.agent_activity.create_index([("user_id", 1), ("timestamp", -1)])
    await db.training_data.create_index("user_id")
    await db.widget_keys.create_index([("api_key", 1), ("active", 1)])
    await db.widget_keys.create_index("user_id")
    await db.widget_conversations.create_index([("session_id", 1), ("timestamp", 1)])
    await db.notifications.create_index([("user_id", 1), ("created_at", -1)])
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@switia.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({"email": admin_email, "password_hash": hash_password(admin_password), "name": "Admin", "role": "admin", "created_at": datetime.now(timezone.utc)})
        logger.info(f"Admin user created: {admin_email}")
    Path("/app/memory").mkdir(exist_ok=True)
    with open("/app/memory/test_credentials.md", "w") as f:
        f.write(f"# Test Credentials\n\n## Admin Account\n- Email: {admin_email}\n- Password: {admin_password}\n- Role: admin\n\n## Auth Endpoints\n- POST /api/auth/register\n- POST /api/auth/login\n- POST /api/auth/logout\n- GET /api/auth/me\n")

@app.on_event("shutdown")
async def shutdown_db_client():
    from config import client as mongo_client
    mongo_client.close()

# Include router and middleware
app.include_router(api_router)
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','), allow_methods=["*"], allow_headers=["*"])
