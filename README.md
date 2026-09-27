# ConsultCastAI

AI-powered practice partner for AI consultants: rehearse objection handling
against skeptical SMB owners across industries before the real sales or
discovery call happens.

**AI Curator LLC**, built on the pattern from the original ConsultCastAI
Founding Document (Claude for conversation, live voice + avatar for
delivery), rebuilt from scratch here with fresh content and a lighter
auth/storage model suited to a solo-founder product rather than an
enterprise client deployment.

## Stack

- **Claude** (`claude_client.py`) — the sole conversation engine, both the
  in-character persona replies and the end-of-session debrief.
- **Anam** (`anam_client.py`) — live avatar (face + voice) only. Anam's own
  LLM is disabled (`llmId: "CUSTOMER_CLIENT_V1"`), Claude drives every word.
- **FastAPI** (`main.py`) — session lifecycle: start, turn, end.
- **Rule-based coaching** (`coaching.py`) — instant, local, no API round
  trip. Tracks Objection Pressure, Trust, Specificity per message.
- **Storage** (`store.py`) — local JSON file by default, swappable to
  Firestore with one env var when this needs to run on real infra.
- **Accounts + auth** (`users.py`, `auth.py`, `emailer.py`) — email/password
  signup and login, bcrypt-hashed passwords, stateless JWT session tokens
  (30 days), soft (never blocking) email verification via Resend. Local
  SQLite by default, Postgres supported. Swappable to real SSO later without
  touching any route, every route depends on `AuthUser`, not on how it was made.

## Content model

Six SMB personas (`content.py`), each anchored to a different industry and
a different core objection type: cost/ROI, "we already use ChatGPT," staff
job-loss fear, data privacy, staff adoption resistance, trust/accuracy.
Two scenarios per persona to start. Add more by extending `PERSONAS` and
`SCENARIOS`, nothing else needs to change.

## Running it locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here      # never commit this
export CONSULTCASTAI_LOCAL_STORE=1              # local JSON file, no cloud needed
uvicorn main:app --reload --port 8080
```

Open `frontend/index.html` directly in a browser (or serve the `frontend/`
folder statically), point the API base at `http://localhost:8080`. In
local-store mode, auth is bypassed (dev user), so there's no login screen
for local testing. To test the real login flow locally, start the server with
`CONSULTCASTAI_DEV_AUTH_BYPASS=0`; with no `RESEND_API_KEY` set, the
verification link is printed to the server log instead of emailed.

Endpoints:

- `GET /personas` — catalog for the frontend
- `POST /sessions` — start a session
- `POST /sessions/{id}/turn` — send a consultant message, get persona reply + coaching
- `POST /sessions/{id}/end` — end session, get debrief
- `POST /avatar/session-token` — mint an Anam session token (requires `ANAM_API_KEY` and a persona with `avatar_id`/`voice_id` set)

## Hosting

No dedicated domain purchased yet, and none needed to get running. Plan is
to host this as a page/subpath on **ai-curator.ai** for now (e.g.
`ai-curator.ai/consultcastai`), not `consultcastai.com` or similar. If that
changes later:

- `index.html` has no hardcoded domain assumptions, it just points at
  whatever `API_BASE` you type into the connect field (saved to
  `localStorage`), so it can be dropped into any static host or subpath
  as-is.
- The backend's CORS only needs to know the *frontend's* origin
  (`CONSULTCASTAI_ALLOWED_ORIGINS`), which will be `https://www.ai-curator.ai`
  while it lives there, not a ConsultCastAI-specific domain.

`consultcastai.com`, `.ai`, and `.io` were all open as of this check, worth
grabbing later if this becomes its own destination rather than a page on the
main site, domain squatting risk is low right now but not zero.

## Going from local to a real deployment

| Layer | Local default | Production |
|---|---|---|
| Storage | JSON file (`CONSULTCASTAI_LOCAL_STORE=1`) | Either stay on local-JSON with a persistent disk (see Render below), or set `CONSULTCASTAI_LOCAL_STORE=0` + `CONSULTCASTAI_FIRESTORE_PROJECT` for Firestore if this ever needs multiple instances |
| Auth | Dev bypass | Set `CONSULTCASTAI_JWT_SECRET` (32+ random chars) and `CONSULTCASTAI_DEV_AUTH_BYPASS=0` (the bypass otherwise defaults on whenever storage is local, see `auth.py`). Optional `RESEND_API_KEY` for verification emails |
| Accounts | Local SQLite (`users_local.db`) | Same SQLite file on a persistent disk (`CONSULTCASTAI_USERS_DB_PATH`), or Postgres: `CONSULTCASTAI_LOCAL_USERS=0` + `DATABASE_URL` + uncomment `psycopg2-binary` in `requirements.txt`. Avoid Render's free Postgres tier, it expires after 30 days |
| CORS | Open to localhost | Set `CONSULTCASTAI_ENV=production` + `CONSULTCASTAI_ALLOWED_ORIGINS` |
| Secrets | Env vars | Move `ANTHROPIC_API_KEY` / `ANAM_API_KEY` to a real secrets manager, or at minimum set them as Render's (non-synced) environment variables, never commit them |
| Avatar | Voice-only (browser TTS/STT) fallback works with zero Anam setup | Publish personas in Anam Lab, set `avatar_id`/`voice_id`/`avatar_model` per persona in `content.py` |

## Deploying to Render

`render.yaml` in the repo root is a Render Blueprint for both services:
`consultcastai-api` (Python web service; sessions and user accounts both on a
small persistent disk at `/data`; real auth enforced with
`CONSULTCASTAI_DEV_AUTH_BYPASS=0`) and `consultcastai-frontend` (a static
site publishing only the `frontend/` folder, kept separate so backend
source can't be exposed alongside it). No GCP/Firestore involved, this stays
entirely on Render.

To deploy: in the Render dashboard, New -> Blueprint, connect this repo.
Render will read `render.yaml` and prompt for the env vars marked
`sync: false` (never committed): `ANTHROPIC_API_KEY`, `ANAM_API_KEY`,
`CONSULTCASTAI_JWT_SECRET` (generate once, 32+ random characters; changing
it later logs everyone out), `RESEND_API_KEY` (optional, verification emails
are skipped without it), `CONSULTCASTAI_ALLOWED_ORIGINS` (the frontend's
Render URL), and `CONSULTCASTAI_ADMIN_EMAIL` (**critical, see Access
approval below** — set this to your own login email or you will lock
yourself out of your own account).

Verification emails: Resend's shared test sender (the default) only delivers
to your own Resend account's address. For real users, verify a sending domain
in Resend and set `CONSULTCASTAI_EMAIL_FROM`, e.g.
`ConsultCastAI <noreply@ai-curator.ai>`.

Password reset: "Forgot password?" on the login screen emails a link that
opens the app on a "choose a new password" screen. Reset tokens are stored
only as a hash, work once, and expire after 1 hour; requests to the same
account are limited to one per minute; the forgot form answers identically
whether or not the email has an account. A successful reset signs you in,
marks the email verified (the link proves the inbox), and bumps the
account's `token_version`, which invalidates every login token issued
before it (each request is checked against the account, so a stolen session
doesn't survive a password change). `users.py` adds the reset columns to an
existing database automatically on startup, no manual migration needed.

A "resend email" link on the verification banner (and in Profile) re-sends
the same verification link, for when the original never arrived.

Access approval: signing up creates an account but doesn't let it use
anything functional (`/sessions`, `/turn`, `/end`, the avatar, session
history, profile edits) until an admin approves it — see `auth.require_approved`
in `main.py`. Login/signup still succeed for a pending account (so the
frontend can show a clear "pending approval" screen instead of a confusing
auth failure), just gated everywhere else.

**`CONSULTCASTAI_ADMIN_EMAIL` is critical, read this before deploying.**
The `approved` column defaults to `false`, including on rows that already
existed before this shipped — same class of mistake as the missing
`CONSULTCASTAI_JWT_SECRET` incident, if you don't set this you lock
yourself out of your own account the moment it ships. Set it to your own
login email: that account is automatically promoted to admin + approved on
startup (covers an existing account) and again right after signup (covers
a fresh one), regardless of whether `is_admin` was ever set on it before.
It's also where "someone signed up" notification emails go (best-effort,
optional — nothing breaks if it's unset or a send fails, the real answer
is always `GET /auth/pending`, and the app's Profile menu -> Pending
Requests when you're logged in as that admin).

Rate limiting on the auth endpoints (`slowapi`, in-memory — no Redis needed
at this scale, a single Render instance): `/auth/login` 5/minute,
`/auth/signup` 3/hour, `/auth/resend-verification` 3/hour,
`/auth/forgot-password` 3/hour. Keyed off the real visitor IP, not
`request.client.host` — Render sits behind a reverse proxy, so that's the
proxy's own address for every request, and the limiter reads the first
`X-Forwarded-For` entry instead. Get that wrong and either every visitor
shares one "IP" (the limiter blocks all your users at once after a handful
of legitimate logins) or it silently limits nothing at all. This is
IP-based only, not account-based — a real, further layer worth adding
eventually if targeted account lockout ever becomes necessary, not required
for this first pass — and there's no CAPTCHA/bot-detection yet either,
fine for launch, worth revisiting if abuse shows up.

Not built yet: account-level lockout and CAPTCHA/bot-detection (see above),
and a way to deny/reject a pending request rather than just leaving it
pending or deleting the row directly.

## Billing (Stripe subscriptions)

Real recurring billing, not a fixed-term one-time charge: ConsultCastAI Pro
is $129/month or $1,316/year (one account, 20 sessions/month), MRR/ARR are
real numbers. Team is $599/month or $6,110/year: up to 5 accounts sharing
one subscription and one pooled 100-session/month cap, with one owner who
invites the rest — see `teams.py`.

Flow: `POST /billing/create-checkout-session` (any signed-in account, even
a still-pending one — subscribing is one of the two ways an account becomes
approved, alongside manual admin approval) opens a Stripe-hosted Checkout
Session in `mode="subscription"` and returns its `checkout_url` to redirect
the browser to. Stripe calls `POST /billing/webhook` server-to-server,
verified by signature (never trust an unverified body), handling the full
subscription lifecycle: `checkout.session.completed` (created, approves the
account), `invoice.payment_succeeded` (renewed — also how a `past_due`
subscription recovers), `customer.subscription.deleted` (canceled, once the
already-paid-for period actually ends, not immediately), and
`invoice.payment_failed` (`past_due`; Stripe's own smart retries handle
dunning, no custom retry code here). `auth.require_active_plan` (stacked on
`require_approved`) checks `subscription_status == "active"` and
`current_period_end` plus a 20-session/month cap on `POST /sessions`
specifically — the one action that costs money per use; continuing an
already-started session doesn't check this again. Admins bypass entirely,
same convention as `require_owner`'s admin bypass elsewhere in `auth.py` —
otherwise the account that bootstrapped the approval system would itself
have no subscription and be locked out by its own gate.

Cancellation is self-service through Stripe's own Customer Portal
(`POST /billing/portal-session` -> redirect to `portal_url`), not a custom
in-app flow — the portal already handles proration and other edge cases
correctly. The Profile menu's "Manage subscription" link is the only
frontend piece this needs.

**Compliance**: the plan-selection screen discloses that it renews
automatically and how to cancel (the actual substance of "click to cancel"
rules, not boilerplate) — see the pending-approval screen's copy.

Required env vars (all `sync: false` in `render.yaml`, fail closed if
unset — nobody gets free access from a missing key): `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET` (from adding an endpoint in the Stripe dashboard for
`{backend}/billing/webhook`, listening for the four events above),
`STRIPE_PRICE_PRO_MONTHLY`, `STRIPE_PRICE_PRO_ANNUAL`,
`STRIPE_PRICE_TEAM_MONTHLY`, `STRIPE_PRICE_TEAM_ANNUAL` (the Price IDs from
each product's two Prices, already created in Stripe).

### Team tier

A `team` (its own table, `teams.py`) groups up to `seat_limit` (5) user
accounts under one subscription and one pooled monthly cap — the owner's
own account counts as one seat, not tracked separately. `checkout.session.completed`
branches on the plan prefix (`plan.split("_")[0]`): `"team"` creates the
`teams` row and sets the owner's `users.team_id`, same as `"pro"` sets the
individual fields, both then call `set_approved`. The renewal/cancellation/
failure events look up whether a `stripe_subscription_id` belongs to a team
or an individual account and update the right table — a team's Stripe
customer and subscription live on the `teams` row, never on any member's
own `users` row.

`auth.require_active_plan` branches on `account.team_id`: set means check
the team's `subscription_status`/`current_period_end` and pool usage under
`store.get_team_usage(team_id, ...)` instead of the member's own id (same
storage shape as individual usage, pooling is just using the team's id as
the key) — a member has no subscription of their own at all, they ride
entirely on the team's.

`POST /team/invite` (owner-only, checked by actually owning the team, a
separate concept from `is_admin`) creates a single-use invite token and
emails a link (`emailer.send_team_invite_email`). Accepting one is just
`POST /auth/signup` with `invite_token` set: the invited person never sees
Checkout or pays individually, they join already-approved. Two checks
beyond the original spec, closing real gaps found while building this: an
invite can't be redeemed twice (`teams.TeamInvite.used`), and seat capacity
is re-checked at acceptance, not just at invite time (several invites can
be outstanding at once). `GET /team/mine` (also owner-only) backs the
Profile menu's "Team" screen — members, pooled usage, an invite form.

`POST /billing/portal-session` is team-aware too: an owner's Stripe
customer lives on the `teams` row, not their own `users` row, so they
resolve through their team; a regular member has no billing role at all
and gets a 403 rather than the owner's payment method and cancel button
(a gap in the original portal-session spec, which predates Team). Canceling
as an owner ends access for every member at `current_period_end`, not just
the owner — they were never individually paying customers. The frontend
surfaces this plainly in the Team screen before anyone clicks cancel.

Not built yet: removing/replacing a teammate mid-cycle, per-seat add-ons
beyond 5, transferring team ownership, proration on plan changes (the
Customer Portal handles the basic case), custom dunning logic beyond
Stripe's built-in retries — all deliberately deferred, not oversights.

## Voice, current state

The frontend uses the browser's built-in Web Speech API (`SpeechRecognition`
for mic input, `speechSynthesis` for the persona's voice) so the whole thing
works end-to-end with zero avatar vendor setup. Wiring in Anam's live avatar
is the next step once personas are published in Anam Lab: call
`POST /avatar/session-token`, feed the token to Anam's client SDK, and send
each `persona_reply` to Anam's `talk()` instead of (or alongside)
`speechSynthesis`.

## Ownership note

This is a from-scratch build for AI Curator LLC. It follows the same
architectural pattern (Claude + live avatar, rule-based live coaching, a
persona/scenario content model) documented in the original ConsultCastAI
Founding Document, but all content, prompts, and code here are written
fresh for this product, not reused from any client engagement.
