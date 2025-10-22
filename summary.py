from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

import pandas as pd

from db import fetch_attendance_rows, fetch_users, fetch_makeup_hours

WORK_START = time(8, 0)
WORK_LUNCH_START = time(12, 0)
WORK_AFTERNOON_START = time(13, 0)
WORK_END = time(17, 0)
LUNCH_BREAK_SECONDS = 3600


@dataclass
class SummaryFilters:
    user_id: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


def _load_dataframe(filters: SummaryFilters) -> pd.DataFrame:
    rows = fetch_attendance_rows(
        user_id=filters.user_id,
        start_date=filters.start_date,
        end_date=filters.end_date,
    )
    if not rows:
        return pd.DataFrame(columns=["user_id", "timestamp", "status"])

    df = pd.DataFrame(rows, columns=["user_id", "timestamp", "status"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date
    return df


def _calculate_metrics(user_id: int, date_value, group: pd.DataFrame) -> dict:
    events = group.sort_values("timestamp")
    timestamps = [row["timestamp"].to_pydatetime() for _, row in events.iterrows()]
    if not timestamps:
        return {
            "user_id": int(user_id),
            "date": date_value.isoformat(),
            "check_in": None,
            "check_out": None,
            "working_hours": None,
            "late_mins": 0,
            "early_leave_mins": 0,
        }

    check_in_ts = min(timestamps) if timestamps else None
    check_out_ts = max(timestamps) if timestamps else None

    if check_in_ts and check_in_ts.time() >= WORK_LUNCH_START:
        am_candidates = [ts for ts in timestamps if ts.time() < WORK_LUNCH_START]
        check_in_ts = min(am_candidates) if am_candidates else None

    if check_out_ts and check_out_ts.time() < WORK_LUNCH_START:
        pm_candidates = [ts for ts in timestamps if ts.time() >= WORK_AFTERNOON_START]
        check_out_ts = max(pm_candidates) if pm_candidates else None

    if check_in_ts and check_out_ts and check_in_ts > check_out_ts:
        check_in_ts, check_out_ts = check_out_ts, check_in_ts

    work_hours = None
    if check_in_ts and check_out_ts and check_out_ts > check_in_ts:
        work_seconds = int((check_out_ts - check_in_ts).total_seconds())
        if (
            check_in_ts.time() <= WORK_LUNCH_START
            and check_out_ts.time() >= WORK_AFTERNOON_START
            and work_seconds > 6 * 3600
        ):
            work_seconds -= LUNCH_BREAK_SECONDS
        work_hours = round(max(0, work_seconds) / 3600, 2)

    work_start_dt = datetime.combine(date_value, WORK_START)
    work_end_dt = datetime.combine(date_value, WORK_END)

    late_minutes = (
        max(0, int(round((check_in_ts - work_start_dt).total_seconds() / 60)))
        if check_in_ts
        else 0
    )
    early_leave_minutes = (
        max(0, int(round((work_end_dt - check_out_ts).total_seconds() / 60)))
        if check_out_ts
        else 0
    )

    return {
        "user_id": int(user_id),
        "date": date_value.isoformat(),
        "check_in": check_in_ts.isoformat() if check_in_ts else None,
        "check_out": check_out_ts.isoformat() if check_out_ts else None,
        "working_hours": work_hours,
        "late_mins": late_minutes,
        "early_leave_mins": early_leave_minutes,
    }


def get_daily_summary(
    user_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """Return computed daily attendance summary."""
    filters = SummaryFilters(user_id=user_id, start_date=start_date, end_date=end_date)
    df = _load_dataframe(filters)
    summary_map: dict[tuple[int, datetime.date], dict] = {}

    if not df.empty:
        grouped = df.groupby(["user_id", "date"], sort=True)
        for (user, date_value), group in grouped:
            user_int = int(user)
            metrics = _calculate_metrics(user_int, date_value, group)
            metrics["late_mins"] = int(metrics["late_mins"])
            metrics["early_leave_mins"] = int(metrics["early_leave_mins"])
            if metrics["working_hours"] is not None:
                metrics["working_hours"] = float(metrics["working_hours"])
            metrics["date"] = date_value.isoformat()
            summary_map[(user_int, date_value)] = metrics

    users_rows = fetch_users()
    users_lookup = {row["user_id"]: row for row in users_rows}

    if filters.start_date and filters.end_date:
        date_index = pd.date_range(filters.start_date, filters.end_date, inclusive="both")
    else:
        date_index = pd.DatetimeIndex([])

    makeup_lookup = fetch_makeup_hours(
        user_id=filters.user_id,
        start_date=filters.start_date,
        end_date=filters.end_date,
    )
    for (mu_user, mu_date_str), payload in makeup_lookup.items():
        mu_date = datetime.fromisoformat(mu_date_str).date()
        if (mu_user, mu_date) not in summary_map:
            summary_map[(mu_user, mu_date)] = {
                "user_id": mu_user,
                "date": mu_date.isoformat(),
                "check_in": None,
                "check_out": None,
                "working_hours": None,
                "late_mins": 0,
                "early_leave_mins": 0,
            }

    if not summary_map and not makeup_lookup:
        return []

    active_users = {key[0] for key in summary_map.keys()}
    if filters.user_id is not None:
        active_users &= {int(filters.user_id)}

    if date_index.size and active_users:
        for user in active_users:
            for ts in date_index:
                date_obj = ts.date()
                key = (user, date_obj)
                if key not in summary_map:
                    summary_map[key] = {
                        "user_id": int(user),
                        "date": date_obj.isoformat(),
                        "check_in": None,
                        "check_out": None,
                        "working_hours": None,
                        "late_mins": 0,
                        "early_leave_mins": 0,
                    }

    summary_rows: list[dict] = []
    for user in sorted(active_users):
        user_dates = sorted([key[1] for key in summary_map.keys() if key[0] == user])
        for date_obj in user_dates:
            row = summary_map[(user, date_obj)]
            row["late_mins"] = int(row.get("late_mins", 0))
            row["early_leave_mins"] = int(row.get("early_leave_mins", 0))
            if row.get("working_hours") is not None:
                row["working_hours"] = float(row["working_hours"])

            makeup_key = (row["user_id"], row["date"])
            makeup_data = makeup_lookup.get(makeup_key, {})
            row["makeup_hours"] = float(makeup_data.get("hours", 0.0))
            row["makeup_note"] = makeup_data.get("note")
            total_hours = (row["working_hours"] or 0.0) + row["makeup_hours"]
            row["total_hours"] = round(total_hours, 2) if total_hours else None

            check_in_ts = datetime.fromisoformat(row["check_in"]) if row["check_in"] else None
            row["missing_check_in"] = check_in_ts is None
            check_out_ts = datetime.fromisoformat(row["check_out"]) if row["check_out"] else None
            row["missing_check_out"] = check_out_ts is None
            row["is_day_off"] = row["missing_check_in"] and row["missing_check_out"]

            date_obj = datetime.fromisoformat(row["date"]).date()
            row["weekday"] = date_obj.weekday()
            row["weekday_label"] = date_obj.strftime("%A")
            row["is_weekend"] = row["weekday"] >= 5
            row["worked_on_weekend"] = row["is_weekend"] and not row["is_day_off"]
            if row["weekday"] == 5 and not row["is_day_off"]:
                row["weekend_note"] = "Worked on Saturday"
            else:
                row["weekend_note"] = None

            user_meta = users_lookup.get(row["user_id"])
            row["name"] = user_meta["name"] if user_meta else None
            row["department"] = user_meta["department"] if user_meta else None

            summary_rows.append(row)

    user_activity = {}
    for row in summary_rows:
        user_activity.setdefault(row["user_id"], False)
        if not row["is_day_off"] or (row.get("makeup_hours") or 0) > 0:
            user_activity[row["user_id"]] = True

    filtered_rows = [
        row for row in summary_rows if user_activity.get(row["user_id"], False)
    ]

    return filtered_rows


def summary_dataframe(**kwargs) -> pd.DataFrame:
    """Convenience helper returning a pandas DataFrame version of the summary."""
    records = get_daily_summary(**kwargs)
    if not records:
        return pd.DataFrame(
            columns=[
                "user_id",
                "name",
                "department",
                "date",
                "check_in",
                "check_out",
                "working_hours",
                "late_mins",
                "early_leave_mins",
                "makeup_hours",
                "makeup_note",
                "total_hours",
                "missing_check_in",
                "missing_check_out",
                "is_day_off",
                "weekday",
                "weekday_label",
                "is_weekend",
                "worked_on_weekend",
                "weekend_note",
            ]
        )
    df = pd.DataFrame(records)
    desired_columns = [
        "user_id",
        "name",
        "department",
        "date",
        "check_in",
        "check_out",
        "working_hours",
        "late_mins",
        "early_leave_mins",
        "makeup_hours",
        "makeup_note",
        "total_hours",
        "missing_check_in",
        "missing_check_out",
        "is_day_off",
        "weekday",
        "weekday_label",
        "is_weekend",
        "worked_on_weekend",
        "weekend_note",
    ]
    return df[desired_columns]
