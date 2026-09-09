# AI Hotel Concierge

An SMS-based AI concierge and staff task management platform, originally built for a family friend's hotel to reduce front-desk load and provide guests with 24/7 automated support — no app required.

## Overview

Guests text the hotel's phone number. An LLM decides whether to reply directly or escalate to a staff task. Hotel staff manage everything — conversations, tasks, guest stays, and hotel knowledge — through a web-based admin dashboard.

## Technical Highlights

- **Twilio webhook pipeline** — inbound SMS routed via `POST /sms`, hotel resolved by destination number
- **LLM routing layer** — GPT-4.1 classifies each guest message as a direct reply or a structured staff task
- **RAG with HNSW vector search** — hotel knowledge docs embedded with `text-embedding-3-small` and retrieved via approximate nearest-neighbor search for context-aware responses
- **Staff admin dashboard** — server-rendered Flask/Jinja UI for managing messages, tasks, guest stays, hotel info, and knowledge base
- **Role-based access** — manager vs. staff roles with scoped permissions
- **SMS compliance** — STOP/HELP handling, opt-out tracking, and per-window rate limiting
- **Dual database support** — SQLite for local development, PostgreSQL for production via `DATABASE_URL`

## Stack

Python, Flask, OpenAI API (GPT-4.1 + text-embedding-3-small), Twilio, SQLite / PostgreSQL, Jinja2

## Setup

Requires Python 3.10+, a Twilio account, and an OpenAI API key. See `.env.example` for required environment variables. The app is deployed and running in production.

## Admin Routes

| Route | Description |
|---|---|
| `/admin/messages` | Guest conversations |
| `/admin/tasks` | Staff task queue |
| `/admin/stay/<id>` | Guest stay detail |
| `/admin/hotel` | Hotel info (hours, wifi, etc.) |
| `/admin/knowledge` | RAG knowledge base |
| `/admin/users` | Staff user management |

---

## What I Learned

- **Prompt engineering is an iterative product problem.** Getting the LLM to reliably distinguish "this guest wants towels" (task) from "what time does the pool close?" (reply) required far more iteration than expected. Small changes in system prompt wording had large effects on behavior.
- **Webhooks require defensive design.** Twilio can retry failed requests, guests can send bursts of messages, and numbers can be spoofed. Rate limiting, idempotency, and opt-out compliance are essential to smooth operations.
- **RAG quality depends on chunking and retrieval, not just embeddings.** Early versions returned irrelevant context because knowledge docs were too long and similarity thresholds were too loose. Tightening both improved response quality significantly.
- **Server-rendered UIs for internal tools.** Flask + Jinja was fast to build, easy to reason about, and  sufficient for a staff-facing dashboard with no need for real-time reactivity.
- **Database schema design matters early.** Retrofitting multi-hotel isolation (scoping everything by `hotel_id`) after the fact would have been painful — building it in from the start kept the data model clean.

## What I Would Do Differently

- **Add a message queue (e.g. Redis + Celery) from the start.** Handling LLM calls synchronously inside the webhook handler works in development but is fragile under load; a slow OpenAI response can cause Twilio to retry and create duplicate messages.
- **Abstract the messaging transport layer earlier.** The app is tightly coupled to Twilio SMS. Supporting additional channels (WhatsApp, Line, web chat) would require significant refactoring; a thin transport abstraction from day one would have made this easier.
- **Use structured outputs for LLM responses.** Early versions parsed free-text LLM responses with heuristics. Switching to OpenAI's structured output / JSON mode made the routing logic dramatically more reliable.
- **Write integration tests for the webhook pipeline.** The `/sms` route involves several sequential steps (hotel lookup, guest creation, rate limiting, LLM call, Twilio send) — any one of which can fail silently. Automated tests with a mocked Twilio client would have caught several bugs earlier.
- **Build an eval set for the LLM routing layer.** Without ground-truth examples of "this message should be a task / this should be a reply," it was hard to measure whether prompt changes were actually improvements or just different.
