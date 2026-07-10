import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception:  # pragma: no cover - optional dependency
    psycopg2 = None
    RealDictCursor = None

DB_PATH = Path(__file__).resolve().parent / "hotel.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")
IS_POSTGRES = DATABASE_URL.startswith("postgres")


def get_conn():
    if IS_POSTGRES:
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is required for Postgres but is not installed")
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _convert_sql(sql: str) -> str:
    if IS_POSTGRES:
        return sql.replace("?", "%s")
    return sql


def _fetchall(sql: str, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(_convert_sql(sql), params)
    rows = cur.fetchall()
    conn.close()
    return rows


def _fetchone(sql: str, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(_convert_sql(sql), params)
    row = cur.fetchone()
    conn.close()
    return row


def _execute(sql: str, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(_convert_sql(sql), params)
    conn.commit()
    conn.close()


def _insert_returning_id(sql: str, params=()):
    conn = get_conn()
    cur = conn.cursor()
    if IS_POSTGRES and "RETURNING" not in sql.upper():
        sql = f"{sql} RETURNING id"
    cur.execute(_convert_sql(sql), params)
    if IS_POSTGRES:
        row = cur.fetchone()
        new_id = int(row["id"]) if row else None
    else:
        new_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return new_id


def init_db():
    _init_db_schema()
    _ensure_migrations()
    _seed_default_hotel()
    _bootstrap_admin()


# Dialect tokens substituted at init time based on the database backend.
_SQLITE_DIALECT = {
    "PK":         "INTEGER PRIMARY KEY AUTOINCREMENT",
    "TS":         "TEXT NOT NULL",
    "TS_NULL":    "TEXT",
    "BOOL_T":     "INTEGER NOT NULL DEFAULT 1",
    "BOOL_F":     "INTEGER NOT NULL DEFAULT 0",
    "BOOL_F_OPT": "INTEGER DEFAULT 0",
}

_POSTGRES_DIALECT = {
    "PK":         "SERIAL PRIMARY KEY",
    "TS":         "TIMESTAMPTZ NOT NULL",
    "TS_NULL":    "TIMESTAMPTZ",
    "BOOL_T":     "BOOLEAN NOT NULL DEFAULT TRUE",
    "BOOL_F":     "BOOLEAN NOT NULL DEFAULT FALSE",
    "BOOL_F_OPT": "BOOLEAN DEFAULT FALSE",
}

# Single source of truth for all table definitions.
# Each entry is (CREATE TABLE sql template, [CREATE INDEX sqls]).
_SCHEMA = [
    (
        """
        CREATE TABLE IF NOT EXISTS hotels (
            id {PK},
            name TEXT NOT NULL,
            timezone TEXT DEFAULT 'America/Los_Angeles',
            phone_number TEXT,
            line_channel_id TEXT,
            line_channel_token TEXT,
            line_channel_secret TEXT,
            staff_language TEXT DEFAULT 'en',
            created_at {TS}
        )
        """,
        ["CREATE INDEX IF NOT EXISTS idx_hotels_phone ON hotels (phone_number)"],
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS guests (
            id {PK},
            phone TEXT NOT NULL UNIQUE,
            created_at {TS}
        )
        """,
        [],
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS stays (
            id {PK},
            guest_id INTEGER NOT NULL,
            hotel_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            room_number TEXT,
            check_out_date TEXT,
            welcome_sent_at {TS_NULL},
            checkout_reminder_sent_at {TS_NULL},
            post_stay_sent_at {TS_NULL},
            created_at {TS},
            FOREIGN KEY (guest_id) REFERENCES guests (id),
            FOREIGN KEY (hotel_id) REFERENCES hotels (id)
        )
        """,
        [
            "CREATE INDEX IF NOT EXISTS idx_stays_hotel ON stays (hotel_id)",
            "CREATE INDEX IF NOT EXISTS idx_stays_guest_hotel ON stays (guest_id, hotel_id)",
        ],
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS messages (
            id {PK},
            stay_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            source TEXT DEFAULT 'guest',
            body TEXT NOT NULL,
            created_at {TS},
            FOREIGN KEY (stay_id) REFERENCES stays (id)
        )
        """,
        ["CREATE INDEX IF NOT EXISTS idx_messages_stay ON messages (stay_id)"],
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id {PK},
            stay_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            status TEXT NOT NULL,
            summary TEXT,
            department TEXT,
            created_from_message_id INTEGER,
            assigned_to_staff_user_id INTEGER,
            priority TEXT DEFAULT 'normal',
            notify_guest_when_done {BOOL_F_OPT},
            completed_at {TS_NULL},
            completed_by_staff_user_id INTEGER,
            created_at {TS},
            FOREIGN KEY (stay_id) REFERENCES stays (id)
        )
        """,
        ["CREATE INDEX IF NOT EXISTS idx_tasks_stay ON tasks (stay_id)"],
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS hotel_info (
            id {PK},
            hotel_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            FOREIGN KEY (hotel_id) REFERENCES hotels (id)
        )
        """,
        ["CREATE INDEX IF NOT EXISTS idx_hotel_info_hotel ON hotel_info (hotel_id)"],
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS hotel_docs (
            id {PK},
            hotel_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding_json TEXT,
            created_at {TS},
            FOREIGN KEY (hotel_id) REFERENCES hotels (id)
        )
        """,
        ["CREATE INDEX IF NOT EXISTS idx_hotel_docs_hotel ON hotel_docs (hotel_id)"],
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS knowledge_suggestions (
            id {PK},
            hotel_id INTEGER NOT NULL,
            stay_id INTEGER,
            guest_question TEXT NOT NULL,
            staff_answer TEXT NOT NULL,
            suggested_title TEXT,
            suggested_content TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at {TS},
            FOREIGN KEY (hotel_id) REFERENCES hotels (id)
        )
        """,
        ["CREATE INDEX IF NOT EXISTS idx_knowledge_suggestions_hotel ON knowledge_suggestions (hotel_id, status)"],
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS staff_users (
            id {PK},
            hotel_id INTEGER NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            is_active {BOOL_T},
            created_at {TS},
            FOREIGN KEY (hotel_id) REFERENCES hotels (id)
        )
        """,
        ["CREATE INDEX IF NOT EXISTS idx_staff_users_hotel ON staff_users (hotel_id)"],
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS guest_hotel_preferences (
            id {PK},
            guest_id INTEGER NOT NULL,
            hotel_id INTEGER NOT NULL,
            opted_out {BOOL_F},
            updated_at {TS},
            UNIQUE(guest_id, hotel_id)
        )
        """,
        [
            "CREATE INDEX IF NOT EXISTS idx_guest_hotel_prefs_guest_hotel"
            " ON guest_hotel_preferences (guest_id, hotel_id)"
        ],
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS inbound_logs (
            id {PK},
            guest_id INTEGER NOT NULL,
            hotel_id INTEGER NOT NULL,
            created_at {TS}
        )
        """,
        [
            "CREATE INDEX IF NOT EXISTS idx_inbound_logs_guest_hotel_time"
            " ON inbound_logs (guest_id, hotel_id, created_at)"
        ],
    ),
]


def _init_db_schema():
    """Create all tables and indexes using a single dialect-aware schema definition."""
    dialect = _POSTGRES_DIALECT if IS_POSTGRES else _SQLITE_DIALECT
    conn = get_conn()
    cur = conn.cursor()
    for table_sql, index_sqls in _SCHEMA:
        cur.execute(_convert_sql(table_sql.format(**dialect)))
        for idx_sql in index_sqls:
            cur.execute(idx_sql)
    conn.commit()
    conn.close()


def _ensure_migrations():
    # Add missing columns in SQLite for older DBs
    if IS_POSTGRES:
        _execute("ALTER TABLE hotels ADD COLUMN IF NOT EXISTS phone_number TEXT")
        _execute("ALTER TABLE hotels ADD COLUMN IF NOT EXISTS line_channel_id TEXT")
        _execute("ALTER TABLE hotels ADD COLUMN IF NOT EXISTS line_channel_token TEXT")
        _execute("ALTER TABLE hotels ADD COLUMN IF NOT EXISTS line_channel_secret TEXT")
        _execute("ALTER TABLE hotels ADD COLUMN IF NOT EXISTS staff_language TEXT DEFAULT 'en'")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS assigned_to_staff_user_id INTEGER")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'normal'")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS notify_guest_when_done BOOLEAN DEFAULT FALSE")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_by_staff_user_id INTEGER")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS summary TEXT")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS department TEXT")
        _execute("ALTER TABLE stays ADD COLUMN IF NOT EXISTS hotel_id INTEGER")
        _execute("ALTER TABLE stays ADD COLUMN IF NOT EXISTS room_number TEXT")
        _execute("ALTER TABLE stays ADD COLUMN IF NOT EXISTS check_out_date TEXT")
        _execute("ALTER TABLE stays ADD COLUMN IF NOT EXISTS welcome_sent_at TIMESTAMPTZ")
        _execute("ALTER TABLE stays ADD COLUMN IF NOT EXISTS checkout_reminder_sent_at TIMESTAMPTZ")
        _execute("ALTER TABLE stays ADD COLUMN IF NOT EXISTS post_stay_sent_at TIMESTAMPTZ")
        _execute("CREATE INDEX IF NOT EXISTS idx_staff_users_hotel ON staff_users (hotel_id)")
        _execute(
            "CREATE INDEX IF NOT EXISTS idx_guest_hotel_prefs_guest_hotel ON guest_hotel_preferences (guest_id, hotel_id)"
        )
        _execute(
            "CREATE INDEX IF NOT EXISTS idx_inbound_logs_guest_hotel_time ON inbound_logs (guest_id, hotel_id, created_at)"
        )
        return

    conn = get_conn()
    cur = conn.cursor()
    cols = cur.execute("PRAGMA table_info(tasks)").fetchall()
    names = {row["name"] for row in cols}
    if "assigned_to_staff_user_id" not in names:
        cur.execute("ALTER TABLE tasks ADD COLUMN assigned_to_staff_user_id INTEGER")
    if "priority" not in names:
        cur.execute("ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'normal'")
    if "notify_guest_when_done" not in names:
        cur.execute("ALTER TABLE tasks ADD COLUMN notify_guest_when_done INTEGER DEFAULT 0")
    if "completed_at" not in names:
        cur.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT")
    if "completed_by_staff_user_id" not in names:
        cur.execute("ALTER TABLE tasks ADD COLUMN completed_by_staff_user_id INTEGER")
    if "summary" not in names:
        cur.execute("ALTER TABLE tasks ADD COLUMN summary TEXT")
    if "department" not in names:
        cur.execute("ALTER TABLE tasks ADD COLUMN department TEXT")

    cols = cur.execute("PRAGMA table_info(stays)").fetchall()
    names = {row["name"] for row in cols}
    if "hotel_id" not in names:
        cur.execute("ALTER TABLE stays ADD COLUMN hotel_id INTEGER")
        default_hotel_id = _fetchone("SELECT id FROM hotels ORDER BY id ASC LIMIT 1")
        if default_hotel_id:
            cur.execute("UPDATE stays SET hotel_id = ? WHERE hotel_id IS NULL", (default_hotel_id["id"],))
    if "room_number" not in names:
        cur.execute("ALTER TABLE stays ADD COLUMN room_number TEXT")
    if "check_out_date" not in names:
        cur.execute("ALTER TABLE stays ADD COLUMN check_out_date TEXT")
    if "welcome_sent_at" not in names:
        cur.execute("ALTER TABLE stays ADD COLUMN welcome_sent_at TEXT")
    if "checkout_reminder_sent_at" not in names:
        cur.execute("ALTER TABLE stays ADD COLUMN checkout_reminder_sent_at TEXT")
    if "post_stay_sent_at" not in names:
        cur.execute("ALTER TABLE stays ADD COLUMN post_stay_sent_at TEXT")

    cols = cur.execute("PRAGMA table_info(messages)").fetchall()
    names = {row["name"] for row in cols}
    if "source" not in names:
        cur.execute("ALTER TABLE messages ADD COLUMN source TEXT DEFAULT 'guest'")
        # Best-effort backfill: existing outbound messages were almost all AI replies
        cur.execute("UPDATE messages SET source = 'ai' WHERE direction = 'outbound'")

    cols = cur.execute("PRAGMA table_info(hotels)").fetchall()
    names = {row["name"] for row in cols}
    if "phone_number" not in names:
        cur.execute("ALTER TABLE hotels ADD COLUMN phone_number TEXT")
    if "line_channel_id" not in names:
        cur.execute("ALTER TABLE hotels ADD COLUMN line_channel_id TEXT")
    if "line_channel_token" not in names:
        cur.execute("ALTER TABLE hotels ADD COLUMN line_channel_token TEXT")
    if "line_channel_secret" not in names:
        cur.execute("ALTER TABLE hotels ADD COLUMN line_channel_secret TEXT")
    if "staff_language" not in names:
        cur.execute("ALTER TABLE hotels ADD COLUMN staff_language TEXT DEFAULT 'en'")

    # knowledge_suggestions table (added for self-expanding KB)
    existing_tables = {row[0] for row in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "knowledge_suggestions" not in existing_tables:
        cur.execute("""
            CREATE TABLE knowledge_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hotel_id INTEGER NOT NULL,
                stay_id INTEGER,
                guest_question TEXT NOT NULL,
                staff_answer TEXT NOT NULL,
                suggested_title TEXT,
                suggested_content TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_suggestions_hotel "
            "ON knowledge_suggestions (hotel_id, status)"
        )

    cur.execute("CREATE INDEX IF NOT EXISTS idx_staff_users_hotel ON staff_users (hotel_id)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_guest_hotel_prefs_guest_hotel ON guest_hotel_preferences (guest_id, hotel_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_inbound_logs_guest_hotel_time ON inbound_logs (guest_id, hotel_id, created_at)"
    )

    conn.commit()
    conn.close()


def _seed_default_hotel():
    row = _fetchone("SELECT id, phone_number FROM hotels LIMIT 1")
    if row is None:
        hotel_id = _insert_returning_id(
            "INSERT INTO hotels (name, timezone, phone_number, created_at) VALUES (?, ?, ?, ?)",
            (os.getenv("BOOTSTRAP_HOTEL_NAME", "Demo Hotel"), "America/Los_Angeles", os.getenv("BOOTSTRAP_HOTEL_PHONE", "") or os.getenv("TWILIO_NUMBER", ""), _now()),
        )
    else:
        hotel_id = int(row["id"])
        phone_number = row["phone_number"] if "phone_number" in row.keys() else None
        if not phone_number and (os.getenv("BOOTSTRAP_HOTEL_PHONE") or os.getenv("TWILIO_NUMBER")):
            _execute(
                "UPDATE hotels SET phone_number = ? WHERE id = ?",
                (os.getenv("BOOTSTRAP_HOTEL_PHONE") or os.getenv("TWILIO_NUMBER"), hotel_id),
            )

    count = _fetchone("SELECT COUNT(1) AS cnt FROM hotel_info WHERE hotel_id = ?", (hotel_id,))
    if count and int(count["cnt"]) == 0:
        default_info = {
            "hotel_name": "Demo Hotel",
            "checkin_time": "3:00 PM",
            "checkout_time": "11:00 AM",
            "breakfast_hours": "6:30 AM - 10:30 AM",
            "pool_hours": "7:00 AM - 9:00 PM",
            "gym_hours": "24 hours",
            "wifi_info": "Network: DemoHotel_WiFi, Password: welcome123",
            "parking_info": "Valet available; ask the front desk.",
        }
        for k, v in default_info.items():
            _execute(
                "INSERT INTO hotel_info (hotel_id, key, value) VALUES (?, ?, ?)",
                (hotel_id, k, v),
            )


def _bootstrap_admin():
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "")
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email or not password:
        return
    existing = _fetchone("SELECT id FROM staff_users LIMIT 1")
    if existing:
        return
    from werkzeug.security import generate_password_hash

    hotel = _fetchone("SELECT id FROM hotels ORDER BY id ASC LIMIT 1")
    if not hotel:
        return
    _insert_returning_id(
        "INSERT INTO staff_users (hotel_id, email, password_hash, role, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (int(hotel["id"]), email, generate_password_hash(password), "manager", True, _now()),
    )


def _now():
    from datetime import timezone
    if IS_POSTGRES:
        return datetime.now(timezone.utc)
    return datetime.utcnow().isoformat(timespec="seconds")


# === Auth / Users ===

def get_staff_user_by_email(email: str):
    return _fetchone("SELECT * FROM staff_users WHERE email = ? AND is_active = ?", (email, True))


def get_staff_user_by_id(user_id: int):
    return _fetchone("SELECT * FROM staff_users WHERE id = ? AND is_active = ?", (user_id, True))


def list_staff_users(hotel_id: int):
    return _fetchall(
        "SELECT id, email, role, is_active, created_at FROM staff_users WHERE hotel_id = ? ORDER BY id DESC",
        (hotel_id,),
    )


def create_staff_user(hotel_id: int, email: str, password_hash: str, role: str):
    return _insert_returning_id(
        "INSERT INTO staff_users (hotel_id, email, password_hash, role, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (hotel_id, email, password_hash, role, True, _now()),
    )


# === Hotel / Tenant ===

def get_hotel_id_for_number(to_number: str):
    if not to_number:
        return None
    normalized = _normalize_phone(to_number)
    row = _fetchone("SELECT id FROM hotels WHERE phone_number = ?", (to_number,))
    if row:
        return int(row["id"])
    if normalized:
        row = _fetchone("SELECT id FROM hotels WHERE phone_number = ?", (normalized,))
        if row:
            return int(row["id"])
    # Fallback: compare normalized versions (handles formatting differences)
    rows = _fetchall("SELECT id, phone_number FROM hotels", ())
    for r in rows:
        if _normalize_phone(r["phone_number"]) == normalized:
            return int(r["id"])
    return None


def _normalize_phone(value: str) -> str:
    if not value:
        return ""
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits.startswith("1") and len(digits) == 11:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if value.startswith("+") and digits:
        return f"+{digits}"
    return value.strip()


def get_hotel_id_for_line_channel(line_channel_id: str):
    """Return hotel_id matching the given LINE channel user ID (destination field)."""
    if not line_channel_id:
        return None
    row = _fetchone("SELECT id FROM hotels WHERE line_channel_id = ?", (line_channel_id,))
    return int(row["id"]) if row else None


def get_hotel_line_credentials(hotel_id: int):
    """Return LINE channel credentials for a hotel, or None if not configured."""
    row = _fetchone(
        "SELECT line_channel_id, line_channel_token, line_channel_secret FROM hotels WHERE id = ?",
        (hotel_id,),
    )
    if not row:
        return None
    return {
        "channel_id": row["line_channel_id"],
        "token": row["line_channel_token"],
        "secret": row["line_channel_secret"],
    }


def update_hotel_line_credentials(hotel_id: int, channel_id: str, token: str, secret: str) -> None:
    _execute(
        "UPDATE hotels SET line_channel_id = ?, line_channel_token = ?, line_channel_secret = ? WHERE id = ?",
        (channel_id or None, token or None, secret or None, hotel_id),
    )


def set_hotel_phone(hotel_id: int, phone_number: str) -> None:
    _execute("UPDATE hotels SET phone_number = ? WHERE id = ?", (phone_number, hotel_id))


def get_hotel(hotel_id: int):
    return _fetchone(
        "SELECT id, name, phone_number, timezone, staff_language FROM hotels WHERE id = ?",
        (hotel_id,),
    )


def update_hotel_staff_language(hotel_id: int, language: str) -> None:
    _execute("UPDATE hotels SET staff_language = ? WHERE id = ?", (language, hotel_id))


def get_default_hotel_id() -> int:
    row = _fetchone("SELECT id FROM hotels ORDER BY id ASC LIMIT 1")
    return int(row["id"]) if row else 0


def get_hotel_info(hotel_id: int) -> dict:
    rows = _fetchall("SELECT key, value FROM hotel_info WHERE hotel_id = ?", (hotel_id,))
    return {row["key"]: row["value"] for row in rows}


def list_hotel_info(hotel_id: int):
    return _fetchall(
        "SELECT id, key, value FROM hotel_info WHERE hotel_id = ? ORDER BY key ASC",
        (hotel_id,),
    )


def upsert_hotel_info(hotel_id: int, key: str, value: str) -> None:
    row = _fetchone("SELECT id FROM hotel_info WHERE hotel_id = ? AND key = ?", (hotel_id, key))
    if row:
        _execute("UPDATE hotel_info SET value = ? WHERE id = ?", (value, row["id"]))
    else:
        _execute("INSERT INTO hotel_info (hotel_id, key, value) VALUES (?, ?, ?)", (hotel_id, key, value))


def add_hotel_doc(hotel_id: int, title: str, content: str) -> int:
    return _insert_returning_id(
        "INSERT INTO hotel_docs (hotel_id, title, content, created_at) VALUES (?, ?, ?, ?)",
        (hotel_id, title, content, _now()),
    )


def list_hotel_docs(hotel_id: int, limit: int = 200):
    return _fetchall(
        "SELECT id, title, content, embedding_json, created_at FROM hotel_docs WHERE hotel_id = ? ORDER BY id DESC LIMIT ?",
        (hotel_id, limit),
    )


def update_hotel_doc_embedding(doc_id: int, embedding_json: str) -> None:
    _execute("UPDATE hotel_docs SET embedding_json = ? WHERE id = ?", (embedding_json, doc_id))


def delete_hotel_info(hotel_id: int, info_id: int) -> None:
    _execute("DELETE FROM hotel_info WHERE id = ? AND hotel_id = ?", (info_id, hotel_id))


def delete_hotel_doc(hotel_id: int, doc_id: int) -> None:
    _execute("DELETE FROM hotel_docs WHERE id = ? AND hotel_id = ?", (doc_id, hotel_id))


def get_hotel_doc(hotel_id: int, doc_id: int):
    return _fetchone(
        "SELECT id, title, content FROM hotel_docs WHERE id = ? AND hotel_id = ?",
        (doc_id, hotel_id),
    )


def update_hotel_doc(hotel_id: int, doc_id: int, title: str, content: str) -> None:
    # Clear embedding so it gets regenerated with the new content
    _execute(
        "UPDATE hotel_docs SET title = ?, content = ?, embedding_json = NULL WHERE id = ? AND hotel_id = ?",
        (title, content, doc_id, hotel_id),
    )


# === Guests / Preferences ===

def get_or_create_guest(phone: str) -> int:
    row = _fetchone("SELECT id FROM guests WHERE phone = ?", (phone,))
    if row:
        return int(row["id"])
    return _insert_returning_id(
        "INSERT INTO guests (phone, created_at) VALUES (?, ?)",
        (phone, _now()),
    )


def set_opted_out(guest_id: int, hotel_id: int, opted_out: bool) -> None:
    row = _fetchone(
        "SELECT id FROM guest_hotel_preferences WHERE guest_id = ? AND hotel_id = ?",
        (guest_id, hotel_id),
    )
    if row:
        _execute(
            "UPDATE guest_hotel_preferences SET opted_out = ?, updated_at = ? WHERE id = ?",
            (opted_out, _now(), row["id"]),
        )
    else:
        _execute(
            "INSERT INTO guest_hotel_preferences (guest_id, hotel_id, opted_out, updated_at) VALUES (?, ?, ?, ?)",
            (guest_id, hotel_id, opted_out, _now()),
        )


def is_opted_out(guest_id: int, hotel_id: int) -> bool:
    row = _fetchone(
        "SELECT opted_out FROM guest_hotel_preferences WHERE guest_id = ? AND hotel_id = ?",
        (guest_id, hotel_id),
    )
    if not row:
        return False
    return bool(row["opted_out"])


# === Rate limiting ===

def log_inbound(guest_id: int, hotel_id: int) -> None:
    _execute(
        "INSERT INTO inbound_logs (guest_id, hotel_id, created_at) VALUES (?, ?, ?)",
        (guest_id, hotel_id, _now()),
    )


def is_rate_limited(guest_id: int, hotel_id: int, window_seconds: int, limit: int) -> bool:
    if IS_POSTGRES:
        from datetime import timezone
        since = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        row = _fetchone(
            "SELECT COUNT(1) AS cnt FROM inbound_logs WHERE guest_id = ? AND hotel_id = ? AND created_at >= ?",
            (guest_id, hotel_id, since),
        )
    else:
        since = (datetime.utcnow() - timedelta(seconds=window_seconds)).isoformat(timespec="seconds")
        row = _fetchone(
            "SELECT COUNT(1) AS cnt FROM inbound_logs WHERE guest_id = ? AND hotel_id = ? AND created_at >= ?",
            (guest_id, hotel_id, since),
        )
    return row and int(row["cnt"]) >= limit


# === Stays / Messages ===

def get_stay(hotel_id: int, stay_id: int):
    return _fetchone(
        """SELECT id, guest_id, hotel_id, status, room_number, check_out_date,
                  welcome_sent_at, checkout_reminder_sent_at, post_stay_sent_at,
                  created_at
           FROM stays WHERE id = ? AND hotel_id = ?""",
        (stay_id, hotel_id),
    )


def set_stay_room_number(hotel_id: int, stay_id: int, room_number: str | None) -> None:
    _execute(
        "UPDATE stays SET room_number = ? WHERE id = ? AND hotel_id = ?",
        (room_number or None, stay_id, hotel_id),
    )


def set_stay_checkout_date(hotel_id: int, stay_id: int, check_out_date: str | None) -> None:
    _execute(
        "UPDATE stays SET check_out_date = ? WHERE id = ? AND hotel_id = ?",
        (check_out_date or None, stay_id, hotel_id),
    )


def mark_welcome_sent(stay_id: int) -> None:
    _execute("UPDATE stays SET welcome_sent_at = ? WHERE id = ?", (_now(), stay_id))


def mark_checkout_reminder_sent(stay_id: int) -> None:
    _execute("UPDATE stays SET checkout_reminder_sent_at = ? WHERE id = ?", (_now(), stay_id))


def mark_post_stay_sent(stay_id: int) -> None:
    _execute("UPDATE stays SET post_stay_sent_at = ? WHERE id = ?", (_now(), stay_id))


def get_stays_needing_outreach():
    """Return all stays with a checkout date set that haven't had all outreach sent."""
    return _fetchall(
        """
        SELECT s.id AS stay_id, s.hotel_id, s.room_number, s.check_out_date,
               s.checkout_reminder_sent_at, s.post_stay_sent_at,
               g.phone AS guest_phone,
               h.phone_number AS hotel_phone,
               h.name AS hotel_name,
               h.line_channel_token AS hotel_line_token
        FROM stays s
        JOIN guests g ON g.id = s.guest_id
        JOIN hotels h ON h.id = s.hotel_id
        WHERE s.check_out_date IS NOT NULL
          AND (h.phone_number IS NOT NULL OR h.line_channel_token IS NOT NULL)
          AND g.phone IS NOT NULL
          AND (s.checkout_reminder_sent_at IS NULL OR s.post_stay_sent_at IS NULL)
        """,
        (),
    )


def get_or_create_active_stay(guest_id: int, hotel_id: int) -> int:
    row = _fetchone(
        "SELECT id FROM stays WHERE guest_id = ? AND hotel_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
        (guest_id, hotel_id),
    )
    if row:
        return int(row["id"])
    return _insert_returning_id(
        "INSERT INTO stays (guest_id, hotel_id, status, created_at) VALUES (?, ?, 'active', ?)",
        (guest_id, hotel_id, _now()),
    )


def log_message(stay_id: int, direction: str, body: str, source: str = "guest") -> int:
    return _insert_returning_id(
        "INSERT INTO messages (stay_id, direction, source, body, created_at) VALUES (?, ?, ?, ?, ?)",
        (stay_id, direction, source, body, _now()),
    )


def list_messages_for_hotel(hotel_id: int, limit: int = 200):
    return _fetchall(
        """
        SELECT m.id, m.direction, m.body, m.created_at,
               g.phone AS guest_phone, s.id AS stay_id, s.room_number
        FROM messages m
        JOIN stays s ON s.id = m.stay_id
        JOIN guests g ON g.id = s.guest_id
        WHERE s.hotel_id = ?
        ORDER BY s.id DESC, m.id ASC
        LIMIT ?
        """,
        (hotel_id, limit),
    )


def list_conversations_for_hotel(hotel_id: int, limit: int = 100):
    """One row per stay, showing the last message and open task status."""
    return _fetchall(
        """
        SELECT s.id AS stay_id, s.room_number, g.phone AS guest_phone,
               m.body AS last_body, m.direction AS last_direction,
               m.created_at AS last_at,
               (SELECT COUNT(*) FROM tasks t
                WHERE t.stay_id = s.id AND t.status != 'done') AS open_task_count,
               (SELECT CASE MIN(CASE t.priority
                                WHEN 'urgent' THEN 0
                                WHEN 'high'   THEN 1
                                WHEN 'normal' THEN 2
                                WHEN 'low'    THEN 3
                                ELSE 4 END)
                       WHEN 0 THEN 'urgent'
                       WHEN 1 THEN 'high'
                       WHEN 2 THEN 'normal'
                       WHEN 3 THEN 'low'
                       ELSE NULL END
                FROM tasks t
                WHERE t.stay_id = s.id AND t.status != 'done') AS top_open_priority
        FROM stays s
        JOIN guests g ON g.id = s.guest_id
        JOIN messages m ON m.id = (
            SELECT MAX(id) FROM messages WHERE stay_id = s.id
        )
        WHERE s.hotel_id = ?
        ORDER BY m.created_at DESC
        LIMIT ?
        """,
        (hotel_id, limit),
    )


def list_guests_for_hotel(hotel_id: int, limit: int = 200):
    """One row per guest at this hotel: latest stay plus activity summary."""
    return _fetchall(
        """
        SELECT g.id AS guest_id, g.phone, g.created_at,
               s.id AS stay_id, s.room_number, s.status AS stay_status,
               COALESCE(msg.message_count, 0) AS message_count,
               msg.last_message_at,
               COALESCE(tk.open_task_count, 0) AS open_task_count,
               p.opted_out
        FROM (
            SELECT guest_id, MAX(id) AS latest_stay_id
            FROM stays WHERE hotel_id = ?
            GROUP BY guest_id
        ) latest
        JOIN guests g ON g.id = latest.guest_id
        JOIN stays s ON s.id = latest.latest_stay_id
        LEFT JOIN (
            SELECT s2.guest_id, COUNT(*) AS message_count,
                   MAX(m.created_at) AS last_message_at
            FROM messages m
            JOIN stays s2 ON s2.id = m.stay_id
            WHERE s2.hotel_id = ?
            GROUP BY s2.guest_id
        ) msg ON msg.guest_id = g.id
        LEFT JOIN (
            SELECT s2.guest_id, COUNT(*) AS open_task_count
            FROM tasks t
            JOIN stays s2 ON s2.id = t.stay_id
            WHERE s2.hotel_id = ? AND t.status != 'done'
            GROUP BY s2.guest_id
        ) tk ON tk.guest_id = g.id
        LEFT JOIN guest_hotel_preferences p
               ON p.guest_id = g.id AND p.hotel_id = ?
        ORDER BY COALESCE(msg.last_message_at, g.created_at) DESC
        LIMIT ?
        """,
        (hotel_id, hotel_id, hotel_id, hotel_id, limit),
    )


def list_messages_for_stay(hotel_id: int, stay_id: int):
    return _fetchall(
        """
        SELECT m.id, m.direction, m.body, m.created_at, m.source,
               g.phone AS guest_phone
        FROM messages m
        JOIN stays s ON s.id = m.stay_id
        JOIN guests g ON g.id = s.guest_id
        WHERE m.stay_id = ? AND s.hotel_id = ?
        ORDER BY m.id ASC
        """,
        (stay_id, hotel_id),
    )


def list_messages_for_stay_after(hotel_id: int, stay_id: int, after_id: int):
    """Return messages with id > after_id for the given stay (hotel-scoped)."""
    return _fetchall(
        """
        SELECT m.id, m.direction, m.body, m.created_at,
               g.phone AS guest_phone
        FROM messages m
        JOIN stays s ON s.id = m.stay_id
        JOIN guests g ON g.id = s.guest_id
        WHERE m.stay_id = ? AND s.hotel_id = ? AND m.id > ?
        ORDER BY m.id ASC
        """,
        (stay_id, hotel_id, after_id),
    )


def list_recent_messages_for_stay(stay_id: int, limit: int = 6):
    return _fetchall(
        """
        SELECT m.id, m.direction, m.body, m.created_at
        FROM messages m
        WHERE m.stay_id = ?
        ORDER BY m.id DESC
        LIMIT ?
        """,
        (stay_id, limit),
    )


def get_guest_phone_for_stay(hotel_id: int, stay_id: int) -> str:
    row = _fetchone(
        """
        SELECT g.phone AS guest_phone
        FROM stays s
        JOIN guests g ON g.id = s.guest_id
        WHERE s.id = ? AND s.hotel_id = ?
        """,
        (stay_id, hotel_id),
    )
    return row["guest_phone"] if row else ""


def get_guest_id_for_stay(hotel_id: int, stay_id: int) -> int | None:
    row = _fetchone(
        """
        SELECT g.id AS guest_id
        FROM stays s
        JOIN guests g ON g.id = s.guest_id
        WHERE s.id = ? AND s.hotel_id = ?
        """,
        (stay_id, hotel_id),
    )
    return int(row["guest_id"]) if row else None


# === Tasks ===

def create_task(
    stay_id: int,
    task_type: str,
    status: str = "open",
    created_from_message_id: int | None = None,
    summary: str | None = None,
    department: str | None = None,
    priority: str = "normal",
    notify_guest_when_done: bool = False,
) -> int:
    return _insert_returning_id(
        """
        INSERT INTO tasks (
            stay_id, type, status, summary, department, created_from_message_id,
            assigned_to_staff_user_id, priority, notify_guest_when_done, completed_at,
            completed_by_staff_user_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL, ?)
        """,
        (
            stay_id,
            task_type,
            status,
            summary,
            department,
            created_from_message_id,
            priority,
            notify_guest_when_done,
            _now(),
        ),
    )


def list_tasks(
    hotel_id: int,
    limit: int = 200,
    status: str | None = None,
    assigned_to: int | None = None,
    priority: str | None = None,
):
    where = "WHERE s.hotel_id = ?"
    params = [hotel_id]
    if status:
        where += " AND t.status = ?"
        params.append(status)
    if assigned_to:
        where += " AND t.assigned_to_staff_user_id = ?"
        params.append(assigned_to)
    if priority:
        where += " AND t.priority = ?"
        params.append(priority)

    sql = f"""
        SELECT t.id, t.type, t.status, t.summary, t.department, t.priority,
               t.notify_guest_when_done, t.created_at, t.created_from_message_id,
               t.assigned_to_staff_user_id, t.completed_at,
               g.phone AS guest_phone, s.id AS stay_id, s.room_number,
               m.body AS created_from_body
        FROM tasks t
        JOIN stays s ON s.id = t.stay_id
        JOIN guests g ON g.id = s.guest_id
        LEFT JOIN messages m ON m.id = t.created_from_message_id
        {where}
        ORDER BY
            CASE t.status WHEN 'done' THEN 1 ELSE 0 END ASC,
            CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 WHEN 'low' THEN 3 ELSE 4 END ASC,
            t.id ASC
        LIMIT ?
        """
    params.append(limit)
    return _fetchall(sql, tuple(params))


def list_tasks_for_stay(hotel_id: int, stay_id: int):
    return _fetchall(
        """
        SELECT t.id, t.summary, t.status, t.priority, t.created_at
        FROM tasks t
        JOIN stays s ON s.id = t.stay_id
        WHERE t.stay_id = ? AND s.hotel_id = ?
        ORDER BY t.id ASC
        """,
        (stay_id, hotel_id),
    )


def get_task_stats(hotel_id: int) -> dict:
    """Return summary counts for the tasks dashboard."""
    open_row = _fetchone(
        "SELECT COUNT(*) AS cnt FROM tasks t JOIN stays s ON s.id = t.stay_id WHERE s.hotel_id = ? AND t.status = 'open'",
        (hotel_id,),
    )
    urgent_row = _fetchone(
        "SELECT COUNT(*) AS cnt FROM tasks t JOIN stays s ON s.id = t.stay_id WHERE s.hotel_id = ? AND t.status = 'open' AND t.priority IN ('urgent', 'high')",
        (hotel_id,),
    )
    unassigned_row = _fetchone(
        "SELECT COUNT(*) AS cnt FROM tasks t JOIN stays s ON s.id = t.stay_id WHERE s.hotel_id = ? AND t.status = 'open' AND t.assigned_to_staff_user_id IS NULL",
        (hotel_id,),
    )
    return {
        "open": int(open_row["cnt"]) if open_row else 0,
        "urgent": int(urgent_row["cnt"]) if urgent_row else 0,
        "unassigned": int(unassigned_row["cnt"]) if unassigned_row else 0,
    }


def update_task_status(task_id: int, status: str, completed_by: int | None = None):
    if status == "done":
        _execute(
            "UPDATE tasks SET status = ?, completed_at = ?, completed_by_staff_user_id = ? WHERE id = ?",
            (status, _now(), completed_by, task_id),
        )
    else:
        _execute(
            "UPDATE tasks SET status = ?, completed_at = NULL, completed_by_staff_user_id = NULL WHERE id = ?",
            (status, task_id),
        )


def update_task_fields(task_id: int, assigned_to: int | None, priority: str, notify_guest_when_done: bool):
    _execute(
        "UPDATE tasks SET assigned_to_staff_user_id = ?, priority = ?, notify_guest_when_done = ? WHERE id = ?",
        (assigned_to, priority, notify_guest_when_done, task_id),
    )


def get_task(task_id: int):
    return _fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))


# === Knowledge Suggestions ===

def create_knowledge_suggestion(
    hotel_id: int,
    stay_id: int | None,
    guest_question: str,
    staff_answer: str,
    suggested_title: str | None,
    suggested_content: str | None,
) -> int:
    return _insert_returning_id(
        """
        INSERT INTO knowledge_suggestions
            (hotel_id, stay_id, guest_question, staff_answer, suggested_title, suggested_content, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (hotel_id, stay_id, guest_question, staff_answer, suggested_title, suggested_content, _now()),
    )


def list_knowledge_suggestions(hotel_id: int, status: str = "pending"):
    return _fetchall(
        """
        SELECT id, stay_id, guest_question, staff_answer, suggested_title, suggested_content, status, created_at
        FROM knowledge_suggestions
        WHERE hotel_id = ? AND status = ?
        ORDER BY id DESC
        """,
        (hotel_id, status),
    )


def update_knowledge_suggestion_status(suggestion_id: int, status: str) -> None:
    _execute(
        "UPDATE knowledge_suggestions SET status = ? WHERE id = ?",
        (status, suggestion_id),
    )


def get_analytics(hotel_id: int, days: int = 30) -> dict:
    """Return analytics data for a hotel over the last N days."""
    from datetime import timezone
    since_dt = datetime.now(timezone.utc) - timedelta(days=days)
    since = since_dt if IS_POSTGRES else since_dt.strftime("%Y-%m-%d")

    # helper: minutes between two timestamp columns
    def _min_diff(a, b):
        if IS_POSTGRES:
            return f"EXTRACT(EPOCH FROM ({a} - {b})) / 60"
        return f"(julianday({a}) - julianday({b})) * 1440"

    # helper: extract hour from timestamp
    def _hour(col):
        if IS_POSTGRES:
            return f"EXTRACT(HOUR FROM {col})::int"
        return f"strftime('%H', {col})"

    # helper: extract date string from timestamp
    def _date(col):
        if IS_POSTGRES:
            return f"TO_CHAR({col}, 'YYYY-MM-DD')"
        return f"strftime('%Y-%m-%d', {col})"

    # Total inbound guest messages
    row = _fetchone(
        """
        SELECT COUNT(*) AS cnt FROM messages m
        JOIN stays s ON s.id = m.stay_id
        WHERE s.hotel_id = ? AND m.source = 'guest' AND m.created_at >= ?
        """,
        (hotel_id, since),
    )
    total_inbound = int(row["cnt"]) if row else 0

    # Inbound messages that got an AI reply within 2 min (no task created)
    row = _fetchone(
        f"""
        SELECT COUNT(*) AS cnt FROM messages m
        JOIN stays s ON s.id = m.stay_id
        WHERE s.hotel_id = ? AND m.source = 'guest' AND m.created_at >= ?
        AND EXISTS (
            SELECT 1 FROM messages r
            WHERE r.stay_id = m.stay_id AND r.source = 'ai'
            AND r.created_at > m.created_at
            AND {_min_diff('r.created_at', 'm.created_at')} < 2
        )
        AND NOT EXISTS (
            SELECT 1 FROM tasks t
            WHERE t.stay_id = m.stay_id
            AND t.created_at >= m.created_at
            AND {_min_diff('t.created_at', 'm.created_at')} < 2
        )
        """,
        (hotel_id, since),
    )
    ai_replies = int(row["cnt"]) if row else 0

    # Tasks created
    row = _fetchone(
        """
        SELECT COUNT(*) AS cnt FROM tasks t
        JOIN stays s ON s.id = t.stay_id
        WHERE s.hotel_id = ? AND t.created_at >= ?
        """,
        (hotel_id, since),
    )
    tasks_created = int(row["cnt"]) if row else 0

    # Avg task resolution time in minutes
    row = _fetchone(
        f"""
        SELECT AVG({_min_diff('t.completed_at', 't.created_at')}) AS avg_min
        FROM tasks t JOIN stays s ON s.id = t.stay_id
        WHERE s.hotel_id = ? AND t.status = 'done'
        AND t.completed_at IS NOT NULL AND t.created_at >= ?
        """,
        (hotel_id, since),
    )
    avg_resolution_min = round(row["avg_min"]) if row and row["avg_min"] else None

    # Avg staff reply time
    row = _fetchone(
        f"""
        SELECT AVG({_min_diff('r.created_at', 'm.created_at')}) AS avg_min
        FROM messages r
        JOIN stays s ON s.id = r.stay_id
        JOIN messages m ON m.id = (
            SELECT MAX(m2.id) FROM messages m2
            WHERE m2.stay_id = r.stay_id
            AND m2.source = 'guest'
            AND m2.created_at < r.created_at
            AND {_min_diff('r.created_at', 'm2.created_at')} <= 120
        )
        WHERE r.source = 'staff' AND s.hotel_id = ? AND r.created_at >= ?
        """,
        (hotel_id, since),
    )
    avg_staff_reply_min = round(row["avg_min"]) if row and row["avg_min"] else None

    # Department breakdown
    dept_rows = _fetchall(
        """
        SELECT t.department, COUNT(*) AS cnt FROM tasks t
        JOIN stays s ON s.id = t.stay_id
        WHERE s.hotel_id = ? AND t.created_at >= ?
        GROUP BY t.department ORDER BY cnt DESC
        """,
        (hotel_id, since),
    )
    dept_breakdown = [{"department": r["department"] or "unspecified", "count": int(r["cnt"])} for r in dept_rows]

    # Avg resolution time by department
    dept_time_rows = _fetchall(
        f"""
        SELECT t.department,
               AVG({_min_diff('t.completed_at', 't.created_at')}) AS avg_min
        FROM tasks t JOIN stays s ON s.id = t.stay_id
        WHERE s.hotel_id = ? AND t.status = 'done'
        AND t.completed_at IS NOT NULL AND t.created_at >= ?
        GROUP BY t.department
        """,
        (hotel_id, since),
    )
    dept_resolution = [
        {"department": r["department"] or "unspecified", "avg_min": round(r["avg_min"])}
        for r in dept_time_rows if r["avg_min"]
    ]

    # Peak hours (0-23)
    hour_rows = _fetchall(
        f"""
        SELECT {_hour('m.created_at')} AS hr, COUNT(*) AS cnt
        FROM messages m JOIN stays s ON s.id = m.stay_id
        WHERE s.hotel_id = ? AND m.source = 'guest' AND m.created_at >= ?
        GROUP BY hr ORDER BY hr
        """,
        (hotel_id, since),
    )
    peak_hours = {int(r["hr"]): int(r["cnt"]) for r in hour_rows}
    peak_hours_list = [peak_hours.get(h, 0) for h in range(24)]

    # Daily volume for trend
    daily_rows = _fetchall(
        f"""
        SELECT {_date('m.created_at')} AS day, COUNT(*) AS cnt
        FROM messages m JOIN stays s ON s.id = m.stay_id
        WHERE s.hotel_id = ? AND m.source = 'guest' AND m.created_at >= ?
        GROUP BY day ORDER BY day
        """,
        (hotel_id, since),
    )
    daily_volume = [{"day": r["day"], "count": int(r["cnt"])} for r in daily_rows]

    ai_rate = min(100, round(ai_replies / total_inbound * 100)) if total_inbound else 0
    hours_saved = round(ai_replies * 4 / 60, 1)

    return {
        "days": days,
        "total_inbound": total_inbound,
        "ai_replies": ai_replies,
        "tasks_created": tasks_created,
        "ai_rate": ai_rate,
        "hours_saved": hours_saved,
        "avg_resolution_min": avg_resolution_min,
        "avg_staff_reply_min": avg_staff_reply_min,
        "dept_breakdown": dept_breakdown,
        "dept_resolution": dept_resolution,
        "peak_hours_list": peak_hours_list,
        "daily_volume": daily_volume,
    }
