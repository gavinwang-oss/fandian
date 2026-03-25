# CLAUDE.md — AI Hotel Concierge (Admin + SMS MVP)

## 1) Project Overview
**Product:** AI‑powered SMS concierge and service desk for hotels.
**Core user flow:** Guest texts hotel number → AI replies or creates a task → staff sees tasks/messages in admin → staff completes task and optionally notifies guest.
**Business purpose:** Reduce front‑desk load, improve guest response time, and provide 24/7 messaging UX.
**Stage:** Early MVP / pilot‑ready prototype.

## 2) Product Summary
- Guests text a hotel phone number (no app).
- Backend decides: respond directly or create a staff task.
- Staff use admin dashboard to manage chats, tasks, hotel info, and hotel knowledge.
- Multi‑tenant: data scoped by `hotel_id`.

## 3) Core Architecture
**Flask app**
- `app.py` handles `/sms` webhook, login, LLM routing, and fallback responses.
- `admin.py` contains admin routes, now backed by template files.

**Twilio webhook**
- `POST /sms` receives inbound messages from Twilio.
- Hotel resolved via `To` number → `hotels.phone_number`.

**Database**
- `db.py` provides a lightweight data layer (SQL) with SQLite by default.
- Postgres supported via `DATABASE_URL`.
- Schema init + idempotent migrations in `init_db()`.

**OpenAI + RAG**
- OpenAI Responses API for “reply vs task” and fallback replies.
- `hotel_docs` stored with embeddings.
- Vector search via HNSW (`vector_index.py`) with fallback cosine similarity.

**Admin UI**
- Server‑rendered Jinja templates in `/templates`.
- Shared layout: `base_admin.html` + `/static/admin.css`.

## 4) Key Entities / Data Model
- `hotels`: hotel record + phone number
- `staff_users`: staff login (hotel_id, role, email)
- `guests`: global by phone number
- `stays`: guest stays per hotel (`guest_id`, `hotel_id`)
- `messages`: inbound/outbound chat messages
- `tasks`: staff task workflow (status, priority, assignment)
- `hotel_info`: key/value info (hours, wifi, etc.)
- `hotel_docs`: knowledge snippets + embeddings
- `guest_hotel_preferences`: per‑hotel opt‑out
- `inbound_logs`: rate limit tracking

## 5) Important Workflows
**Inbound SMS**
1. Twilio POSTs to `/sms`.
2. Resolve `hotel_id` using `To` number.
3. Create/get guest and active stay.
4. STOP/HELP handling.
5. Rate‑limit check.
6. LLM decision (reply vs task).
7. Send reply and log messages.

**Task flow**
- Task created with default status `open`.
- Admin can assign, set priority, mark done.
- If `notify_guest_when_done`, SMS is sent when task closed.

**Auth flow**
- Staff login at `/login`.
- Roles: `manager` vs `staff`.
- Manager has access to hotel info, knowledge, users.

## 6) Project Conventions
- Keep Flask + Jinja (no React/Next).
- Prefer incremental edits over rewrites.
- Preserve route behavior.
- Keep admin UI simple and maintainable.
- Multi‑tenant isolation is mandatory.
- Avoid cross‑hotel data leakage.
- Use env‑based config.
- Minimal dependencies.

## 7) Current Priorities
- UI polish for admin dashboard.
- More reliable LLM task vs reply logic.
- Compliance hardening (STOP/HELP, opt‑out, rate limits).
- Production readiness (Postgres + logging).
- Pilot deployment readiness.

## 8) Safe‑Edit Instructions for Claude
- Inspect files before modifying.
- Edit in place; no parallel apps.
- Keep schema migrations idempotent.
- Update templates consistently.
- Keep dependencies minimal.
- Document new env vars.

## 9) Run / Setup Notes
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```
Login: `http://localhost:5000/login`

**Admin URLs:**
- `/admin/messages`
- `/admin/tasks`
- `/admin/stay/<id>`
- `/admin/hotel`
- `/admin/knowledge`
- `/admin/users`

## 10) Known Constraints / Non‑Goals
- Not a full guest‑facing web app; SMS is primary channel.
- Admin UI is server‑rendered (no SPA).
- Avoid heavy frontend rewrites.
- Don’t replace working flows without reason.

## Environment Variables
```bash
FLASK_SECRET_KEY=your_secret
APP_ENV=development
DATABASE_URL=sqlite:///hotel.db
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4.1
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
TWILIO_SID=...
TWILIO_TOKEN=...
TWILIO_NUMBER=...
STOP_RESPONSE=You have been opted out and will no longer receive messages. Reply HELP for assistance.
HELP_RESPONSE=Hotel Concierge support: reply with your request, or reply STOP to opt out.
RATE_LIMIT_COUNT=10
RATE_LIMIT_WINDOW_SECONDS=300
BOOTSTRAP_ADMIN_EMAIL=admin@demo.local
BOOTSTRAP_ADMIN_PASSWORD=change-me-123
BOOTSTRAP_HOTEL_NAME=Demo Hotel
BOOTSTRAP_HOTEL_PHONE=+18885551234
```
