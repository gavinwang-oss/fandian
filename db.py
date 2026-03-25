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
            created_at {TS},
            FOREIGN KEY (guest_id) REFERENCES guests (id),
            FOREIGN KEY (hotel_id) REFERENCES hotels (id)
        )
        """,
        ["CREATE INDEX IF NOT EXISTS idx_stays_hotel ON stays (hotel_id)"],
    ),
    (
        """
        CREATE TABLE IF NOT EXISTS messages (
            id {PK},
            stay_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
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
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS assigned_to_staff_user_id INTEGER")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'normal'")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS notify_guest_when_done BOOLEAN DEFAULT FALSE")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_by_staff_user_id INTEGER")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS summary TEXT")
        _execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS department TEXT")
        _execute("ALTER TABLE stays ADD COLUMN IF NOT EXISTS hotel_id INTEGER")
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

    cols = cur.execute("PRAGMA table_info(hotels)").fetchall()
    names = {row["name"] for row in cols}
    if "phone_number" not in names:
        cur.execute("ALTER TABLE hotels ADD COLUMN phone_number TEXT")

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
    if IS_POSTGRES:
        return datetime.utcnow()
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
        since = datetime.utcnow() - timedelta(seconds=window_seconds)
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


def log_message(stay_id: int, direction: str, body: str) -> int:
    return _insert_returning_id(
        "INSERT INTO messages (stay_id, direction, body, created_at) VALUES (?, ?, ?, ?)",
        (stay_id, direction, body, _now()),
    )


def list_messages_for_hotel(hotel_id: int, limit: int = 200):
    return _fetchall(
        """
        SELECT m.id, m.direction, m.body, m.created_at,
               g.phone AS guest_phone, s.id AS stay_id
        FROM messages m
        JOIN stays s ON s.id = m.stay_id
        JOIN guests g ON g.id = s.guest_id
        WHERE s.hotel_id = ?
        ORDER BY s.id DESC, m.id ASC
        LIMIT ?
        """,
        (hotel_id, limit),
    )


def list_messages_for_stay(hotel_id: int, stay_id: int):
    return _fetchall(
        """
        SELECT m.id, m.direction, m.body, m.created_at,
               g.phone AS guest_phone
        FROM messages m
        JOIN stays s ON s.id = m.stay_id
        JOIN guests g ON g.id = s.guest_id
        WHERE m.stay_id = ? AND s.hotel_id = ?
        ORDER BY m.id ASC
        """,
        (stay_id, hotel_id),
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
               g.phone AS guest_phone, s.id AS stay_id,
               m.body AS created_from_body
        FROM tasks t
        JOIN stays s ON s.id = t.stay_id
        JOIN guests g ON g.id = s.guest_id
        LEFT JOIN messages m ON m.id = t.created_from_message_id
        {where}
        ORDER BY
            CASE t.status WHEN 'done' THEN 1 ELSE 0 END ASC,
            CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 WHEN 'low' THEN 3 ELSE 4 END ASC,
            t.id DESC
        LIMIT ?
        """
    params.append(limit)
    return _fetchall(sql, tuple(params))


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
