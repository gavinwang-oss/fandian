"""
Gym Tracker — a shared workout tracker. Anyone with the link can create an
account; everyone who signs up joins the same board and competes together.

Flask. SQLite locally by default; set DATABASE_URL to a Postgres URL (e.g. on
Render's free tier) for persistence. Runs as `python3 app.py` locally and
`gunicorn app:app` in production.
"""

import os
import re
import sqlite3
from datetime import date, datetime
from functools import wraps

from flask import (Flask, jsonify, redirect, render_template, request,
                   session, url_for)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-prod")

WEEKLY_GOAL = int(os.environ.get("WEEKLY_GOAL", "3"))

# Accent colors handed out to accounts in signup order.
COLORS = ["#38bdf8", "#fb923c", "#34d399", "#a78bfa", "#f472b6",
          "#facc15", "#f87171", "#2dd4bf", "#c084fc", "#4ade80"]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_RE = re.compile(r"^[A-Za-z0-9 _.'\-]{1,30}$")


def normalize_name(name):
    """Match names case-insensitively, ignoring extra spaces."""
    return " ".join(name.strip().lower().split())

# --- Database backend -------------------------------------------------------
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
    """Run a query against whichever backend is active. Queries use '?'
    placeholders (rewritten to '%s' for Postgres); rows come back as dicts."""
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
        CREATE TABLE IF NOT EXISTS users (
            {id_col},
            username      TEXT UNIQUE NOT NULL,   -- normalized name (match key)
            display_name  TEXT NOT NULL,          -- name as typed
            color         TEXT NOT NULL,
            created_at    TEXT NOT NULL
        )
        """
    )
    query(
        f"""
        CREATE TABLE IF NOT EXISTS workouts (
            {id_col},
            person     TEXT NOT NULL,          -- user id (as text)
            day        TEXT NOT NULL,          -- YYYY-MM-DD
            notes      TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(person, day)
        )
        """
    )
    migrate()


def migrate():
    """Idempotent schema fixes on an existing (persistent) database.

    Early versions gave `users` a NOT NULL `password_hash`. The app no longer
    uses passwords, so new-user INSERTs would violate that constraint. Drop the
    column if it's still there. Safe to run on every startup."""
    if USE_PG:
        try:
            query("ALTER TABLE users DROP COLUMN IF EXISTS password_hash")
        except Exception:
            pass
    else:
        # SQLite only supports DROP COLUMN on newer versions; ignore if it fails
        # (a fresh SQLite file already has the current schema).
        try:
            query("ALTER TABLE users DROP COLUMN password_hash")
        except Exception:
            pass

    # Remove throwaway players created by deploy smoke-tests.
    try:
        ids = query("SELECT id FROM users WHERE username LIKE 'deploytest\\_%' ESCAPE '\\'",
                    fetch="all") or []
        for row in ids:
            query("DELETE FROM workouts WHERE person=?", (str(row["id"]),))
        query("DELETE FROM users WHERE username LIKE 'deploytest\\_%' ESCAPE '\\'")
    except Exception:
        pass


def valid_date(s):
    if not s or not DATE_RE.match(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


# --- Auth -------------------------------------------------------------------
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return query("SELECT id, username, display_name, color FROM users WHERE id=?",
                 (uid,), fetch="one")


@app.before_request
def require_login():
    # Public endpoints; everything else needs a valid session.
    if request.endpoint in {"login", "static"}:
        return
    # A logged-in session whose user still exists is fine. A missing or stale
    # session (e.g. the user was removed, or the DB reset) bounces to login
    # instead of 500-ing.
    if session.get("uid") and current_user():
        return
    session.pop("uid", None)
    if request.path.startswith("/api/"):
        return jsonify({"error": "auth required"}), 401
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """No passwords — just type your name. New name = new person; existing name
    (case-insensitive) logs you in as them. Fine for a private friends' board."""
    if session.get("uid"):
        return redirect(url_for("index"))
    if request.method == "POST":
        raw = request.form.get("name", "")
        name = " ".join(raw.strip().split())[:30]
        norm = normalize_name(raw)

        if not name or not NAME_RE.match(name):
            return render_template("login.html", name=raw,
                                   error="Enter a name (letters, numbers, spaces).")

        u = query("SELECT id FROM users WHERE username=?", (norm,), fetch="one")
        if not u:
            n = query("SELECT COUNT(*) AS c FROM users", fetch="one")["c"]
            color = COLORS[n % len(COLORS)]
            query(
                "INSERT INTO users (username, display_name, color, created_at) "
                "VALUES (?,?,?,?)",
                (norm, name, color, datetime.utcnow().isoformat()),
            )
            u = query("SELECT id FROM users WHERE username=?", (norm,), fetch="one")

        session["uid"] = u["id"]
        return redirect(url_for("index"))

    return render_template("login.html", error=None, name="")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --- Main app ---------------------------------------------------------------
@app.route("/")
def index():
    me = current_user()
    users = query("SELECT id, display_name, color FROM users ORDER BY id", fetch="all")
    people = [{"key": str(u["id"]), "name": u["display_name"], "color": u["color"]}
              for u in users]
    return render_template(
        "index.html",
        people=people,
        me={"key": str(me["id"]), "name": me["display_name"], "color": me["color"]},
        weekly_goal=WEEKLY_GOAL,
        today=date.today().isoformat(),
    )


@app.route("/api/workouts")
def api_workouts():
    rows = query("SELECT person, day, notes FROM workouts ORDER BY day", fetch="all")
    return jsonify(rows)


@app.route("/api/toggle", methods=["POST"])
def api_toggle():
    """Toggle the LOGGED-IN user's workout for a day. You can only log yourself."""
    data = request.get_json(silent=True) or {}
    person = str(session["uid"])
    day = str(data.get("day", ""))
    if not valid_date(day):
        return jsonify({"error": "bad date"}), 400

    existing = query("SELECT id FROM workouts WHERE person=? AND day=?",
                     (person, day), fetch="one")
    if existing:
        query("DELETE FROM workouts WHERE id=?", (existing["id"],))
        active = False
    else:
        query("INSERT INTO workouts (person, day, notes, created_at) VALUES (?,?,?,?)",
              (person, day, "", datetime.utcnow().isoformat()))
        active = True
    return jsonify({"person": person, "day": day, "active": active})


@app.route("/api/note", methods=["POST"])
def api_note():
    """Set the note on the logged-in user's workout day."""
    data = request.get_json(silent=True) or {}
    person = str(session["uid"])
    day = str(data.get("day", ""))
    notes = str(data.get("notes", ""))[:500]
    if not valid_date(day):
        return jsonify({"error": "bad date"}), 400

    existing = query("SELECT id FROM workouts WHERE person=? AND day=?",
                     (person, day), fetch="one")
    if existing:
        query("UPDATE workouts SET notes=? WHERE id=?", (notes, existing["id"]))
    else:
        query("INSERT INTO workouts (person, day, notes, created_at) VALUES (?,?,?,?)",
              (person, day, notes, datetime.utcnow().isoformat()))
    return jsonify({"person": person, "day": day, "notes": notes, "active": True})


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
