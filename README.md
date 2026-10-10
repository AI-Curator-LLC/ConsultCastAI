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

Access: signing up creates an account but doesn't let it use anything
functional (`/sessions`, `/turn`, `/end`, the avatar, session history,
profile edits) until it's approved, see `auth.require_approved`. Approval
is automatic. Verifying the email approves the account
(`users.mark_verified`); so does a password reset (it proves the same
inbox), subscribing, accepting a team invite, and being the bootstrap
admin. There is no manual approval step. Until then login still succeeds
and the frontend shows a "Verify your email" screen with a resend button;
it re-checks every few seconds, so clicking the link in another tab or on a
phone lets the waiting one in without a refresh. Accounts that had already
verified and were waiting on an admin when this changed are approved at
startup; the rest are approved when they verify.

`POST /auth/approve/{id}` (admin) is kept as an override for when a
verification email never arrives. It does what clicking the link would
have: verifies and approves.

**Verification email delivery is now what lets people in.** If Resend isn't
sending to arbitrary addresses (see emailer.py's note on the shared
`onboarding@resend.dev` sender, which only delivers to the Resend account
owner), nobody can verify and nobody gets access except through the manual
override. Check `RESEND_API_KEY` and `CONSULTCASTAI_EMAIL_FROM` on a
verified sending domain.

Suspension: an admin can suspend any non-admin account from Profile ->
Accounts (`GET /admin/accounts`, `POST /admin/accounts/{id}/suspend` and
`/unsuspend`). It's a separate flag from approval and is checked first, so
verifying or subscribing can't lift it, and checkout is refused for a
suspended account. A suspended account can still log in, export its data
and delete itself. Deleting it leaves a keyed hash of the email in
`suspended_emails` (same hashing as `trial_records`), and a new account on
that email starts suspended; unsuspending it clears the hash. Suspending
doesn't cancel a Stripe subscription.

Signup guards:
- Disposable email domains are refused (`disposable.py`).
  `disposable_email_domains.txt` is an unmodified copy of the
  community-maintained blocklist from
  github.com/disposable-email-domains/disposable-email-domains (about 9,200
  domains, CC0); `disposable_email_domains_extra.txt` holds this project's
  own additions. The upstream list changes constantly, so the copy goes
  stale: to refresh it, replace the file with the current
  `disposable_email_blocklist.conf` and redeploy.
  `CONSULTCASTAI_BLOCKED_EMAIL_DOMAINS` adds domains and
  `CONSULTCASTAI_ALLOWED_EMAIL_DOMAINS` lets one through, both
  comma-separated, no deploy needed. Forwarding aliases that reach a
  real inbox (SimpleLogin, Hide My Email and similar) are deliberately not
  blocked. Team invites to a disposable address are refused too.
- `SIGNUPS_PER_IP_PER_DAY` (3) accounts per IP per rolling 24 hours
  (`users.record_signup`). Counts accounts actually created, not attempts,
  so a typo doesn't use it up. Kept in the database (`signup_events`, IP
  stored as a keyed hash), so a restart or redeploy doesn't reset it.
  Team-invite signups are exempt: a team onboarding from one office shares
  an address.

**`CONSULTCASTAI_ADMIN_EMAIL` is critical, read this before deploying.**
Set it to your own login email: that account is automatically promoted to
admin + approved on startup (covers an existing account) and again right
after signup (covers a fresh one), regardless of whether `is_admin` was
ever set on it before. Without an admin there is no accounts view and no
way to suspend anyone. It's also where "someone signed up" notification
emails go. Those are informational (best-effort, nothing breaks if it's
unset or a send fails); the real list is always Profile -> Accounts.

Rate limiting on the auth endpoints (`slowapi`, in-memory, no Redis needed
at this scale, a single Render instance): `/auth/login` 5/minute,
`/auth/signup` 10/hour (every attempt, on top of the accounts-per-day limit
above), `/auth/resend-verification` 3/hour, `/auth/forgot-password` 3/hour.

All per-IP limits key on `CF-Connecting-IP` (`get_real_ip` in main.py).
Not `request.client.host`, and not `X-Forwarded-For`: its first entry is
whatever the client sent. A request with a made-up `X-Forwarded-For`
reached the app with that value as the client address, which let anyone
reset their own limit on every request (checked against the live deploy).
Cloudflare, which fronts every Render service, sets `CF-Connecting-IP`
itself and rejects a request that tries to supply one. If this app ever
moves somewhere not behind Cloudflare, that function has to change with it.

This is IP-based only, not account-based, and there's no CAPTCHA or
bot-detection: worth revisiting if abuse shows up.

Not built yet: account-level lockout and CAPTCHA/bot-detection (see above).

## Billing (Stripe subscriptions)

Real recurring billing, not a fixed-term one-time charge: ConsultCastAI Pro
is $129/month or $1,316/year (one account, 120 practice minutes a month),
MRR/ARR are real numbers. Team is $599/month or $6,110/year: up to 5
accounts sharing one subscription and one pool of 1,000 practice minutes a
month, with one owner who invites the rest — see `teams.py`. Minutes are
the only limit on a paid plan (see "Paid minutes and top-ups" below); there
is no limit on the number of sessions.

Flow: `POST /billing/create-checkout-session` (any signed-in account, even
an unverified one — subscribing approves an account too, alongside
verifying the email; a suspended account is refused) opens a Stripe-hosted Checkout
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
`current_period_end`, and that the plan has practice minutes left, on
`POST /sessions` specifically — the one action that costs money per use; continuing an
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
rules, not boilerplate) — see the upgrade prompt's copy.

Required env vars (all `sync: false` in `render.yaml`, fail closed if
unset — nobody gets free access from a missing key): `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET` (from adding an endpoint in the Stripe dashboard for
`{backend}/billing/webhook`, listening for the four events above),
`STRIPE_PRICE_PRO_MONTHLY`, `STRIPE_PRICE_PRO_ANNUAL`,
`STRIPE_PRICE_TEAM_MONTHLY`, `STRIPE_PRICE_TEAM_ANNUAL` (the Price IDs from
each product's two Prices, already created in Stripe).

### Team tier

A `team` (its own table, `teams.py`) groups up to `seat_limit` (5) user
accounts under one subscription and one shared pool of monthly minutes — the owner's
own account counts as one seat, not tracked separately. `checkout.session.completed`
branches on the plan prefix (`plan.split("_")[0]`): `"team"` creates the
`teams` row and sets the owner's `users.team_id`, same as `"pro"` sets the
individual fields, both then call `set_approved`. The renewal/cancellation/
failure events look up whether a `stripe_subscription_id` belongs to a team
or an individual account and update the right table — a team's Stripe
customer and subscription live on the `teams` row, never on any member's
own `users` row.

`auth.require_active_plan` branches on `account.team_id`: set means check
the team's `subscription_status`/`current_period_end`, and the team's
minutes (kept under the team's id rather than the member's own: same
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
be outstanding at once). `GET /team/mine` backs the Profile menu's "Team"
screen for everyone on the team: the team's practice minutes (used and left
this month, extra minutes, when the month's minutes reset). The owner also
gets every member with the minutes each has used this month, an "Add 100
minutes for $40" button and the invite form; a member gets the same team
figures and only their own row.

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

## Data retention, deletion, and export

Gives users control over their data and puts a real, enforced expiry on
stored sessions.

| Data | Rule |
|---|---|
| Full transcript (`conversation`) | Purged `TRANSCRIPT_RETENTION_DAYS` (90) after the session was created |
| Debrief, scores, session metadata | Kept `DEBRIEF_RETENTION_DAYS` (365) total, then the whole record is deleted |
| Sessions with no debrief (abandoned) | Whole record deleted at `TRANSCRIPT_RETENTION_DAYS`, nothing worth keeping |
| After cancellation | Kept `POST_CANCEL_GRACE_DAYS` (90) past `current_period_end`, then that user's (or team member's) sessions are deleted |
| User deletes a session, or all sessions | Deleted immediately |
| User deletes their account | Account, sessions, and usage records deleted immediately. One thing is kept: if the account used any trial time, a keyed hash of its email, so the same address can't get a second trial (see "Trial and minutes tracking") |
| Billing records | Stripe holds them — we keep only customer id, status, and dates |

All three periods are env vars (`retention.py`), defaulted to the values
above and declared in `render.yaml` so they're one edit away, no code
change needed.

`retention.py` runs as a background thread started at app startup (see
main.py's `startup` event), not a Render Cron Job — a Cron Job is a
separate service/container and can't reach this service's persistent disk
(a Render Disk mounts to exactly one service). Runs once immediately, then
every 24 hours; a single Render instance means it never double-runs, and
`run_once()` is written to be idempotent anyway. Each pass:
1. Backfills `created_at` on any record that predates the field (stamped
   with "now", skipped this run — never treated as "very old" and deleted).
2. Purges the transcript (empties `conversation`, sets
   `transcript_purged = true`, keeps the debrief) on anything past
   `TRANSCRIPT_RETENTION_DAYS` that still has a debrief; deletes outright
   anything past `TRANSCRIPT_RETENTION_DAYS` with no debrief, or past
   `DEBRIEF_RETENTION_DAYS` regardless.
3. Deletes every session for a rep (or team member) whose subscription (or
   team's) reads `canceled` with `current_period_end` more than
   `POST_CANCEL_GRACE_DAYS` in the past.
4. Logs only counts ("purged 12 transcripts, deleted 3 sessions") — never
   session content or message text.

Concurrency: `store.py`'s local-file writes (`save`, `delete`,
`delete_for_rep`, `purge_transcript`, `backfill_created_at`) all go through
the same lock and now write atomically (temp file + rename), so the job
rewriting the file on a timer can't collide with a `/turn` request saving
mid-session, and a crash mid-write can never leave a truncated file. Every
mutation also re-checks its own condition (still has a debrief, isn't
already purged, key is still missing) right before writing, under the
lock, so a session that's still active by the time the job actually gets
to it is never touched even if it looked eligible when first scanned.

User controls, all gated on `auth.verify_user` only — deliberately not
`require_approved` or `require_active_plan`, since a lapsed, canceled, or
unverified, or suspended account must be able to reach these regardless
(the Profile screen isn't otherwise reachable behind the verify-your-email
and suspended screens, so each has an "Account settings" link straight to
it):

- `DELETE /sessions/{id}` — owner-only (`auth.require_owner`, same check
  `/turn` and `/end` already use: 404 if the id doesn't exist, 403 if it's
  someone else's).
- `DELETE /me/sessions` — deletes every one of the caller's own sessions.
  Never touches `usage_records` — that's account deletion's job below, not
  plain session deletion, which must not let anyone claw back part of
  their monthly minutes by deleting sessions.
- `GET /me/export` — profile fields, plan status, and every one of the
  caller's sessions (metadata, debrief, and the transcript unless already
  purged), filtered by `rep_id` the same way `/sessions/mine` is.
- `POST /me/delete-account` (body: `{"password": "..."}`) — requires the
  current password (same check as change-password), then deletes the
  account row, all its sessions, and its usage records. Refused while a
  subscription reads active: an individual account checks its own, a team
  owner checks the team's (a regular member is always allowed to leave,
  which just frees their seat — they were never the one being billed). The
  Stripe customer record itself is never deleted; Stripe keeps billing
  history for tax purposes independent of this account existing. The
  `trial_records` row for the email, if there is one, is kept too, with its
  link to the account removed.

Frontend: Debrief History gets a Download button (writes the debrief to a
`.txt` file client-side, no extra endpoint) and a Delete button per
session, plus a "Delete all sessions" button. Profile gets "Export my
data" (downloads the `/me/export` JSON) and a Danger Zone requiring the
current password and typing `DELETE` to confirm — hidden entirely in local
dev-bypass mode, which has no real password to check.

Server logs: Render controls retention on its side — don't promise a
specific window in a Privacy Policy until that's actually been checked for
the plan in use. Confirmed no log line prints session content or message
text; current error logs print exception types only.

Backups: check whether Render keeps persistent-disk snapshots and for how
long before stating anything about it publicly — deleted data can outlive
this job in a snapshot until that expires.

Not built yet: a manual "run the cleanup job now" trigger (it runs on its
own schedule, no admin control surface for it), and the actual production
verification this needs before any of the above gets restated as a Privacy
Policy promise — see the next paragraph.

**Before publishing any retention/deletion wording externally**: confirm
the job has actually run against production data at least once, generated
`created_at` values behave as expected for anything pre-dating the field
you added, and Render's real log/snapshot retention for backups have
actually been checked, not assumed.

## Trial and minutes tracking

An approved account that has never subscribed is a trial account
(`minutes.is_trial`): not an admin, not a team member, and not a former
subscriber, who gets the "subscribe" message rather than a second trial.
Approval is automatic once the email is verified, so in practice a trial is
what every new account gets as soon as it verifies, until it subscribes.

| Rule | Value |
|---|---|
| Trial practice time in total | `TRIAL_TOTAL_MINUTES` (10) |
| Longest single trial session | `TRIAL_SESSION_MINUTES` (10), or whatever is left of the trial if that's less |
| Warning before a trial session ends | `TRIAL_WARNING_SECONDS` (60) |
| Trial counts as used up | less than a minute left (the counter reads in whole minutes) |
| Email verification | required before any account's first session |
| Paid plans | a monthly allowance of minutes, see "Paid minutes and top-ups" below; tracked per user, and per team for team members |

All of it is env vars in `minutes.py`, declared in `render.yaml`.

**Where the time comes from.** The server's own clock, never the browser.
Each session record carries its clock (`run_since`, `run_sec`,
`paused_at`, `ended_at`): it starts when the server creates the session,
stops on `POST /sessions/{id}/pause` or `/end`, restarts on `/resume`. Every
`/turn` settles what has run so far into the ledger (`minutes.settle`), so a
session that is never formally ended (tab closed, browser crashed) has
still been charged up to its last exchange. A paused session refuses turns,
so pausing can't be used to practice off the clock.

**Where it's kept.** In the usage store (`store.add_seconds`,
`seconds_used` on the same per-owner, per-month record as `session_count`),
keyed by user id, and also by team id for a team member. Not on the
session, so deleting a session, or the retention job deleting it later,
doesn't give the time back. Clearing the browser changes nothing: the
counter is just `/auth/me`'s `trial` field. Only deleting the whole account
removes its ledger rows.

**Enforcement.** `auth.require_active_plan` refuses a new session with a
402 `trial_exhausted` once the trial is used up, and a 403
`email_unverified` if the account has no sessions yet and hasn't verified
(accounts that were already practicing before this shipped are exempt, so
nobody is locked out by it). `/turn` refuses with a 402
`trial_session_limit` once a trial session is past its limit plus
`TRIAL_TURN_GRACE_SECONDS`, before any Claude call. Time past a session's
limit is never charged, so a slow request or a tab left open can't cost a
trial more than the session's limit.

**Frontend.** The header shows "8 of 10 trial minutes left" on the start
screen and through the session, counting down live; a pinned copy appears
while the header is scrolled out of view mid-session. A trial session warns
at one minute left and ends itself at the limit through the same path as
the End button, so it finishes with a normal debrief. When the trial is
used up, Start is disabled and an upgrade prompt offers Pro checkout.
`/sessions`, `/end`, and `/pause` all return the updated `trial` status, so
the counter never needs a second request.

Session duration (`duration_sec`) now comes from the same persisted clock
rather than an in-memory start time, so a restart or redeploy mid-session
no longer zeroes it.

## Paid minutes and top-ups

Paid plans include practice minutes each calendar month (UTC), counted from
the same ledger as the trial. Nothing about the trial changed.

| Rule | Value |
|---|---|
| Pro, minutes a month | `PRO_MONTHLY_MINUTES` (120), for the one account |
| Team, minutes a month | `TEAM_MONTHLY_MINUTES` (1,000), shared by the team: one pool for the owner and every member |
| Pro through the suite's plan | the Pro allowance (`SUITE_PLAN_GRANTS_PRO`, on) |
| One block of extra minutes | `TOPUP_BLOCK_MINUTES` (100), a one-time payment at the Stripe Price in `STRIPE_PRICE_ID_TOPUP`; the button reads `TOPUP_PRICE_LABEL` ("$40") |
| Warning before a paid session ends | `PAID_WARNING_SECONDS` (60) |
| Minutes count as used up | less than `PAID_MIN_START_SECONDS` (60) left |
| The switch | `PAID_MINUTES_ENFORCED` (on). Off: paid plans are recorded, never limited, and show no counter |
| Admin accounts | not limited, as before |

All of it is env vars read in `minutes.py`, declared in `render.yaml`.

**Who is on which plan** (`minutes.paid_plan`). A team member, the owner
included, draws on the team's pool, kept under the team's id. An account
with its own active subscription is Pro, with its own pool. An account
whose person holds the AI Curator Consulting Suite plan is Pro too
(`minutes.suite_pro`): the suite says what the plan includes at every
sign-in and at every recheck of a sign-in, `users.note_suite_plan` records
whether that includes the Suite plan and when it was said, and the answer
stops counting after `SUITE_PLAN_MAX_AGE_DAYS` (8) without a fresh one.
Only an address the suite has confirmed counts. Before this, the suite's
plan gave nothing here: such a person was an ordinary trial account.

**The month's figure.** The ledger was already one record per owner per
calendar month (`usage_records`, key `<owner id>:<YYYY-MM>`, field
`seconds_used`), written for the user and, for a team member, for the
team. The month's use for a pool is that record for the current month,
read as it is: no new storage, no migration, and every record written
before this change means what it always meant. A new month simply has no
record yet, which is how the allowance comes back.

**Extra minutes (top-ups).** `POST /billing/topup-checkout-session` opens
Stripe Checkout in payment mode for one block. A Pro account buys for
itself; on a team only the owner can buy, for the whole team (a member
gets "Ask your team owner to add minutes"). With `STRIPE_PRICE_ID_TOPUP`
unset there is no button and the endpoint answers 400 `topup_unavailable`;
nothing else is affected.

The block is credited by `/billing/webhook` when Stripe confirms the
payment (`checkout.session.completed` with `payment_status: paid`, or
`checkout.session.async_payment_succeeded` for a payment that settles
later), never by the browser coming back. Crediting is one INSERT into
`topup_purchases` (in the users database, `topups.py`) whose primary key
is Stripe's id for that Checkout: a webhook that is retried or replayed
hits the key and adds nothing. The balance is not stored: it is everything
bought (the sum of those rows) minus everything used
(`store.get_topup_used`, one `<owner id>:topup` record in the usage store).

Extra minutes are used only after the month's minutes are gone
(`minutes.settle` takes each charge from the month first and only the
part that doesn't fit from the balance), carry over from month to month,
and can only be used while the subscription is active; the balance stays
on record otherwise. Deleting an account deletes its purchases and its
usage records. A refund in Stripe does not take minutes back: remove the
row from `topup_purchases` by hand if that is wanted.

**Enforcement**, the same shape as the trial's. A paid session's
`time_limit_sec` is whatever the pool had left when it started (the
month's minutes plus extra minutes); there is no per-session cap. The page
warns one minute before and ends the session itself at the limit, with a
normal debrief. `/turn` refuses with a 402 `minutes_session_limit` past
the limit plus `TRIAL_TURN_GRACE_SECONDS`, and also when a team's pool has
been used up by a colleague's session in the meantime. Time past the limit
is never charged. With under a minute left `auth.require_active_plan`
refuses a new session with a 402 `minutes_exhausted`. Minutes are the
only limit on a paid plan: the old monthly session caps (20 for Pro, 100
pooled for Team) are gone. The number of sessions started in a month is
still recorded (`session_count`) but nothing is refused because of it, and
with `PAID_MINUTES_ENFORCED` off a paid plan is not limited at all.

**Frontend.** `/auth/me`, `/sessions`, `/end`, `/pause` and `/resume`
carry `paid_minutes` next to `trial` (`minutes.paid_status`). The header
counter reads "84 of 120 minutes left this month" ("... of 1,000 team
minutes left this month" on a team), with "+ 100 extra" when there is a
balance. When the minutes are used, a panel says so, says when they reset
(the first day of next month, UTC) and offers "Add 100 minutes for $40" to
a Pro account or a team owner; the account menu has "Add minutes" for the
same people. The Team screen (account menu, "Team") shows the same
figures for the team, with the button for the owner. Coming back from Stripe (`?payment=topup_success`) the page
re-reads `/auth/me` until the new balance shows. A live avatar session on
any plan also has the avatar session limit (10 minutes, see "Cost controls
for live avatar sessions"): whichever limit comes first applies.

**Switching top-ups on.** In Stripe: create a product "100 extra practice
minutes" with a one-time Price of $40, and add
`checkout.session.async_payment_succeeded` to the events the existing
webhook endpoint listens for. In Render: set `STRIPE_PRICE_ID_TOPUP` on
`consultcastai-api` to that Price's id. To turn the paid cap off without a
code change, set `PAID_MINUTES_ENFORCED` to `0` there.

Tests: `python tests/test_minutes.py`.

**One trial per email.** The first time a trial account is charged any
session time, `users.record_trial_used` writes a row to `trial_records`: a
keyed hash (HMAC-SHA256) of the normalized email, the account id, and the
date. Never the email itself. Deleting the account keeps the row and nulls
its account id, so what survives is a hash and a date. A new account whose
email hashes to an existing row that isn't its own starts with no trial
minutes (`trial_status` reports `previously_used`), sees the upgrade
prompt, and can still subscribe. The account that used the trial is
unaffected and keeps counting against its own ledger. An account that
never practiced leaves no row, so deleting it and signing up again still
gets a trial. `/me/export` includes `trial_record_created_at`.

The hash is keyed so the table can't be checked against a list of guessed
addresses without the key. The key is `CONSULTCASTAI_TRIAL_HASH_KEY` if
set, otherwise `CONSULTCASTAI_JWT_SECRET`. **Whichever it is has to stay
the same**: change it (including rotating the JWT secret while no separate
key is set) and existing rows stop matching, so those emails would get a
trial again. To rotate the JWT secret safely, first set
`CONSULTCASTAI_TRIAL_HASH_KEY` to the current JWT secret's value.

SQLite connections run with `secure_delete` on, so a deleted account's row
is zeroed in the file rather than left readable in freed pages. That only
covers deletions made after it was turned on, so the database is also
vacuumed once at startup (`users._vacuum_once`, tracked by
`PRAGMA user_version` so it doesn't repeat) to clear rows deleted before
then. If that vacuum fails it's logged and retried on the next start; it
never blocks startup.

To give a trial back to an email by hand, delete its `trial_records` row.

Known limits, deliberately left: a different address (including a `+tag`
alias of the same inbox) is a different email and gets its own trial,
limited by the signup guards (verified email, no disposable domains, 3
accounts per IP per day); two sessions started at the same moment in two tabs
each get their own limit; and time between the last exchange and closing
the tab without ending isn't charged.

## Voice, current state

The frontend uses the browser's built-in Web Speech API (`SpeechRecognition`
for mic input, `speechSynthesis` for the persona's voice) as the zero-setup
fallback for any persona without a published Anam avatar. A persona with
`avatar_id`/`voice_id`/`avatar_model` set in `content.py` (currently
`carla_diaz`, `harold_bennett`, `dr_owens` and `tom_walsh`) gets the live Anam avatar automatically instead
— no manual mode picker, `applyOutputMode()` decides per persona (see
`/personas`' `has_avatar` field).

**Adding an avatar built in Anam's Persona builder.** The builder gives a
persona ID and a voice ID but shows no avatar ID. Don't mint tokens with the
persona ID: `personaConfig.personaId` is accepted by Anam, but it cannot be
combined with `llmId`, so the session runs the persona's own Anam LLM and it
answers alongside Claude. Look the avatar up instead and use the same three
fields as every other persona:

    GET https://api.anam.ai/v1/personas/{persona_id}
    -> avatar.id   -> avatar_id
    -> voice.id    -> voice_id
    -> avatarModel -> avatar_model

### Hands-free avatar sessions

An avatar session is a spoken conversation: no button to press, no text to
review. This follows Anam's client-side custom LLM pattern.

1. The session token is minted with `llmId: "CUSTOMER_CLIENT_V1"`
   (`anam_client.py`), which turns Anam's own model off. Anam runs the
   microphone, the speech recognition, the voice and the face; what the
   persona says is decided here.
2. The microphone is Anam's for as long as the connection is up
   (`createClient` with input audio left on).
3. `AnamEvent.MESSAGE_HISTORY_UPDATED` fires when the consultant finishes
   speaking. The browser sends that text to
   `POST /sessions/{id}/turn/stream`, which runs the same checks, builds the
   same persona prompt and history, and makes the same Claude call as
   `/turn` (they share `_begin_turn`), with `"stream": true`.
4. The reply comes back as Server-Sent Events and each piece is passed to
   `anamClient.createTalkMessageStream()` as it arrives, so the avatar is
   speaking after the first few words. The final event carries the scores
   and the coaching note, which update exactly as they do for a typed turn.

Typing is still there as the fallback and goes through the same path (the
avatar answers out loud either way). In an avatar session the text box and
Send are put away behind a small keyboard button, "Type instead", which
brings them back for the rest of the session; they come back by themselves
if the browser refuses the microphone or the microphone stops working
part-way through (`syncComposer`). A voice-only session always shows them. Time limits, the
warning, the clean end, the idle prompt and the debrief are untouched; a
persona without an avatar still uses `/turn` and the browser's own speech
recognition.

Mute switches the microphone track off (`muteInputAudio`), so nothing is
sent to Anam. Pause closes the connection, which releases the microphone and
stops both Anam's billing and this app's clock (`/pause`); Resume opens a new
connection and carries on, since the conversation is held here, not by Anam.
If Anam closes the connection itself, the session pauses the same way and
offers Resume.

**Talking over the persona.** Anam stops the avatar when the consultant
speaks and emits `TALK_STREAM_INTERRUPTED`. The browser then hangs up on the
reply stream; the server notices between pieces, closes the Claude call, and
keeps what had been written, marked as cut off. With its next turn the
browser reports how much the avatar had actually said (from Anam's
transcript of the persona's speech), and the server cuts its record back to
that (`_apply_spoken_reply`), so the next reply and the debrief work from
what was heard rather than what was written. The report can only shorten
the latest reply; text that isn't the start of it is ignored. If nothing was
said at all, the reply is dropped and the two halves of what the consultant
said become one turn. Turns for a session are taken one at a time
(`_TurnGate`), so a turn sent in the same instant the last one is cut off
can't be recorded out of order.

**What Anam charges for, and its limits** (from Anam's pricing and billing
pages, checked October 2026; confirm against your own plan):
- Billing is by the second of connected session time, at the plan's rate.
  Using your own LLM doesn't change Anam's price; Claude is billed
  separately by Anthropic, as before.
- Silence, a muted microphone and an idle avatar are all still billed. Only
  closing the connection stops it, which is why Pause and the idle
  disconnect close it rather than mute it.
- Each plan has a maximum session length (3 minutes on Free, 5 on Starter,
  10 on Explorer, 2 hours on Growth and Professional) and a number of
  simultaneous sessions (1, 1, 3, 5, 10). This deploy is on Explorer: 10
  minutes, 3 at a time. Avatar sessions here end themselves at
  `AVATAR_SESSION_MINUTES` (9.5, under Anam's 10) so Anam's cut-off is never reached; see
  "Cost controls for live avatar sessions". Past the concurrency limit,
  starting an avatar fails and the session falls back to voice.
- Streaming means a reply that's interrupted stops being generated, so
  hands-free costs slightly less in Claude output than generating every
  reply in full.

Tests: `python tests/test_handsfree.py` (throwaway files, the AI replaced by
stand-ins; the last two sections start a local server to show what happens
when the browser hangs up mid-reply).

### Cost controls for live avatar sessions

Anam bills by the connected minute, so a live avatar session carries a real
per-minute cost a voice-only one doesn't — both controls below are gated on
`outputMode === 'avatar'` for exactly that reason (see `checkCostControls()`
in `frontend/index.html`), and both are wall-clock (`Date.now()`) based, not
a decrementing counter, so a tab backgrounded or a computer put to sleep
mid-session self-corrects the instant it's next checked rather than losing
track of real elapsed time. A `visibilitychange` listener forces that check
immediately on wake, rather than waiting on a `setInterval` tick the browser
may have throttled or suspended entirely while hidden.

- **Avatar session limit** (`AVATAR_SESSION_MINUTES`, 9.5): ends an avatar
  session automatically through the exact same path as clicking End
  yourself, so it finishes with a normal debrief, with a one-time "session
  ending soon" warning `AVATAR_WARNING_SECONDS` (60) before: at 8:30 and
  9:30 by default. It applies on every plan, to avatar sessions only; a
  voice-only session has no such limit. It exists because Anam closes a
  connection at its plan's maximum session length (10 minutes on Explorer),
  mid-sentence and with no debrief; this hangs up cleanly first. It is kept
  under Anam's maximum, never at it: at the same figure the two race and
  Anam sometimes wins (a background tab runs its timers late).
  - **To change it** (after upgrading the Anam plan, say to Growth's 2
    hours, set 119): set `AVATAR_SESSION_MINUTES` on `consultcastai-api`, in
    `render.yaml` or in the Render dashboard under Environment. The service
    restarts and sessions started after that use the new figure. Nothing in
    the frontend changes: the limit and the warning time are sent with each
    session (`avatar_limit` in the response to `POST /sessions`, from
    `minutes.avatar_limit`). Keep it at or below Anam's own maximum.
  - It limits one sitting, not the month. A trial's or a paid plan's
    minutes are counted and enforced as before (`time_limit_sec`), in any
    mode, and whichever limit comes first applies. When both fall at the
    same moment the minutes limit is the one the person is told about.
  - Paused time doesn't count. Each Resume opens a new Anam connection, so
    no single connection is ever older than the session's active time.
  - The avatar connection is closed the moment a session ends, before the
    debrief is requested, not once it has come back: those seconds are
    exactly when Anam would otherwise cut in. If Anam does hang up a moment
    before the limit (a background tab runs its timers late), that is
    treated as the limit: a clean end with the debrief, not "the avatar
    disconnected". If the debrief request itself fails, the session is left
    paused rather than running with no avatar.
  - The browser enforces this limit; the server only supplies the number.
    That's deliberate: its purpose is to beat Anam's cut-off, and a browser
    that ignored it would simply be cut off by Anam instead.
- **Idle disconnect** (`IDLE_TIMEOUT_SEC`, 5 min of no real speech captured
  and no message sent): shows a "still there?" prompt, then disconnects the
  avatar (reusing the manual Pause button's own code path, so Resume works
  the normal way) if there's no reply — the button, actual speech, or a sent
  message — within `IDLE_GRACE_SEC` (60s). Both timers stop entirely while
  the session is paused (manually or via this same idle-disconnect), since
  they only ever run from the same ticking interval pausing already clears.

Tab close (`beforeunload`/`pagehide`) already released the Anam connection
before this shipped; these two are the "session left running" cases that
didn't otherwise have a ceiling.

## Ownership note

This is a from-scratch build for AI Curator LLC. It follows the same
architectural pattern (Claude + live avatar, rule-based live coaching, a
persona/scenario content model) documented in the original ConsultCastAI
Founding Document, but all content, prompts, and code here are written
fresh for this product, not reused from any client engagement.

## Single sign-on with the AI Curator Consulting Suite

Built 2026-10-03, and **off** until the suite's administrator turns it on (Admin > Accounts on the suite, no deploy). While it is off this app's own signup, login and password reset work exactly as before; nothing below runs.

- `suite_sso.py` talks to the suite's server; `sso_sessions.py` holds the logins that came through the suite; `frontend/suite_sso.js`, `frontend/sso-start.html` and `frontend/sso-callback.html` are the browser's side. The design is `docs/SSO_DESIGN.md` in the aicsuite repo.
- Setting on the API service in Render: `SUITE_SSO_SECRET`, the same value as `AICSUITE_SSO_SECRET_CONSULTCASTAI` on the suite, 32+ characters. Blank keeps single sign-on off here whatever the suite says. It is in `render.yaml`, but a setting added after the service was created is not asked for again: add it by hand. `SUITE_BASE_URL` is optional and defaults to the live suite.
- When it is on: the login screen hands over to the suite; a login made through the suite lasts 7 days and is renewed in the background while the suite sign-in is still good; Log out signs out of the suite and the other apps. The app's own 30-day logins are refused, and work again the moment the switch is turned off.
- Teams are unchanged. A member is found by email and keeps their team, so their access still comes through the team owner's subscription. A new teammate opens the invitation link as before; the invitation is kept in the browser while they create or sign in to their suite account, and they join the team when they arrive back here.
- An account created through the suite has no password of its own here, so "Delete account" (which asks for the current password) does not work for it yet.
- This app has one language, so the suite account's language is not applied here.
- Tests: `python tests/test_sso.py` (throwaway files, the suite replaced by a stand-in; no network).
