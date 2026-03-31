from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from publish.read_daily_log import DailyLogSummary, read_daily_log
from publish.render_weekly_mail import render_weekly_mail
from publish.send_mail import InlineImage, MailConfig, send_mail
from publish.weekly_graphs import GraphImage, build_weekly_graphs
from scripts.daily_job import WORKER_ENDPOINTS, build_worker_url
from scripts.openai_chat_utils import chat_completion

JST = ZoneInfo("Asia/Tokyo")
TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


@dataclass(frozen=True)
class WeeklyWindow:
    start: datetime
    end: datetime
    dates: list[str]
    label: str


def is_weekly_enabled() -> bool:
    return os.getenv("WEEKLY_REPORT_ENABLED", "").strip().lower() in TRUE_VALUES


def get_weekly_send_hour_jst() -> int:
    raw = os.getenv("WEEKLY_REPORT_SEND_HOUR_JST", "").strip()
    if not raw:
        return 21
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("WEEKLY_REPORT_SEND_HOUR_JST must be integer") from exc
    if value < 0 or value > 23:
        raise RuntimeError("WEEKLY_REPORT_SEND_HOUR_JST must be 0-23")
    return value


def should_send_now(*, now: Optional[datetime] = None) -> tuple[bool, str]:
    now = now or datetime.now(JST)
    if not is_weekly_enabled():
        return False, "weekly_disabled_by_env"
    send_hour = get_weekly_send_hour_jst()
    if now.weekday() != 6:
        return False, f"not_sunday_jst:{now.strftime('%Y-%m-%d')}"
    if now.hour != send_hour or now.minute != 0:
        return False, f"outside_send_time_jst:expected={send_hour:02d}:00 actual={now.strftime('%H:%M')}"
    return True, "send_allowed"


def compute_weekly_window(*, now: Optional[datetime] = None) -> WeeklyWindow:
    now = now or datetime.now(JST)
    sunday = now.date() - timedelta(days=(now.weekday() - 6) % 7)
    end = datetime.combine(sunday, time(4, 59, 59), tzinfo=JST)
    start = end - timedelta(days=5, hours=23, minutes=59, seconds=59)
    monday = sunday - timedelta(days=6)
    dates = [(monday + timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(6)]
    return WeeklyWindow(start=start, end=end, dates=dates, label=f"{dates[0]}〜{dates[-1]} (JST)")


def _mood_to_score(value: str | None) -> Optional[float]:
    if not value:
        return None
    return float(value.count("★")) if "★" in value else None


def _avg(values: list[Optional[float]]) -> Optional[float]:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def _sum(values: list[Optional[float]]) -> float:
    return round(sum(v or 0 for v in values), 2)


def _collect(dates: list[str], read_url: str, bearer: str | None) -> list[DailyLogSummary]:
    rows: list[DailyLogSummary] = []
    for d in dates:
        s = read_daily_log(daily_log_read_url=read_url, target_date=d, bearer_token=bearer)
        if s:
            rows.append(s)
    return rows


def _metrics(rows: list[DailyLogSummary]) -> dict[str, object]:
    sleep_hours = [round((r.resolved_sleep_duration_min or 0) / 60, 2) if r.resolved_sleep_duration_min is not None else None for r in rows]
    sleep_score = [r.sleep_score for r in rows]
    mood = [_mood_to_score(r.mood) for r in rows]
    expenses = [r.expenses_total for r in rows]
    done = [float(r.done_count or 0) for r in rows]
    drop = [float(r.drop_count or 0) for r in rows]
    weight = [r.weight for r in rows]  # Daily Log only
    f_days = sum(1 for r in rows if (r.expense_f_count or 0) > 0)
    exercise_count = sum(1 for r in rows if any(x in (r.notes or "").lower() for x in ["gym", "ジム", "運動", "workout"]))
    return {
        "sleep_hours": sleep_hours,
        "sleep_score": sleep_score,
        "mood": mood,
        "expenses": expenses,
        "done": done,
        "drop": drop,
        "weight": weight,
        "f_days": f_days,
        "exercise_count": exercise_count,
        "weight_data_days": sum(1 for w in weight if w is not None),
        "weight_insufficient": sum(1 for w in weight if w is not None) < 3,
        "summary": {
            "平均睡眠時間": _avg(sleep_hours),
            "平均 Sleep Score": _avg(sleep_score),
            "平均 mood": _avg(mood),
            "支出合計": _sum(expenses),
            "F発生日数": f_days,
            "Done 合計": int(_sum(done)),
            "Drop 合計": int(_sum(drop)),
            "ジム/運動回数": exercise_count,
            "体重週平均": _avg(weight),
        },
    }


def _diff(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev is None:
        return None
    return round(cur - prev, 2)


def _build_llm_sections(*, current: dict[str, object], previous: dict[str, object], daily_rows: list[DailyLogSummary], model: str) -> dict[str, str]:
    prompt_data = {
        "current_summary": current["summary"],
        "previous_summary": previous["summary"],
        "anomaly_flags": _detect_anomalies(current=current, previous=previous),
        "daily_logs": [{"date": r.target_date, "notes": r.notes, "diary": r.diary, "mood": r.mood} for r in daily_rows],
        "constraints": {
            "do_not_mix_fact_and_inference": True,
            "no_causal_claims": True,
            "weight_no_medical_judgement": True,
            "actions_max": 3,
            "show_f_risk_only_when_exists": bool(current.get("f_days", 0)),
        },
    }
    system = "あなたは週次レポート作成アシスタント。観測事実と推測を分離し、因果を断定しない。"
    user = (
        "次のJSONから、summary/good_points/alerts/patterns/actions/daily_digest の6キーを持つJSONのみ返してください。"
        "alerts は異常検知を自然文説明し、actionsは最大3件。"
        f"\n\n{prompt_data}"
    )
    response = chat_completion(model=model, system_prompt=system, user_prompt=user, temperature=0.2)
    import json

    parsed = json.loads(response)
    required = ["summary", "good_points", "alerts", "patterns", "actions", "daily_digest"]
    for key in required:
        if key not in parsed:
            raise RuntimeError(f"weekly llm output missing: {key}")
    return {k: str(parsed[k]) for k in required}


def _detect_anomalies(*, current: dict[str, object], previous: dict[str, object]) -> list[str]:
    c = current["summary"]
    p = previous["summary"]
    flags: list[str] = []
    sleep_delta = _diff(c.get("平均睡眠時間"), p.get("平均睡眠時間"))
    mood_delta = _diff(c.get("平均 mood"), p.get("平均 mood"))
    if sleep_delta is not None and sleep_delta <= -1.0:
        flags.append("平均睡眠時間が前週比で1時間以上低下")
    if mood_delta is not None and mood_delta <= -0.8:
        flags.append("平均moodが前週比で0.8以上低下")
    prev_spend = p.get("支出合計")
    cur_spend = c.get("支出合計")
    if isinstance(prev_spend, (int, float)) and isinstance(cur_spend, (int, float)) and prev_spend > 0 and cur_spend >= prev_spend * 1.5:
        flags.append("支出合計が前週比150%以上")
    if not flags:
        flags.append("顕著な閾値超過は検知されませんでした")
    return flags


def build_weekly_payload(*, current_rows: list[DailyLogSummary], previous_rows: list[DailyLogSummary], period_label: str, model: str) -> tuple[dict[str, object], list[GraphImage]]:
    current = _metrics(current_rows)
    previous = _metrics(previous_rows)
    current_summary = dict(current["summary"])
    prev_summary = previous["summary"]
    current_summary["前週差分"] = {
        "平均睡眠時間": _diff(current_summary.get("平均睡眠時間"), prev_summary.get("平均睡眠時間")),
        "平均 Sleep Score": _diff(current_summary.get("平均 Sleep Score"), prev_summary.get("平均 Sleep Score")),
        "平均 mood": _diff(current_summary.get("平均 mood"), prev_summary.get("平均 mood")),
        "支出合計": _diff(current_summary.get("支出合計"), prev_summary.get("支出合計")),
        "体重週平均": _diff(current_summary.get("体重週平均"), prev_summary.get("体重週平均")),
    }
    graphs = build_weekly_graphs(labels=[r.target_date[5:] for r in current_rows], metrics=current)
    graph_desc = [
        "睡眠: 平均/前週差を併記",
        "mood: 低調日注記(値<=2.0)",
        f"支出: 合計={current_summary['支出合計']} F発生日数={current['f_days']}",
        "Done/Drop: 日別件数",
        "体重: Daily Log Weightのみ参照",
    ]
    if current.get("weight_insufficient"):
        graph_desc[-1] = "体重: 記録不足（3日未満のためグラフ非表示）"
        graphs = [g for g in graphs if g.cid != "weekly-weight"]
    llm_sections = _build_llm_sections(current=current, previous=previous, daily_rows=current_rows, model=model)
    payload = {
        "period_label": period_label,
        "key_metrics": current_summary,
        "graph_descriptions": graph_desc,
        "llm_sections": llm_sections,
    }
    return payload, graphs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    now = datetime.now(JST)
    allowed, reason = should_send_now(now=now)
    if (not allowed) and (not args.force):
        logging.info("weekly_report skipped: %s", reason)
        return 0

    upsert_url = os.getenv("DAILY_LOG_UPSERT_URL", "").strip()
    if not upsert_url:
        raise RuntimeError("Missing env var: DAILY_LOG_UPSERT_URL")
    read_url = build_worker_url(upsert_url, WORKER_ENDPOINTS["read"])
    bearer = os.getenv("WORKERS_BEARER_TOKEN")

    window = compute_weekly_window(now=now)
    prev_dates = [(datetime.strptime(d, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d") for d in window.dates]
    current_rows = _collect(window.dates, read_url, bearer)
    previous_rows = _collect(prev_dates, read_url, bearer)
    if not current_rows:
        raise RuntimeError("weekly_report aborted: no daily logs in target week")

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    payload, graphs = build_weekly_payload(current_rows=current_rows, previous_rows=previous_rows, period_label=window.label, model=model)
    mail = render_weekly_mail(payload=payload, graphs=graphs)

    mail_to = [x.strip() for x in os.getenv("MAIL_TO", "").split(",") if x.strip()]
    send_mail(
        MailConfig(
            mail_from=os.getenv("MAIL_FROM", ""),
            mail_to=mail_to,
            gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", ""),
            mail_cc=[x.strip() for x in os.getenv("MAIL_CC", "").split(",") if x.strip()],
            mail_bcc=[x.strip() for x in os.getenv("MAIL_BCC", "").split(",") if x.strip()],
        ),
        mail.subject,
        mail.plain_text,
        mail.html_body,
        inline_images=[InlineImage(cid=g.cid, filename=g.filename, data=g.data) for g in mail.inline_images],
    )
    logging.info("weekly_report sent: %s", window.label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
