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
are skipped without it), and `CONSULTCASTAI_ALLOWED_ORIGINS` (the frontend's
Render URL).

Verification emails: Resend's shared test sender (the default) only delivers
to your own Resend account's address. For real users, verify a sending domain
in Resend and set `CONSULTCASTAI_EMAIL_FROM`, e.g.
`ConsultCastAI <noreply@ai-curator.ai>`.

Not built yet: password reset (do this next), rate limiting on login/signup
(add before this goes properly public), and a way to resend a verification
email. There's also no UI to make a user an admin, `is_admin` is a column
on `users` you'd flip directly.

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
