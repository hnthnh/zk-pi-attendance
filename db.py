import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


DB_PATH = _get_base_dir() / "attendance.db"

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    department TEXT,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_ATTENDANCE_TABLE = """
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    status INTEGER NOT NULL,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (user_id),
    UNIQUE (user_id, timestamp, status)
);
"""

CREATE_MAKEUP_TABLE = """
CREATE TABLE IF NOT EXISTS makeup_hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    hours REAL NOT NULL,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, date)
);
"""

CREATE_DEVICES_TABLE = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('auto', 'manual')),
    ip TEXT,
    port INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with Row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create required tables if they do not exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(CREATE_USERS_TABLE)
        cursor.execute(CREATE_ATTENDANCE_TABLE)
        cursor.execute(CREATE_MAKEUP_TABLE)
        cursor.execute(CREATE_DEVICES_TABLE)
        conn.commit()


@contextmanager
def get_cursor(commit: bool = False):
    """Context manager yielding a cursor that optionally commits on exit."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        yield cursor
        if commit:
            conn.commit()
    finally:
        cursor.close()
        conn.close()


def upsert_user(
    user_id: int,
    name: Optional[str] = None,
    department: Optional[str] = None,
) -> None:
    """Ensure the user exists and update metadata when provided."""
    with get_cursor(commit=True) as cursor:
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, name, department) VALUES (?, ?, ?)",
            (user_id, name, department),
        )
        if name is not None or department is not None:
            cursor.execute(
                """
                UPDATE users
                SET name = COALESCE(?, name),
                    department = COALESCE(?, department)
                WHERE user_id = ?
                """,
                (name, department, user_id),
            )


def bulk_insert_attendance(rows: Iterable[tuple[int, str, int]]) -> int:
    """Insert raw attendance rows. Returns the number of new rows inserted."""
    with get_connection() as conn:
        cursor = conn.cursor()
        before = conn.total_changes
        cursor.executemany(
            """
            INSERT OR IGNORE INTO attendance (user_id, timestamp, status)
            VALUES (?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return conn.total_changes - before


def fetch_attendance_rows(
    user_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[sqlite3.Row]:
    """Load attendance rows constrained by optional filters."""
    query = "SELECT user_id, timestamp, status FROM attendance"
    filters = []
    params: list = []

    if user_id is not None:
        filters.append("user_id = ?")
        params.append(user_id)
    if start_date is not None:
        filters.append("date(timestamp) >= date(?)")
        params.append(start_date)
    if end_date is not None:
        filters.append("date(timestamp) <= date(?)")
        params.append(end_date)

    if filters:
        query += " WHERE " + " AND ".join(filters)

    query += " ORDER BY timestamp ASC"

    with get_cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def fetch_users() -> list[sqlite3.Row]:
    """Return all users."""
    with get_cursor() as cursor:
        cursor.execute("SELECT user_id, name, department FROM users ORDER BY user_id")
        return cursor.fetchall()


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    """Return a single user row or None."""
    with get_cursor() as cursor:
        cursor.execute("SELECT user_id, name, department FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone()


def delete_user(user_id: int) -> None:
    """Remove a user and related attendance/make-up records."""
    with get_cursor(commit=True) as cursor:
        cursor.execute("DELETE FROM attendance WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM makeup_hours WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))


def fetch_devices() -> list[sqlite3.Row]:
    """Return all configured devices."""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT id, name, mode, ip, port, created_at FROM devices ORDER BY id"
        )
        return cursor.fetchall()


def insert_device(
    name: str,
    mode: str,
    ip: Optional[str] = None,
    port: Optional[int] = None,
) -> int:
    """Insert a new device and return its primary key."""
    with get_cursor(commit=True) as cursor:
        cursor.execute(
            """
            INSERT INTO devices (name, mode, ip, port)
            VALUES (?, ?, ?, ?)
            """,
            (name, mode, ip, port),
        )
        return cursor.lastrowid


def delete_device(device_id: int) -> None:
    """Delete a device entry."""
    with get_cursor(commit=True) as cursor:
        cursor.execute("DELETE FROM devices WHERE id = ?", (device_id,))


def set_makeup_hours(
    user_id: int,
    date: str,
    hours: float,
    note: Optional[str] = None,
) -> None:
    """Create or update a make-up hours entry for the given user/date."""
    with get_cursor(commit=True) as cursor:
        cursor.execute(
            """
            INSERT INTO makeup_hours (user_id, date, hours, note)
            VALUES (?, date(?), ?, ?)
            ON CONFLICT(user_id, date)
            DO UPDATE SET
                hours = excluded.hours,
                note = excluded.note,
                created_at = CURRENT_TIMESTAMP
            """,
            (user_id, date, hours, note),
        )


def fetch_makeup_hours(
    user_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[tuple[int, str], dict]:
    """Return a mapping of (user_id, date) to stored make-up hours details."""
    query = "SELECT user_id, date, hours, note FROM makeup_hours"
    filters = []
    params: list = []

    if user_id is not None:
        filters.append("user_id = ?")
        params.append(user_id)
    if start_date is not None:
        filters.append("date >= date(?)")
        params.append(start_date)
    if end_date is not None:
        filters.append("date <= date(?)")
        params.append(end_date)

    if filters:
        query += " WHERE " + " AND ".join(filters)

    query += " ORDER BY date ASC"

    with get_cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()

    result: dict[tuple[int, str], dict] = {}
    for row in rows:
        result[(row["user_id"], row["date"])] = {
            "hours": float(row["hours"]),
            "note": row["note"],
        }
    return result
