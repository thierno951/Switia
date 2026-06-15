from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
import uuid
import asyncio
import logging
import io
from datetime import datetime, timezone
from typing import List
from bson import ObjectId
from config import db, EMERGENT_LLM_KEY
from models import ChatMessage, ChatResponse, TicketCreate, TicketUpdate, TicketResponse, TrainingEntry, PreviewChatMessage
from utils import get_current_user, send_escalation_email
from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)
router = APIRouter()


def _extract_pdf(content: bytes, filename: str) -> str:
    from PyPDF2 import PdfReader
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)[:8000]


def _extract_docx(content: bytes, filename: str) -> str:
    from docx import Document
    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())[:8000]


def _extract_tabular(content: bytes, filename: str) -> str:
    import pandas as pd
    reader = pd.read_csv if filename.lower().endswith('.csv') else pd.read_excel
    df = reader(io.BytesIO(content))
    summary = (
        f"Fichier: {filename}\nLignes: {len(df)}, Colonnes: {len(df.columns)}\n"
        f"Colonnes: {', '.join(df.columns)}\n\nAperçu:\n{df.head(10).to_string()}"
    )
    return summary[:8000]


def _extract_txt(content: bytes, filename: str) -> str:
    return content.decode('utf-8', errors='ignore')[:8000]


def _extract_image(content: bytes, filename: str) -> str:
    return f"[Image uploadée: {filename}. Décrivez ce que vous souhaitez que j'analyse dans cette image.]"


_EXTRACTORS = [
    (('.pdf',), _extract_pdf),
    (('.docx',), _extract_docx),
    (('.csv', '.xlsx', '.xls'), _extract_tabular),
    (('.txt',), _extract_txt),
    (('.jpg', '.jpeg', '.png', '.webp'), _extract_image),
]


def extract_text_from_file(content: bytes, filename: str) -> str:
    """Extract text content from uploaded files. Dispatches by extension."""
    fname = filename.lower()
    extractor = next((fn for exts, fn in _EXTRACTORS if fname.endswith(exts)), None)
    if extractor is None:
        return f"[Fichier uploadé: {filename}. Format non supporté pour l'extraction de texte.]"
    try:
        return extractor(content, filename)
    except Exception as e:
        logger.error(f"File extraction error: {e}")
        return f"[Erreur lors de la lecture du fichier: {filename}]"

SUPPORT_SYSTEM_PROMPT = """Tu es l'agent Support IA de Switia. Ton rôle : aider l'utilisateur à automatiser ses messages clients.

🎯 RÈGLE D'OR : donne TOUJOURS 1 seule action simple à faire immédiatement. Pas de pavé, pas de guide complet, pas de jargon technique (pas de "déclencheurs", "règles", "niveaux", "automatisation partielle/avancée"). Zéro.

🏷️ MARQUEUR OBLIGATOIRE (TOUT PREMIER TOKEN de CHAQUE réponse) :
- [STEP:1/3]  → quand tu demandes à l'utilisateur le CANAL (où : email, WhatsApp, site…)
- [STEP:2/3]  → quand tu demandes le TYPE de messages (quoi : FAQ, commandes, RDV…)
- [STEP:3/3]  → quand tu demandes 1 EXEMPLE concret de message typique
- [STEP:DONE] → quand le setup des 3 étapes est terminé, tu proposes la config finale, ou pour toute conversation hors setup (questions générales, hors-sujet, etc.)

Le marqueur DOIT être écrit tel quel, sans espace avant, sur la première ligne, puis saute une ligne et commence ta réponse. Le marqueur sera masqué côté interface, n'y fais PAS référence dans le texte.

📝 PREMIER MESSAGE (si l'utilisateur n'a rien précisé) : réponds court, max 4 lignes, commence par [STEP:1/3] puis :

"Je peux automatiser tes messages clients en quelques minutes.

Dis-moi simplement :
👉 où tu veux répondre (email, WhatsApp, site…)
👉 ce que tu veux automatiser (FAQ, commandes, RDV…)"

📝 SI l'utilisateur a choisi un canal + un type : enchaîne en 3 étapes courtes, 1 question à la fois, avec le bon marqueur :
  1. [STEP:1/3] Canal (où)
  2. [STEP:2/3] Type de messages (quoi)
  3. [STEP:3/3] Exemple concret (peux-tu me donner 1 exemple de message typique ?)

Après ces 3 étapes : [STEP:DONE] propose une config prête et demande validation.

🗣️ Ton : amical, direct, rassurant. Utilise "tu". Phrases courtes. Emojis occasionnels (👉 ✅ ✨) mais sans abus.

📄 Formatage : Markdown léger (gras pour mots-clés, listes à puces courtes). Jamais de titres H1/H2.

🌍 Détecte la langue de l'utilisateur et réponds dans la même langue. Le marqueur [STEP:X/3] reste en anglais/format exact.

🚨 Escalade : si l'utilisateur demande un remboursement, a un problème technique complexe, ou une question légale/RH sensible, commence ta réponse par "[ESCALATE][STEP:DONE]" puis propose une réponse initiale utile.

❌ N'introduis JAMAIS de concepts comme "automatisation partielle/avancée", "déclencheurs", "règles", "workflows" dans un premier message. Ces notions arrivent seulement si l'utilisateur demande plus de détails."""

# Agent-specific prompts for new agents
AGENT_PROMPTS = {
    "commercial": """You are a professional AI sales agent for Switia. You help businesses with:
1. Qualifying leads and understanding prospect needs
2. Answering questions about products/services
3. Creating sales proposals and follow-up messages
4. Pipeline tracking suggestions and sales strategy advice
5. Objection handling and negotiation tips
Detect the user's language and respond in the same language. Be persuasive but honest.""",

    "rh": """You are a professional AI HR agent for Switia. You assist with:
1. Employee questions about policies, benefits, and procedures
2. Leave management and scheduling advice
3. Onboarding process guidance for new hires
4. Internal communication drafting
5. Compliance and labor law questions (general guidance, not legal advice)
Detect the user's language and respond in the same language. Be empathetic and precise.""",

    "finance": """You are a professional AI finance agent for Switia. You help with:
1. Invoice and billing questions
2. Budget planning and financial reporting advice
3. Expense tracking and categorization
4. Cash flow analysis and projections
5. General accounting and tax questions (guidance, not professional advice)
Detect the user's language and respond in the same language. Be precise with numbers.""",

    "marketing": """You are a professional AI marketing agent for Switia. You help with:
1. Campaign planning and strategy recommendations
2. Copywriting for ads, emails, and social media
3. SEO and content optimization suggestions
4. Performance analysis and KPI interpretation
5. Creative brainstorming for brand growth
Detect the user's language and respond in the same language. Be creative and data-driven."""
}

@router.post("/support/chat", response_model=ChatResponse)
async def support_chat(chat_data: ChatMessage, request: Request):
    user = await get_current_user(request)
    session_id = chat_data.session_id or str(uuid.uuid4())
    await db.support_conversations.insert_one({"session_id": session_id, "user_id": user["id"], "message": chat_data.message, "role": "user", "timestamp": datetime.now(timezone.utc)})
    history = await db.support_conversations.find({"session_id": session_id}, {"_id": 0, "message": 1, "role": 1}).sort("timestamp", 1).to_list(50)
    context = "\n".join([f"{'User' if h['role'] == 'user' else 'Agent'}: {h['message']}" for h in history[:-1]])
    training_entries = await db.training_data.find({"user_id": user["id"]}, {"_id": 0, "question": 1, "answer": 1}).to_list(100)
    training_context = ""
    if training_entries:
        faq_text = "\n".join([f"Q: {e['question']}\nA: {e['answer']}" for e in training_entries])
        training_context = f"\n\nCustom Knowledge Base:\n{faq_text}\n"
    system_prompt = SUPPORT_SYSTEM_PROMPT + training_context
    try:
        chat = LlmChat(api_key=EMERGENT_LLM_KEY, session_id=f"support_{session_id}", system_message=system_prompt).with_model("openai", "gpt-5.2")
        full_message = f"Previous conversation:\n{context}\n\nUser's new message: {chat_data.message}" if context else chat_data.message
        response_text = await chat.send_message(UserMessage(text=full_message))
    except Exception as e:
        logger.error(f"LLM error: {e}")
        response_text = "I apologize, but I'm experiencing technical difficulties. Please try again."
    escalated = response_text.strip().startswith("[ESCALATE]")
    if escalated:
        response_text = response_text.replace("[ESCALATE]", "").strip()
        # Send escalation email notification
        asyncio.create_task(send_escalation_email(user["email"], user["name"], chat_data.message, response_text, session_id))
    await db.support_conversations.insert_one({"session_id": session_id, "user_id": user["id"], "message": response_text, "role": "agent", "escalated": escalated, "timestamp": datetime.now(timezone.utc)})
    await db.agent_activity.insert_one({"agent_type": "support", "user_id": user["id"], "session_id": session_id, "action": "chat", "escalated": escalated, "timestamp": datetime.now(timezone.utc)})
    return ChatResponse(response=response_text, session_id=session_id, escalated=escalated)


PREVIEW_SYSTEM_PROMPT = """Tu es un agent Support IA DÉPLOYÉ EN PRODUCTION pour le compte d'un utilisateur de Switia. La personne qui te parle est un client final (ou un utilisateur en train de tester ton comportement).

🎯 Rôle : joue parfaitement un agent Support professionnel. Sois poli, direct, utile et rassurant. Réponds comme un vrai agent, PAS comme un assistant de configuration.

📋 Ta configuration actuelle :
{setup_context}

🧭 Règles :
- Réponds toujours en 2 à 5 phrases courtes, jamais de pavé.
- Utilise le vouvoiement avec un client final (à moins qu'il tutoie).
- Si une question dépasse ta configuration ou demande une action humaine, dis poliment que tu vas transmettre à un conseiller humain.
- Formatage : Markdown léger (gras pour mots-clés, listes courtes si pertinent).
- AUCUN marqueur de type [STEP:X/3], [ESCALATE], etc. Tu ne parles JAMAIS de ton système interne.
- Détecte la langue de l'utilisateur et réponds dans la même langue.

Tu es en démonstration : fais une excellente première impression."""


@router.post("/support/preview")
async def support_preview_chat(payload: PreviewChatMessage, request: Request):
    """Stateless preview chat — lets user test the configured agent as if they were a customer.
    No DB persistence. Full history is passed from the client each call."""
    user = await get_current_user(request)
    setup_context = payload.setup_context or "(aucune configuration spécifiée — joue le rôle d'un agent Support généraliste.)"
    history = payload.history or []
    context_lines = [f"{'Client' if h.get('role') == 'user' else 'Agent'}: {h.get('content', '')}" for h in history[-12:]]
    context = "\n".join(context_lines)
    system_prompt = PREVIEW_SYSTEM_PROMPT.format(setup_context=setup_context)
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"preview_{user['id']}_{uuid.uuid4().hex[:8]}",
            system_message=system_prompt,
        ).with_model("openai", "gpt-5.2")
        full_message = f"Previous conversation:\n{context}\n\nClient's new message: {payload.message}" if context else payload.message
        response_text = await chat.send_message(UserMessage(text=full_message))
    except Exception as e:
        logger.error(f"Preview LLM error: {e}")
        response_text = "Désolé, une erreur technique est survenue. Veuillez réessayer."
    return {"response": response_text.strip()}

@router.post("/agents/{agent_type}/chat", response_model=ChatResponse)
async def agent_chat(agent_type: str, chat_data: ChatMessage, request: Request):
    """Generic chat endpoint for Commercial, RH, Finance, Marketing agents"""
    user = await get_current_user(request)
    if agent_type not in AGENT_PROMPTS:
        raise HTTPException(status_code=404, detail="Agent non trouvé")
    session_id = chat_data.session_id or str(uuid.uuid4())
    collection = f"agent_{agent_type}_conversations"
    await db[collection].insert_one({"session_id": session_id, "user_id": user["id"], "message": chat_data.message, "role": "user", "timestamp": datetime.now(timezone.utc)})
    history = await db[collection].find({"session_id": session_id}, {"_id": 0, "message": 1, "role": 1}).sort("timestamp", 1).to_list(50)
    context = "\n".join([f"{'User' if h['role'] == 'user' else 'Agent'}: {h['message']}" for h in history[:-1]])
    try:
        chat = LlmChat(api_key=EMERGENT_LLM_KEY, session_id=f"{agent_type}_{session_id}", system_message=AGENT_PROMPTS[agent_type]).with_model("openai", "gpt-5.2")
        full_message = f"Previous conversation:\n{context}\n\nUser's new message: {chat_data.message}" if context else chat_data.message
        response_text = await chat.send_message(UserMessage(text=full_message))
    except Exception as e:
        logger.error(f"LLM error ({agent_type}): {e}")
        response_text = "Je rencontre des difficultés techniques. Veuillez réessayer."
    await db[collection].insert_one({"session_id": session_id, "user_id": user["id"], "message": response_text, "role": "agent", "timestamp": datetime.now(timezone.utc)})
    await db.agent_activity.insert_one({"agent_type": agent_type, "user_id": user["id"], "session_id": session_id, "action": "chat", "timestamp": datetime.now(timezone.utc)})
    return ChatResponse(response=response_text, session_id=session_id)

@router.post("/agents/{agent_type}/upload")
async def agent_upload_file(agent_type: str, request: Request, file: UploadFile = File(...), message: str = Form(""), session_id: str = Form("")):
    """Chat with file attachment for any agent"""
    user = await get_current_user(request)
    if agent_type not in AGENT_PROMPTS and agent_type != "support":
        raise HTTPException(status_code=404, detail="Agent non trouvé")
    
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Fichier trop volumineux (max 10 Mo)")
    
    file_text = extract_text_from_file(content, file.filename)
    sid = session_id or str(uuid.uuid4())
    user_msg = message.strip() if message.strip() else f"Analyse ce fichier : {file.filename}"
    full_prompt = f"{user_msg}\n\n--- Contenu du fichier ({file.filename}) ---\n{file_text}"
    
    # Store user message
    if agent_type == "support":
        collection = "support_conversations"
        system_prompt = SUPPORT_SYSTEM_PROMPT
    else:
        collection = f"agent_{agent_type}_conversations"
        system_prompt = AGENT_PROMPTS[agent_type]
    
    store_doc = {"session_id": sid, "user_id": user["id"], "message": user_msg, "role": "user", "has_file": True, "filename": file.filename, "timestamp": datetime.now(timezone.utc)}
    await db[collection].insert_one(store_doc)
    
    # Get history
    history = await db[collection].find({"session_id": sid}, {"_id": 0, "message": 1, "role": 1}).sort("timestamp", 1).to_list(50)
    context = "\n".join([("User: " + h['message'] if h['role'] == 'user' else "Agent: " + h['message']) for h in history[:-1]])
    
    try:
        chat = LlmChat(api_key=EMERGENT_LLM_KEY, session_id=f"{agent_type}_{sid}", system_message=system_prompt).with_model("openai", "gpt-5.2")
        ctx_msg = f"Previous conversation:\n{context}\n\n{full_prompt}" if context else full_prompt
        response_text = await chat.send_message(UserMessage(text=ctx_msg))
    except Exception as e:
        logger.error(f"LLM upload error ({agent_type}): {e}")
        response_text = "Je rencontre des difficultés techniques lors de l'analyse du fichier."
    
    escalated = response_text.strip().startswith("[ESCALATE]")
    if escalated:
        response_text = response_text.replace("[ESCALATE]", "").strip()
        if agent_type == "support":
            asyncio.create_task(send_escalation_email(user["email"], user["name"], user_msg, response_text, sid))
    
    await db[collection].insert_one({"session_id": sid, "user_id": user["id"], "message": response_text, "role": "agent", "escalated": escalated, "timestamp": datetime.now(timezone.utc)})
    await db.agent_activity.insert_one({"agent_type": agent_type, "user_id": user["id"], "session_id": sid, "action": "upload_chat", "filename": file.filename, "timestamp": datetime.now(timezone.utc)})
    
    return {"response": response_text, "session_id": sid, "escalated": escalated, "filename": file.filename}

@router.get("/agents/{agent_type}/sessions")
async def get_agent_sessions(agent_type: str, request: Request):
    user = await get_current_user(request)
    if agent_type not in AGENT_PROMPTS:
        raise HTTPException(status_code=404, detail="Agent non trouvé")
    collection = f"agent_{agent_type}_conversations"
    pipeline = [
        {"$match": {"user_id": user["id"], "role": "user"}},
        {"$sort": {"timestamp": 1}},
        {"$group": {"_id": "$session_id", "first_message": {"$first": "$message"}, "last_timestamp": {"$last": "$timestamp"}, "message_count": {"$sum": 1}}},
        {"$sort": {"last_timestamp": -1}}, {"$limit": 30}
    ]
    sessions = await db[collection].aggregate(pipeline).to_list(30)
    return {"sessions": [{"session_id": s["_id"], "title": s["first_message"][:60] + ("..." if len(s["first_message"]) > 60 else ""), "last_timestamp": s["last_timestamp"].isoformat() if isinstance(s["last_timestamp"], datetime) else s["last_timestamp"], "message_count": s["message_count"]} for s in sessions]}

@router.get("/agents/{agent_type}/history/{session_id}")
async def get_agent_history(agent_type: str, session_id: str, request: Request):
    user = await get_current_user(request)
    if agent_type not in AGENT_PROMPTS:
        raise HTTPException(status_code=404, detail="Agent non trouvé")
    collection = f"agent_{agent_type}_conversations"
    history = await db[collection].find({"session_id": session_id, "user_id": user["id"]}, {"_id": 0, "message": 1, "role": 1, "timestamp": 1}).sort("timestamp", 1).to_list(100)
    return {"history": history}

@router.post("/support/ticket", response_model=TicketResponse)
async def create_ticket(ticket_data: TicketCreate, request: Request):
    user = await get_current_user(request)
    ticket_doc = {"subject": ticket_data.subject, "description": ticket_data.description, "priority": ticket_data.priority, "status": "open", "user_id": user["id"], "created_at": datetime.now(timezone.utc)}
    result = await db.tickets.insert_one(ticket_doc)
    return TicketResponse(id=str(result.inserted_id), subject=ticket_data.subject, description=ticket_data.description, priority=ticket_data.priority, status="open", created_at=ticket_doc["created_at"], user_id=user["id"])

@router.get("/support/tickets", response_model=List[TicketResponse])
async def get_tickets(request: Request):
    user = await get_current_user(request)
    tickets = await db.tickets.find({"user_id": user["id"]}, {"_id": 1, "subject": 1, "description": 1, "priority": 1, "status": 1, "created_at": 1, "user_id": 1}).sort("created_at", -1).to_list(100)
    return [TicketResponse(id=str(t["_id"]), subject=t["subject"], description=t["description"], priority=t["priority"], status=t["status"], created_at=t["created_at"], user_id=t["user_id"]) for t in tickets]

@router.patch("/support/tickets/{ticket_id}")
async def update_ticket_status(ticket_id: str, update: TicketUpdate, request: Request):
    user = await get_current_user(request)
    valid_statuses = ["open", "in_progress", "resolved", "closed"]
    if update.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
    result = await db.tickets.update_one({"_id": ObjectId(ticket_id), "user_id": user["id"]}, {"$set": {"status": update.status, "updated_at": datetime.now(timezone.utc)}})
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket = await db.tickets.find_one({"_id": ObjectId(ticket_id)})
    return TicketResponse(id=str(ticket["_id"]), subject=ticket["subject"], description=ticket["description"], priority=ticket["priority"], status=ticket["status"], created_at=ticket["created_at"], user_id=ticket["user_id"])

@router.get("/support/history/{session_id}")
async def get_support_history(session_id: str, request: Request):
    user = await get_current_user(request)
    history = await db.support_conversations.find({"session_id": session_id, "user_id": user["id"]}, {"_id": 0, "message": 1, "role": 1, "timestamp": 1, "escalated": 1}).sort("timestamp", 1).to_list(100)
    return {"history": history}

@router.get("/support/sessions")
async def get_support_sessions(request: Request):
    user = await get_current_user(request)
    pipeline = [
        {"$match": {"user_id": user["id"], "role": "user"}}, {"$sort": {"timestamp": 1}},
        {"$group": {"_id": "$session_id", "first_message": {"$first": "$message"}, "last_timestamp": {"$last": "$timestamp"}, "message_count": {"$sum": 1}}},
        {"$sort": {"last_timestamp": -1}}, {"$limit": 30}
    ]
    sessions = await db.support_conversations.aggregate(pipeline).to_list(30)
    return {"sessions": [{"session_id": s["_id"], "title": s["first_message"][:60] + ("..." if len(s["first_message"]) > 60 else ""), "last_timestamp": s["last_timestamp"].isoformat() if isinstance(s["last_timestamp"], datetime) else s["last_timestamp"], "message_count": s["message_count"]} for s in sessions]}

@router.post("/support/training")
async def add_training_entry(entry: TrainingEntry, request: Request):
    user = await get_current_user(request)
    doc = {"entry_id": str(uuid.uuid4()), "user_id": user["id"], "question": entry.question, "answer": entry.answer, "category": entry.category, "created_at": datetime.now(timezone.utc)}
    await db.training_data.insert_one(doc)
    return {"entry_id": doc["entry_id"], "question": doc["question"], "answer": doc["answer"], "category": doc["category"], "created_at": doc["created_at"].isoformat()}

@router.get("/support/training")
async def get_training_entries(request: Request):
    user = await get_current_user(request)
    entries = await db.training_data.find({"user_id": user["id"]}, {"_id": 0, "entry_id": 1, "question": 1, "answer": 1, "category": 1, "created_at": 1}).sort("created_at", -1).to_list(200)
    for e in entries:
        if isinstance(e.get("created_at"), datetime):
            e["created_at"] = e["created_at"].isoformat()
    return {"entries": entries}

@router.delete("/support/training/{entry_id}")
async def delete_training_entry(entry_id: str, request: Request):
    user = await get_current_user(request)
    result = await db.training_data.delete_one({"entry_id": entry_id, "user_id": user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"message": "Entry deleted"}
