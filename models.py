"""Session record schema and API request/response models."""

import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    email: str
    password: str
    # Set only when accepting a team invite link — joins that team instead
    # of the normal "pending approval, needs their own subscription" path.
    # See main.py's /auth/signup and teams.py.
    invite_token: str | None = None


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


class DeleteAccountRequest(BaseModel):
    password: str


class CheckoutRequest(BaseModel):
    plan: str  # "pro_monthly" | "pro_annual" | "team_monthly" | "team_annual"


class InviteRequest(BaseModel):
    email: str


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
    # Set by the retention cleanup job once the transcript ages past
    # TRANSCRIPT_RETENTION_DAYS (see retention.py): conversation is emptied,
    # this flips to True, everything else (debrief, scores, metadata) is
    # kept. Declared here (not left as a bare dict key) so it survives a
    # SessionRecord(**raw) round trip instead of silently being dropped —
    # Pydantic v2 ignores undeclared extra fields by default.
    transcript_purged: bool = False
    # Server-side session time accounting (see minutes.py) — the server's own
    # clock, so none of it depends on anything the browser reports or keeps.
    # run_since is the start of the currently-running stretch (None while
    # paused or ended, and on a record that predates this), run_sec is every
    # already-closed stretch added up, charged_sec is how much has been
    # written to the minutes ledger so far (so settling twice never double-
    # charges). time_limit_sec is set only for a trial session.
    run_since: str | None = None
    run_sec: float = 0
    paused_at: str | None = None
    ended_at: str | None = None
    charged_sec: int = 0
    time_limit_sec: int | None = None
    # Set once, at construction, never touched again. Existing sessions saved
    # before this field existed get "now" the first time they're re-loaded
    # (the default_factory firing on that load, not their real start time) —
    # a one-time quirk of JSON storage having no real migration, not
    # something new sessions from here on run into.
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class StartSessionResponse(SessionRecord):
    # Set only for a trial account: remaining minutes and this session's
    # limit, so the frontend can show the counter without a second request.
    trial: dict | None = None


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
    trial: dict | None = None  # updated trial status after this session's time was charged


class AvatarTokenRequest(BaseModel):
    persona_id: str


class AvatarTokenResponse(BaseModel):
    session_token: str
