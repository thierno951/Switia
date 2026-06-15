from pydantic import BaseModel, EmailStr
from typing import List, Optional, Any, Dict
from datetime import datetime


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None

class TeamMemberInvite(BaseModel):
    email: EmailStr
    name: str
    role: str = "member"

class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    created_at: datetime
    onboarding_completed: bool = False

class ChatMessage(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    session_id: str
    escalated: bool = False
    ticket_id: Optional[str] = None

class PreviewChatMessage(BaseModel):
    message: str
    setup_context: Optional[str] = None
    history: List[Dict[str, str]] = []  # [{"role": "user"|"agent", "content": "..."}]

class TicketCreate(BaseModel):
    subject: str
    description: str
    priority: str = "medium"

class TicketUpdate(BaseModel):
    status: str

class TicketResponse(BaseModel):
    id: str
    subject: str
    description: str
    priority: str
    status: str
    created_at: datetime
    user_id: str

class DataAnalysisRequest(BaseModel):
    question: str
    session_id: str

class DataAnalysisResponse(BaseModel):
    answer: str
    charts: Optional[List[Dict[str, Any]]] = None
    summary: Optional[Dict[str, Any]] = None

class SubscriptionPlan(BaseModel):
    id: str
    name: str
    price: float
    currency: str
    conversations_limit: int
    analyses_limit: int
    features: List[str]
    popular: bool
    contact_only: bool = False

class CheckoutRequest(BaseModel):
    plan_id: str
    origin_url: str

class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str

class UserSubscription(BaseModel):
    plan_id: str
    plan_name: str
    conversations_used: int
    conversations_limit: int
    analyses_used: int
    analyses_limit: int
    valid_until: Optional[datetime] = None

class TrainingEntry(BaseModel):
    question: str
    answer: str
    category: Optional[str] = "general"

class WidgetKeyCreate(BaseModel):
    name: str
    allowed_origins: Optional[List[str]] = ["*"]

class WhiteLabelSettings(BaseModel):
    brand_name: Optional[str] = None
    primary_color: Optional[str] = None
    logo_url: Optional[str] = None


# ============ SAV (After-sales customer support for Switia users) ============

class SavTicketCreate(BaseModel):
    subject: str
    message: str
    category: Optional[str] = "general"  # general | billing | technical | account | feature


class SavReplyCreate(BaseModel):
    message: str


# ============ Security / GDPR ============

class AccountDeletionConfirm(BaseModel):
    password: str


class TwoFactorVerify(BaseModel):
    code: str


class TwoFactorDisable(BaseModel):
    password: str
    code: str


class LoginPayload(BaseModel):
    email: EmailStr
    password: str
    totp_code: Optional[str] = None


# ============ Channels (Email / WhatsApp / Phone) ============

class EmailChannelConfig(BaseModel):
    enabled: bool = False
    imap_host: Optional[str] = None
    imap_port: Optional[int] = 993
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = 587
    email_address: Optional[str] = None
    app_password: Optional[str] = None  # stored encrypted
    auto_reply: bool = False  # if True, Switia sends; else drafts only
    signature: Optional[str] = None
    preferred_agent: Optional[str] = "support"


class WhatsAppChannelConfig(BaseModel):
    enabled: bool = False
    phone_number: Optional[str] = None  # E.164 format
    preferred_agent: Optional[str] = "support"


class PhoneChannelConfig(BaseModel):
    enabled: bool = False
    phone_number: Optional[str] = None
    preferred_agent: Optional[str] = "support"


class ChannelsPayload(BaseModel):
    email: Optional[EmailChannelConfig] = None
    whatsapp: Optional[WhatsAppChannelConfig] = None
    phone: Optional[PhoneChannelConfig] = None


# ============ Agent Configuration (per-user, per-agent) ============

class QuickAction(BaseModel):
    icon: str = "💬"
    label: str
    prompt: str


class FAQItem(BaseModel):
    question: str
    answer: str


class AgentConfigPayload(BaseModel):
    display_name: Optional[str] = None
    tone: Optional[str] = "friendly"  # friendly | formal | casual | pro
    language: Optional[str] = "fr"
    system_prompt: Optional[str] = None
    welcome_message: Optional[str] = None
    quick_actions: Optional[List[QuickAction]] = None
    faqs: Optional[List[FAQItem]] = None
    auto_escalate: Optional[bool] = True
    restricted_topics: Optional[List[str]] = None
