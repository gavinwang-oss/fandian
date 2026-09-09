"""
Gym Tracker — a tiny shared workout tracker for Gavin & Devin.
Log gym days on a calendar, track weekly goals and streaks, keep each other honest.

Flask. Uses SQLite locally by default; set DATABASE_URL to a Postgres URL
(e.g. on Render's free tier) and it uses Postgres so data survives redeploys.
Runs as `python3 app.py` locally and `gunicorn app:app` in production.
"""

import os
import re
import sqlite3
from datetime import date, datetime

from flask import Flask, jsonify, render_template, request

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

app = Flask(__name__)

# --- People -----------------------------------------------------------------
# The two athletes. `key` is stored in the DB; name/color drive the UI.
PEOPLE = [
    {"key": "gavin", "name": "Gavin", "color": "#38bdf8"},   # cyan
    {"key": "devin", "name": "Devin", "color": "#fb923c"},   # orange
]
PERSON_KEYS = {p["key"] for p in PEOPLE}

WEEKLY_GOAL = int(os.environ.get("WEEKLY_GOAL", "3"))

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# --- Database backend -------------------------------------------------------
# Postgres when DATABASE_URL is a postgres URL, else a local SQLite file.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_PG = DATABASE_URL.startswith("postgres")
DB_PATH = os.environ.get("GYM_DB_PATH", os.path.join(os.path.dirname(__file__), "gym.db"))

if USE_PG:
    import psycopg2
    import psycopg2.extras


def get_conn():
    if USE_PG:
        return psycopg2.connect(DATABASE_URL)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql, params=(), fetch=None):
    """Run a query against whichever backend is active.

    Queries are written with '?' placeholders (SQLite style); they're rewritten
    to '%s' for Postgres. `fetch` is 'all', 'one', or None. Rows come back as
    plain dicts either way.
    """
    conn = get_conn()
    try:
        if USE_PG:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql.replace("?", "%s"), params)
        else:
            cur = conn.cursor()
            cur.execute(sql, params)

        result = None
        if fetch == "all":
            result = [dict(r) for r in cur.fetchall()]
        elif fetch == "one":
            row = cur.fetchone()
            result = dict(row) if row else None
        conn.commit()
        return result
    finally:
        conn.close()


def init_db():
    id_col = ("id BIGSERIAL PRIMARY KEY" if USE_PG
              else "id INTEGER PRIMARY KEY AUTOINCREMENT")
    query(
        f"""
        CREATE TABLE IF NOT EXISTS workouts (
            {id_col},
            person     TEXT NOT NULL,
            day        TEXT NOT NULL,           -- YYYY-MM-DD
            notes      TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(person, day)
        )
        """
    )


def valid_date(s):
    if not s or not DATE_RE.match(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


# --- Routes -----------------------------------------------------------------
@app.route("/")
def index():
    return render_template(
        "index.html",
        people=PEOPLE,
        weekly_goal=WEEKLY_GOAL,
        today=date.today().isoformat(),
    )


@app.route("/api/workouts")
def api_workouts():
    """Return every logged workout. The frontend computes the calendar,
    weekly goals, and streaks from this list."""
    rows = query("SELECT person, day, notes FROM workouts ORDER BY day", fetch="all")
    return jsonify(rows)


@app.route("/api/toggle", methods=["POST"])
def api_toggle():
    """Toggle a person's workout for a given day. Returns the new state."""
    data = request.get_json(silent=True) or {}
    person = str(data.get("person", "")).lower()
    day = str(data.get("day", ""))

    if person not in PERSON_KEYS:
        return jsonify({"error": "unknown person"}), 400
    if not valid_date(day):
        return jsonify({"error": "bad date"}), 400

    existing = query(
        "SELECT id FROM workouts WHERE person=? AND day=?", (person, day), fetch="one"
    )
    if existing:
        query("DELETE FROM workouts WHERE id=?", (existing["id"],))
        active = False
    else:
        query(
            "INSERT INTO workouts (person, day, notes, created_at) VALUES (?,?,?,?)",
            (person, day, "", datetime.utcnow().isoformat()),
        )
        active = True

    return jsonify({"person": person, "day": day, "active": active})


@app.route("/api/note", methods=["POST"])
def api_note():
    """Set the note for a person's workout day. Creates the workout if missing."""
    data = request.get_json(silent=True) or {}
    person = str(data.get("person", "")).lower()
    day = str(data.get("day", ""))
    notes = str(data.get("notes", ""))[:500]

    if person not in PERSON_KEYS:
        return jsonify({"error": "unknown person"}), 400
    if not valid_date(day):
        return jsonify({"error": "bad date"}), 400

    existing = query(
        "SELECT id FROM workouts WHERE person=? AND day=?", (person, day), fetch="one"
    )
    if existing:
        query("UPDATE workouts SET notes=? WHERE id=?", (notes, existing["id"]))
    else:
        query(
            "INSERT INTO workouts (person, day, notes, created_at) VALUES (?,?,?,?)",
            (person, day, notes, datetime.utcnow().isoformat()),
        )

    return jsonify({"person": person, "day": day, "notes": notes, "active": True})


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
