# AI B2B SaaS — Hotel Concierge MVP

This repo contains a local MVP of an SMS-based AI concierge for hotels.

## What’s Included
- Flask SMS webhook (`/sms`)
- SQLite or Postgres database (via `DATABASE_URL`)
- Admin pages:
  - `/admin/messages`
  - `/admin/tasks`
  - `/admin/stay/<id>`
  - `/admin/hotel`
  - `/admin/knowledge`
  - `/admin/users`
- OpenAI LLM fallback with RAG + vector search (HNSW)
- LLM task escalation (reply vs task)
- STOP/HELP compliance + opt-out
- Rate limiting

## Project Path
```
/Users/gavinwang/Documents/New project/ai b2b saas
```

## Setup (First Time)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment Variables
Create `.env` in the project folder:
```
FLASK_SECRET_KEY=your_secret
DATABASE_URL=sqlite:///hotel.db
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4.1
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
TWILIO_SID=...
TWILIO_TOKEN=...
TWILIO_NUMBER=...
STOP_RESPONSE=You have been opted out...
HELP_RESPONSE=Hotel Concierge support...
RATE_LIMIT_COUNT=10
RATE_LIMIT_WINDOW_SECONDS=300
BOOTSTRAP_ADMIN_EMAIL=manager@example.com
BOOTSTRAP_ADMIN_PASSWORD=change-me
BOOTSTRAP_HOTEL_NAME=Demo Hotel
BOOTSTRAP_HOTEL_PHONE=+18885551234
```

## Run the App
```bash
source .venv/bin/activate
python3 app.py
```

## Login
Go to `http://localhost:5000/login` and log in with the bootstrap admin.

## Admin Pages
- http://localhost:5000/admin/messages
- http://localhost:5000/admin/tasks
- http://localhost:5000/admin/stay/1
- http://localhost:5000/admin/hotel
- http://localhost:5000/admin/knowledge
- http://localhost:5000/admin/users

## Notes
- Restart Flask after adding new knowledge docs to rebuild the vector index.
- If OpenAI key is missing, the bot falls back to rule-based behavior.
