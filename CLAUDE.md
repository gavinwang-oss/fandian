# CLAUDE.md — Gym Tracker (Gavin & Devin)

## What this is
A shared gym tracker. Anyone with the link can create an account; everyone who
signs up joins the **same board** and competes together. Goal: hit the gym
**3 days a week**. Log workout days on a month calendar, see this week's progress,
keep week-streaks going, and talk trash. Head-to-head for 2 people becomes a
leaderboard at 3+.

The previous project in this repo (an AI hotel SMS concierge) has been moved to
`_archive_hotel_concierge/` and is no longer part of the app.

## Stack
- **Flask** (`app.py`) — serves one page + a small JSON API.
- **SQLite** (`gym.db`, gitignored) — one table, `workouts`.
- Vanilla HTML/CSS/JS in `templates/index.html`, `static/style.css`, `static/app.js`.
- No auth. A "Logging as" toggle picks who a click applies to.

## Data model
- `users(id, username, display_name, color, password_hash, created_at)` — accounts.
- `workouts(id, person, day 'YYYY-MM-DD', notes, created_at)`, unique on
  `(person, day)`. `person` is the user **id as text**. A row existing = that
  user worked out that day.

## Auth
- Public signup at `/signup`, login `/login`, logout `/logout`. Passwords hashed
  with werkzeug. Session via `FLASK_SECRET_KEY` (set it in prod!). `before_request`
  gates everything except login/signup/static.
- Colors auto-assigned from a palette in signup order.
- You can only log **yourself**: `/api/toggle` and `/api/note` use the session
  user, ignoring any `person` in the body.

## API
- `GET  /api/workouts` — all users' workouts (frontend derives everything).
- `POST /api/toggle` `{day}` — add/remove YOUR workout that day.
- `POST /api/note`   `{day, notes}` — set YOUR note (creates the day if needed).

## Goal
`WEEKLY_GOAL` env var (default 3). Weeks run Monday–Sunday.

## Run
```bash
.venv/bin/python app.py        # http://localhost:5000
```
Production: `gunicorn app:app`. Set `DATABASE_URL` (Postgres) for persistent
hosting — SQLite on an ephemeral host resets on redeploy.

## Conventions
- Keep it small: Flask + vanilla JS, minimal deps.
- Inspect files before editing; edit in place.
