from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Iterable

from zk import ZK

from db import bulk_insert_attendance, init_db, upsert_user


DEVICE_IP = os.environ.get("ZK_DEVICE_IP", "192.168.0.201")
DEVICE_PORT = int(os.environ.get("ZK_DEVICE_PORT", "4370"))
DEVICE_TIMEOUT = int(os.environ.get("ZK_DEVICE_TIMEOUT", "5"))
FORCE_UDP = os.environ.get("ZK_DEVICE_FORCE_UDP", "false").lower() in {
    "1",
    "true",
    "yes",
}


class SyncError(RuntimeError):
    """Raised when synchronisation with the ZKTeco device fails."""


def _serialise_attendance(entries: Iterable[Any]) -> list[tuple[int, str, int]]:
    serialised: list[tuple[int, str, int]] = []
    for entry in entries:
        if entry.timestamp is None:
            continue
        timestamp = entry.timestamp
        if isinstance(timestamp, datetime):
            timestamp_str = timestamp.isoformat(sep=" ", timespec="seconds")
        else:
            # zk library sometimes returns naive strings already
            timestamp_str = str(timestamp)
        serialised.append((int(entry.user_id), timestamp_str, int(entry.status)))
    return serialised


def _sync_users(device_conn) -> None:
    try:
        users = device_conn.get_users()
    except Exception:
        # Skip silently if firmware does not support user extraction
        return
    for user in users:
        name = getattr(user, "name", None) or None
        dept = getattr(user, "department", None) or None
        try:
            upsert_user(int(user.user_id), name=name, department=dept)
        except Exception:
            # Do not break sync on malformed metadata
            continue


def sync_attendance() -> Dict[str, Any]:
    """Synchronise attendance logs from the device into SQLite."""
    init_db()

    device = ZK(
        DEVICE_IP,
        port=DEVICE_PORT,
        timeout=DEVICE_TIMEOUT,
        password=int(os.environ.get("ZK_DEVICE_PASSWORD", "0")),
        force_udp=FORCE_UDP,
    )

    connection = None
    try:
        connection = device.connect()
        connection.disable_device()
        _sync_users(connection)
        attendance_entries = connection.get_attendance() or []
        rows = _serialise_attendance(attendance_entries)
        inserted = bulk_insert_attendance(rows)
        return {
            "status": "ok",
            "host": DEVICE_IP,
            "total_rows": len(rows),
            "inserted_rows": inserted,
        }
    except Exception as exc:  # pragma: no cover - defensive broad catch
        raise SyncError(f"Failed to sync attendance: {exc}") from exc
    finally:
        if connection:
            try:
                connection.enable_device()
                connection.disconnect()
            except Exception:
                pass
