# CLAUDE.md — Gym Tracker (Gavin & Devin)

## What this is
A tiny shared gym tracker for two people, Gavin and Devin. Goal: hit the gym
**3 days a week**. Log workout days on a month calendar, see this week's progress
toward the goal, and keep week-streaks going.

The previous project in this repo (an AI hotel SMS concierge) has been moved to
`_archive_hotel_concierge/` and is no longer part of the app.

## Stack
- **Flask** (`app.py`) — serves one page + a small JSON API.
- **SQLite** (`gym.db`, gitignored) — one table, `workouts`.
- Vanilla HTML/CSS/JS in `templates/index.html`, `static/style.css`, `static/app.js`.
- No auth. A "Logging as" toggle picks who a click applies to.

## Data model
`workouts(id, person, day 'YYYY-MM-DD', notes, created_at)`, unique on
`(person, day)`. A row existing = that person worked out that day.

## API
- `GET  /api/workouts` — all logged workouts (frontend derives calendar/streaks).
- `POST /api/toggle` `{person, day}` — add/remove a workout day.
- `POST /api/note`   `{person, day, notes}` — set notes (creates the day if needed).

## People / goal
Configured at the top of `app.py`: `PEOPLE` (key, name, color) and `WEEKLY_GOAL`
(default 3, overridable via the `WEEKLY_GOAL` env var). Weeks run Monday–Sunday.

## Run
```bash
.venv/bin/python app.py        # http://localhost:5000
```
Production: `gunicorn app:app`. Set `DATABASE_URL` (Postgres) for persistent
hosting — SQLite on an ephemeral host resets on redeploy.

## Conventions
- Keep it small: Flask + vanilla JS, minimal deps.
- Inspect files before editing; edit in place.
