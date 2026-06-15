from dotenv import load_dotenv
load_dotenv()

import os
import secrets
from motor.motor_asyncio import AsyncIOMotorClient

# MongoDB
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# JWT
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")

# Cookies
IS_PRODUCTION = os.environ.get("ENVIRONMENT") == "production" or \
                "emergentagent.com" in os.environ.get("REACT_APP_BACKEND_URL", "") or \
                os.environ.get("KUBERNETES_SERVICE_HOST") is not None
COOKIE_SECURE = IS_PRODUCTION
COOKIE_SAMESITE = "none" if IS_PRODUCTION else "lax"

# Stripe
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "")

# Resend
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "onboarding@resend.dev")

# Plans
SUBSCRIPTION_PLANS = {
    "free": {
        "id": "free", "name": "Gratuit", "price": 0.0, "currency": "eur",
        "conversations_limit": 50, "analyses_limit": 5,
        "features": ["Agent Support (50 conv/mois)", "Agent Analyse (5/mois)", "Dashboard basique"],
        "popular": False, "contact_only": False
    },
    "pro": {
        "id": "pro", "name": "Pro", "price": 49.0, "currency": "eur",
        "conversations_limit": 1000, "analyses_limit": 100,
        "features": ["Agent Support (1000 conv/mois)", "Agent Analyse (100/mois)", "Dashboard avancé", "Export PDF", "Support prioritaire"],
        "popular": True, "contact_only": False
    },
    "pme": {
        "id": "pme", "name": "Business", "price": 99.0, "currency": "eur",
        "conversations_limit": 5000, "analyses_limit": 500,
        "features": ["Agent Support (5000 conv/mois)", "Agent Analyse (500/mois)", "Widget embarquable", "Base de connaissances", "Gestion d'équipe (5 membres)", "Export PDF"],
        "popular": False, "contact_only": False
    },
    "enterprise": {
        "id": "enterprise", "name": "Entreprise", "price": 199.0, "currency": "eur",
        "conversations_limit": -1, "analyses_limit": -1,
        "features": ["Conversations illimitées", "Analyses illimitées", "API Access", "Support dédié 24/7", "Formation équipe", "SLA garanti", "White-label"],
        "popular": False, "contact_only": False
    }
}
