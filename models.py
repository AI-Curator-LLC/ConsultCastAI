"""Session record schema and API request/response models."""

import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class UpdateProfileRequest(BaseModel):
    name: str | None = None
    company: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class CheckoutRequest(BaseModel):
    plan: str  # "pro_monthly" | "pro_annual" — Team is deliberately not built yet, see the phasing note in billing setup


class ConversationTurn(BaseModel):
    role: str  # "user" (consultant) | "assistant" (persona)
    content: str


class SessionRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rep_id: str
    persona_name: str
    persona_role: str
    scenario_title: str
    scenario_product: str
    voice_tier: str = "standard"
    call_direction: str = "outbound"  # "inbound" (they called you) | "outbound" (you called them)
    active_scenario_id: str
    conversation: list[ConversationTurn] = Field(default_factory=list)
    pressure: int = 75
    trust: int = 20
    specificity: int = 50
    duration_sec: int = 0
    debrief: str | None = None
    status: str = "active"  # "active" | "completed"
    # Set once, at construction, never touched again. Existing sessions saved
    # before this field existed get "now" the first time they're re-loaded
    # (the default_factory firing on that load, not their real start time) —
    # a one-time quirk of JSON storage having no real migration, not
    # something new sessions from here on run into.
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class StartSessionRequest(BaseModel):
    persona_id: str
    scenario_id: str
    voice_tier: str = "standard"
    call_direction: str = "outbound"  # "inbound" | "outbound"


class TurnRequest(BaseModel):
    message: str


class TurnResponse(BaseModel):
    persona_reply: str
    pressure: int
    trust: int
    specificity: int
    coaching_note_kind: str
    coaching_note_text: str


class EndSessionResponse(BaseModel):
    session_id: str
    debrief: str
    duration_sec: int


class AvatarTokenRequest(BaseModel):
    persona_id: str


class AvatarTokenResponse(BaseModel):
    session_token: str
