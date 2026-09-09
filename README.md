# 🏋️ Gym Tracker — Gavin & Devin

A dead-simple shared workout tracker. The goal: **3 days a week**. Log your gym
days on a calendar, watch this week fill up, and keep your streak alive.

![calendar with two people's workout days](static/style.css)

## Features
- **Calendar view** — tap a day's `G` / `D` pill to log a workout for Gavin or Devin.
- **This week** — live rings showing each person's progress toward the 3× goal.
- **Week streak** — consecutive weeks you both hit the goal (🔥).
- **Day notes** — tap a day to jot what you trained (legs, push, run…).
- Month + all-time totals.

## Run locally
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```
Open http://localhost:5000.

## Config
- `WEEKLY_GOAL` — sessions/week goal (default `3`).
- `PEOPLE` in `app.py` — names and accent colors.
- `DATABASE_URL` — not required locally; SQLite (`gym.db`) is used by default.

The old hotel-concierge project lives in `_archive_hotel_concierge/`.
