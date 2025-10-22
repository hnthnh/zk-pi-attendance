from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Optional

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from db import (
    delete_device,
    delete_user,
    fetch_devices,
    fetch_users,
    get_user,
    init_db,
    insert_device,
    set_makeup_hours,
    upsert_user,
)
from summary import get_daily_summary, summary_dataframe
from zk_sync import SUPPORTED_MODELS, SyncError, sync_attendance, test_connection


REQUIRED_DEVICE_KEYS = {"host", "port", "timeout", "password", "force_udp"}
TRUE_VALUES = {"1", "true", "yes", "on"}


def _parse_bool(value: Optional[object]) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in TRUE_VALUES
    return bool(value)


def _get_session_device_config() -> Optional[dict]:
    config = session.get("device_config")
    if not isinstance(config, dict):
        return None
    if not REQUIRED_DEVICE_KEYS.issubset(config.keys()):
        return None
    if not config.get("host"):
        return None
    try:
        config["port"] = int(config["port"])
        config["timeout"] = int(config["timeout"])
        config["password"] = int(config["password"])
        config["force_udp"] = _parse_bool(config["force_udp"])
    except (TypeError, ValueError):
        return None
    return config


def _ensure_device_connection():
    config = _get_session_device_config()
    if not config:
        return None, redirect(url_for("connect_device"))
    return config, None


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "maychamcong-secret-key")

    init_db()

    @app.get("/")
    def index():
        config, redirect_response = _ensure_device_connection()
        if redirect_response:
            return redirect_response
        users = [dict(row) for row in fetch_users()]
        return render_template("index.html", users=users, device_config=config)

    @app.get("/connect")
    def connect_device():
        devices = [dict(row) for row in fetch_devices()]
        current_config = _get_session_device_config()
        return render_template(
            "connect.html",
            devices=devices,
            supported_models=SUPPORTED_MODELS,
            active_config=current_config,
        )

    @app.get("/employees")
    def employees():
        _, redirect_response = _ensure_device_connection()
        if redirect_response:
            return redirect_response
        users = [dict(row) for row in fetch_users()]
        return render_template("employees.html", users=users)

    @app.get("/devices")
    def devices():
        devices = [dict(row) for row in fetch_devices()]
        return render_template("devices.html", devices=devices)

    @app.get("/sync")
    def sync():
        config = _get_session_device_config()
        if not config:
            return (
                jsonify({"status": "error", "message": "Vui lòng kết nối thiết bị trước khi đồng bộ."}),
                412,
            )
        try:
            result = sync_attendance(
                host=config["host"],
                port=config["port"],
                password=config["password"],
                timeout=config["timeout"],
                force_udp=config["force_udp"],
            )
            return jsonify(result)
        except SyncError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500

    @app.get("/summary")
    def summary():
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        user_id = request.args.get("user_id", type=int)
        data = get_daily_summary(user_id=user_id, start_date=start_date, end_date=end_date)
        return jsonify(data)

    @app.get("/summary/<int:user_id>")
    def summary_by_user(user_id: int):
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        data = get_daily_summary(user_id=user_id, start_date=start_date, end_date=end_date)
        return jsonify(data)

    @app.get("/export")
    def export():
        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        user_id = request.args.get("user_id", type=int)
        df = summary_dataframe(user_id=user_id, start_date=start_date, end_date=end_date)
        if df.empty:
            return jsonify({"status": "error", "message": "No data to export"}), 404

        export_df = df[[
            "user_id",
            "name",
            "date",
            "check_in",
            "check_out",
            "working_hours",
        ]].copy()
        export_df.rename(
            columns={
                "user_id": "Employee ID",
                "name": "Name",
                "date": "Date",
                "check_in": "Check In",
                "check_out": "Check Out",
                "working_hours": "Working Hours",
            },
            inplace=True,
        )

        def _format_time_cell(value: Optional[str]) -> str:
            if not value:
                return ""
            try:
                return datetime.fromisoformat(value).strftime("%H:%M:%S")
            except ValueError:
                return value

        export_df["Check In"] = export_df["Check In"].map(_format_time_cell)
        export_df["Check Out"] = export_df["Check Out"].map(_format_time_cell)
        export_df["Working Hours"] = export_df["Working Hours"].map(
            lambda x: "" if x is None or (isinstance(x, float) and (x != x)) else x
        )

        buffer = io.BytesIO()
        export_df.to_excel(buffer, index=False)
        buffer.seek(0)
        return send_file(
            buffer,
            as_attachment=True,
            download_name="attendance_summary.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.post("/makeup")
    def makeup():
        payload = request.get_json(silent=True) or {}
        try:
            user_id = int(payload.get("user_id"))
            hours = float(payload.get("hours"))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid user or hours value"}), 400

        date_value = payload.get("date")
        note = payload.get("note")

        if not date_value:
            return jsonify({"status": "error", "message": "Date is required"}), 400
        if hours < 0:
            return jsonify({"status": "error", "message": "Hours must be non-negative"}), 400

        set_makeup_hours(user_id=user_id, date=date_value, hours=hours, note=note)
        summary_row = get_daily_summary(
            user_id=user_id,
            start_date=date_value,
            end_date=date_value,
        )

        return jsonify(
            {
                "status": "ok",
                "data": summary_row[0] if summary_row else None,
            }
        )

    @app.post("/api/users")
    def create_user():
        payload = request.get_json(silent=True) or {}
        try:
            user_id = int(payload.get("user_id"))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "user_id phải là số"}), 400

        if get_user(user_id):
            return jsonify({"status": "error", "message": "User đã tồn tại"}), 409

        name = payload.get("name") or None
        department = payload.get("department") or None

        upsert_user(user_id, name=name, department=department)
        user_row = get_user(user_id)
        return jsonify({"status": "ok", "data": dict(user_row)}), 201

    @app.put("/api/users/<int:user_id>")
    def update_user(user_id: int):
        if not get_user(user_id):
            return jsonify({"status": "error", "message": "User không tồn tại"}), 404

        payload = request.get_json(silent=True) or {}
        name = payload.get("name")
        department = payload.get("department")

        upsert_user(user_id, name=name, department=department)
        user_row = get_user(user_id)
        return jsonify({"status": "ok", "data": dict(user_row)})

    @app.delete("/api/users/<int:user_id>")
    def remove_user(user_id: int):
        if not get_user(user_id):
            return jsonify({"status": "error", "message": "User không tồn tại"}), 404

        delete_user(user_id)
        return jsonify({"status": "ok"})

    @app.post("/devices")
    def create_device():
        form = request.form
        name = (form.get("name") or "").strip()
        mode = (form.get("mode") or "manual").lower()
        ip = (form.get("ip") or "").strip()
        port_raw = (form.get("port") or "").strip()

        errors: list[str] = []
        if not name:
            errors.append("Tên thiết bị không được bỏ trống.")
        if mode not in {"auto", "manual"}:
            errors.append("Chế độ thiết bị không hợp lệ.")

        port: Optional[int] = None
        if port_raw:
            try:
                port = int(port_raw)
            except ValueError:
                errors.append("Port phải là số.")

        if errors:
            devices = [dict(row) for row in fetch_devices()]
            return (
                render_template(
                    "devices.html",
                    devices=devices,
                    error=" ".join(errors),
                    form_data={
                        "name": name,
                        "mode": mode,
                        "ip": ip,
                        "port": port_raw,
                    },
                ),
                400,
            )

        if not ip:
            ip = None

        insert_device(name, mode, ip, port)
        return redirect(url_for("devices"))

    @app.post("/devices/<int:device_id>/delete")
    def remove_device(device_id: int):
        delete_device(device_id)
        return redirect(url_for("devices"))

    @app.get("/api/devices")
    def api_devices():
        devices = [dict(row) for row in fetch_devices()]
        return jsonify({"devices": devices})

    @app.get("/api/device/current")
    def api_current_device():
        config = _get_session_device_config()
        if not config:
            return jsonify({"connected": False})
        return jsonify({"connected": True, "config": config})

    @app.post("/api/device/test")
    def api_test_device():
        payload = request.get_json(silent=True) or {}
        host = (payload.get("host") or payload.get("ip") or "").strip()
        port = payload.get("port")
        password = payload.get("password")
        timeout = payload.get("timeout")
        force_udp = payload.get("force_udp")

        if not host:
            return jsonify({"status": "error", "message": "Vui lòng nhập địa chỉ IP của thiết bị."}), 400
        try:
            port_int = int(port) if port is not None else None
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Port phải là số."}), 400

        try:
            result = test_connection(
                host=host,
                port=port_int,
                password=int(password) if password not in (None, "") else None,
                timeout=int(timeout) if timeout not in (None, "") else None,
                force_udp=_parse_bool(force_udp) if force_udp is not None else None,
            )
            return jsonify(result)
        except SyncError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

    @app.post("/api/device/connect")
    def api_connect_device():
        payload = request.get_json(silent=True) or {}
        host = (payload.get("host") or "").strip()
        port = payload.get("port")
        password = payload.get("password", 0)
        timeout = payload.get("timeout", 5)
        force_udp = payload.get("force_udp", False)

        if not host:
            return jsonify({"status": "error", "message": "Địa chỉ IP không được bỏ trống."}), 400
        try:
            port_int = int(port)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Port phải là số."}), 400

        try:
            test_connection(
                host=host,
                port=port_int,
                password=int(password) if password not in (None, "") else 0,
                timeout=int(timeout) if timeout not in (None, "") else 5,
                force_udp=_parse_bool(force_udp),
            )
        except SyncError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

        force_udp_flag = _parse_bool(force_udp)
        session["device_config"] = {
            "host": host,
            "port": port_int,
            "password": int(password) if password not in (None, "") else 0,
            "timeout": int(timeout) if timeout not in (None, "") else 5,
            "force_udp": force_udp_flag,
        }
        session.modified = True

        return jsonify({"status": "ok", "config": session["device_config"]})

    @app.post("/api/device/disconnect")
    def api_disconnect_device():
        session.pop("device_config", None)
        return jsonify({"status": "ok"})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=False)
