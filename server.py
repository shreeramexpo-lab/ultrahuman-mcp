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


@mcp.tool()
def get_daily_metrics(day: str = "yesterday") -> dict:
    """Ankit's own Ultrahuman Ring data for one day: sleep, HRV, resting heart
    rate, heart rate, steps, temperature, recovery index, movement index,
    VO2 max, metabolic score (glucose fields only if a CGM is used). `day` = YYYY-MM-DD, 'today' or 'yesterday'
    (Asia/Kolkata). Use 'yesterday' for last night's sleep."""
    return _fetch_day(_parse_date(day, _today_ist() - timedelta(days=1)))


@mcp.tool()
def get_metrics_range(start_date: str, end_date: str = "today") -> dict:
    """Ankit's own Ultrahuman Ring data for every day in a range (max 31 days),
    for trends such as 'last 7 days HRV' or 'this month's sleep'. Dates are
    YYYY-MM-DD, 'today' or 'yesterday' (Asia/Kolkata). Days with no data
    (ring not worn or charging) come back as data=null, never zero."""
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
        "days": [_fetch_day(start + timedelta(days=i)) for i in range(days)],
    }


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
