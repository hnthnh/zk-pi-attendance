from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

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
SUPPORTED_MODELS = [
    "ZKTeco iClock 680",
    "ZKTeco UA760",
    "ZKTeco MB360",
    "ZKTeco K40",
    "ZKTeco uFace 602",
    "ZKTeco U580",
    "ZKTeco MA300",
]


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


def _resolve_config(
    host: Optional[str] = None,
    port: Optional[int] = None,
    password: Optional[int] = None,
    timeout: Optional[int] = None,
    force_udp: Optional[bool] = None,
) -> Dict[str, Any]:
    return {
        "host": host or DEVICE_IP,
        "port": port or DEVICE_PORT,
        "password": password if password is not None else int(os.environ.get("ZK_DEVICE_PASSWORD", "0")),
        "timeout": timeout or DEVICE_TIMEOUT,
        "force_udp": force_udp if force_udp is not None else FORCE_UDP,
    }


def _connect(config: Dict[str, Any]):
    device = ZK(
        config["host"],
        port=int(config["port"]),
        timeout=int(config["timeout"]),
        password=int(config["password"]),
        force_udp=bool(config["force_udp"]),
    )
    return device.connect()


def test_connection(
    host: Optional[str] = None,
    port: Optional[int] = None,
    password: Optional[int] = None,
    timeout: Optional[int] = None,
    force_udp: Optional[bool] = None,
) -> Dict[str, Any]:
    """Attempt to connect to the device and return metadata."""
    init_db()
    config = _resolve_config(
        host=host,
        port=port,
        password=password,
        timeout=timeout,
        force_udp=force_udp,
    )
    connection = None
    try:
        connection = _connect(config)
        firmware = None
        serial = None
        try:
            firmware = connection.get_firmware_version()
        except Exception:
            firmware = "Unknown"
        try:
            serial = connection.get_serialnumber()
        except Exception:
            serial = "Unknown"
        return {
            "status": "ok",
            "host": config["host"],
            "port": int(config["port"]),
            "firmware": firmware,
            "serial": serial,
        }
    except Exception as exc:
        raise SyncError(f"Không thể kết nối thiết bị: {exc}") from exc
    finally:
        if connection:
            try:
                connection.disconnect()
            except Exception:
                pass


def sync_attendance(
    host: Optional[str] = None,
    port: Optional[int] = None,
    password: Optional[int] = None,
    timeout: Optional[int] = None,
    force_udp: Optional[bool] = None,
) -> Dict[str, Any]:
    """Synchronise attendance logs from the device into SQLite."""
    init_db()

    config = _resolve_config(
        host=host,
        port=port,
        password=password,
        timeout=timeout,
        force_udp=force_udp,
    )

    connection = None
    try:
        connection = _connect(config)
        connection.disable_device()
        _sync_users(connection)
        attendance_entries = connection.get_attendance() or []
        rows = _serialise_attendance(attendance_entries)
        inserted = bulk_insert_attendance(rows)
        return {
            "status": "ok",
            "host": config["host"],
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
