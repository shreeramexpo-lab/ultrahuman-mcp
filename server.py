"""Ultrahuman Ring -> Claude MCP connector (read-only, single user).

Env vars (set on the host, never commit them):
  UH_TOKEN        Ultrahuman personal API token (required)
  UH_EMAIL        Ultrahuman account email (required by the API)
  MCP_SECRET_PATH Random string; the MCP URL becomes /<MCP_SECRET_PATH>/mcp
  PORT            Port to listen on (default 8000)
"""

import os
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = "https://partner.ultrahuman.com/api/v1/metrics"
IST = ZoneInfo("Asia/Kolkata")
MAX_RANGE_DAYS = 31
CACHE_TTL_SECONDS = 3600

TOKEN = os.environ.get("UH_TOKEN", "").strip()
EMAIL = os.environ.get("UH_EMAIL", "").strip()
SECRET_PATH = os.environ.get("MCP_SECRET_PATH", "").strip("/ ")
PORT = int(os.environ.get("PORT", "8000"))

if not TOKEN:
    raise SystemExit("UH_TOKEN is not set")
if not EMAIL:
    raise SystemExit("UH_EMAIL is not set")
if len(SECRET_PATH) < 24:
    raise SystemExit("MCP_SECRET_PATH must be a random string of 24+ characters")

mcp = FastMCP(
    "ultrahuman-ring",
    host="0.0.0.0",
    port=PORT,
    streamable_http_path=f"/{SECRET_PATH}/mcp",
)

_cache: dict[str, tuple[float, dict]] = {}


def _today_ist() -> date:
    return datetime.now(IST).date()


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    v = value.strip().lower()
    if v in ("today", "aaj"):
        return _today_ist()
    if v in ("yesterday", "kal"):
        return _today_ist() - timedelta(days=1)
    return date.fromisoformat(value.strip())


def _fetch_day(day: date) -> dict:
    key = day.isoformat()
    hit = _cache.get(key)
    # Today's data keeps changing, so only cache finished days.
    if hit and day < _today_ist() and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    params = {"email": EMAIL, "date": key}
    try:
        r = httpx.get(
            BASE_URL,
            params=params,
            headers={"Authorization": TOKEN},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        return {"date": key, "error": f"network error: {type(exc).__name__}"}

    # Log only status, never health values or the token.
    print(f"[uh] {key} -> {r.status_code}", flush=True)

    if r.status_code == 401:
        return {"date": key, "error": "Ultrahuman token invalid or expired (401)"}
    if r.status_code == 404:
        return {"date": key, "data": None, "note": "no data"}
    if r.status_code >= 400:
        return {"date": key, "error": f"Ultrahuman API error {r.status_code}"}

    body = r.json()
    result = {"date": key, "timezone": "Asia/Kolkata", "data": body}
    _cache[key] = (time.time(), result)
    return result


SERIES = ("hr", "temp", "hrv", "steps")
SLEEP_MINUTE_FIELDS = ("total_sleep", "time_in_bed", "deep_sleep", "rem_sleep", "light_sleep")


def _ist_time(ts):
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d %H:%M")


def _series_stats(obj: dict) -> dict:
    vals = [v.get("value") for v in obj.get("values") or [] if isinstance(v.get("value"), (int, float))]
    out = {"unit": obj.get("unit")}
    if vals:
        out.update(
            readings=len(vals),
            avg=round(sum(vals) / len(vals), 1),
            min=min(vals),
            max=max(vals),
        )
    for k in ("avg", "total", "last_reading", "subtitle", "trend_title", "trend_direction"):
        if obj.get(k) is not None:
            out["api_" + k if k == "avg" else k] = obj[k]
    return {k: v for k, v in out.items() if v is not None}


def _sleep_summary(o: dict) -> dict:
    out = {
        "bedtime_start": _ist_time(o.get("bedtime_start")),
        "bedtime_end": _ist_time(o.get("bedtime_end")),
        "sleep_score": (o.get("sleep_score") or {}).get("score"),
        "sleep_efficiency_pct": (o.get("sleep_efficiency") or {}).get("percentage"),
        "restorative_sleep_pct": (o.get("restorative_sleep") or {}).get("percentage"),
        "temperature_deviation_c": (o.get("temperature_deviation") or {}).get("celsius"),
        "average_body_temperature_c": (o.get("average_body_temperature") or {}).get("celsius"),
        "hr_drop_time": (o.get("hr_drop") or {}).get("time"),
        "full_sleep_cycles": (o.get("full_sleep_cycles") or {}).get("cycles"),
        "tosses_and_turns": (o.get("tosses_and_turns") or {}).get("count"),
        "morning_alertness_min": (o.get("morning_alertness") or {}).get("minutes"),
        "spo2": (o.get("spo2") or {}).get("value"),
    }
    for f in SLEEP_MINUTE_FIELDS:
        m = (o.get(f) or {}).get("minutes")
        if m is not None:
            out[f + "_min"] = m
    stages = [
        {"stage": st.get("title"), "pct": st.get("percentage"), "time": st.get("stage_time_text")}
        for st in o.get("sleep_stages") or []
    ]
    if stages:
        out["stages"] = stages
    summary = [
        {"factor": x.get("title"), "state": x.get("state_title"), "score": x.get("score")}
        for x in o.get("summary") or []
    ]
    if summary:
        out["contributors"] = summary
    if o.get("score_trend"):
        out["score_trend"] = o["score_trend"]
    return {k: v for k, v in out.items() if v not in (None, [], {})}


def _summarize(res: dict) -> dict:
    if "error" in res or res.get("data") is None:
        return res
    body = res["data"]
    metrics = ((body.get("data") or {}).get("metric_data")) or []
    out = {"date": res["date"], "timezone": "Asia/Kolkata"}
    last = (body.get("data") or {}).get("last_synced_at")
    if last:
        out["ring_last_synced"] = _ist_time(last)
    for m in metrics:
        t, o = m.get("type"), m.get("object") or {}
        if t in SERIES:
            out[t] = _series_stats(o)
        elif t == "Sleep":
            out["sleep"] = _sleep_summary(o)
        elif t == "night_rhr":
            out["night_rhr"] = {k: o.get(k) for k in ("avg", "subtitle") if o.get(k) is not None}
        elif isinstance(o, dict) and "value" in o:
            if o["value"] is not None:  # skip empty CGM fields
                out[t] = o["value"] if not o.get("unit") else {"value": o["value"], "unit": o["unit"], "status": o.get("status")}
    if len(out) <= 3:
        out["note"] = "no data"
    return out


def _hourly(res: dict, metric: str) -> dict:
    if "error" in res or res.get("data") is None:
        return res
    metrics = ((res["data"].get("data") or {}).get("metric_data")) or []
    obj = next((m.get("object") or {} for m in metrics if m.get("type") == metric), None)
    if obj is None:
        return {"date": res["date"], "metric": metric, "note": "no data"}
    buckets: dict[str, list] = {}
    for v in obj.get("values") or []:
        if isinstance(v.get("value"), (int, float)) and v.get("timestamp"):
            h = datetime.fromtimestamp(v["timestamp"], IST).strftime("%H:00")
            buckets.setdefault(h, []).append(v["value"])
    agg = "sum" if metric == "steps" else "avg"
    rows = [
        {"hour": h, agg: round(sum(x), 1) if agg == "sum" else round(sum(x) / len(x), 1)}
        for h, x in sorted(buckets.items())
    ]
    return {"date": res["date"], "metric": metric, "unit": obj.get("unit"), "hourly": rows}


@mcp.tool()
def get_daily_metrics(day: str = "yesterday") -> dict:
    """Ankit's own Ultrahuman Ring summary for one day: sleep (score, total,
    deep/REM/light, efficiency, bedtime), HRV, resting heart rate, heart rate,
    steps, temperature, recovery index, movement index, active minutes,
    VO2 max. `day` = YYYY-MM-DD, 'today' or 'yesterday' (Asia/Kolkata).
    Use 'yesterday' for last night's sleep."""
    return _summarize(_fetch_day(_parse_date(day, _today_ist() - timedelta(days=1))))


@mcp.tool()
def get_metrics_range(start_date: str, end_date: str = "today") -> dict:
    """Ankit's own Ultrahuman Ring daily summaries for every day in a range
    (max 31 days), for trends such as 'last 7 days HRV' or 'this month's
    sleep'. Dates are YYYY-MM-DD, 'today' or 'yesterday' (Asia/Kolkata).
    Days with no data (ring not worn or charging) say 'no data', never zero."""
    end = _parse_date(end_date, _today_ist())
    start = _parse_date(start_date, end - timedelta(days=6))
    if start > end:
        start, end = end, start
    days = (end - start).days + 1
    if days > MAX_RANGE_DAYS:
        return {"error": f"range is {days} days; max is {MAX_RANGE_DAYS}"}
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days": [_summarize(_fetch_day(start + timedelta(days=i))) for i in range(days)],
    }


@mcp.tool()
def get_hourly(metric: str = "hr", day: str = "today") -> dict:
    """Hour-by-hour view of one ring metric for a day: metric = 'hr' (heart
    rate avg), 'hrv' (avg), 'temp' (avg) or 'steps' (sum per hour).
    `day` = YYYY-MM-DD, 'today' or 'yesterday' (Asia/Kolkata)."""
    if metric not in SERIES:
        return {"error": f"metric must be one of {list(SERIES)}"}
    return _hourly(_fetch_day(_parse_date(day, _today_ist())), metric)


@mcp.tool()
def check_connection() -> dict:
    """Checks that the Ultrahuman token works by fetching yesterday's data.
    Returns only ok/error, no health values."""
    res = _fetch_day(_today_ist() - timedelta(days=1))
    if "error" in res:
        return {"ok": False, "error": res["error"]}
    return {"ok": True, "has_data": res.get("data") is not None}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
