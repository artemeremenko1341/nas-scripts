#!/usr/bin/env python3
"""
Health data pull from Google Fit (Health Sync bridge from Samsung Health + PICOOC).

Pulls yesterday's metrics in MSK:
  - steps         (sum, int)        from raw Samsung Health (com.sec.android.app.shealth)
  - weight_kg     (last value, fp)  from raw PICOOC (com.picooc.international)
  - body_fat_pct  (last value, fp)  from raw PICOOC, else first available
  - sleep_minutes (sum non-awake segments) from Samsung Health, else aggregated
  - hydration_l   (sum, fp)         from Samsung Health, else aggregated

Source preferences (avoid 2x duplication from derived aggregators that double-count):
  - Steps:     prefer raw com.sec.android.app.shealth (Samsung Health) — exact, no dedup needed
  - Weight:    prefer raw com.picooc.international (scale itself writes to Google Fit)
  - Body fat:  prefer raw com.picooc.international
  - Sleep:     prefer raw com.sec.android.app.shealth
  - Hydration: prefer raw com.sec.android.app.shealth

Falls back to derived com.google.android.gms estimated_steps source if preferred not found.

Saves to daily_data/{today}/health.json.

OAuth scope: fitness.{activity,body,sleep,nutrition}.read — added to project just-slate-319910 on 2026-05-19.
"""
import json
import os
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

CRED = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json"
TOK = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_token.json"
DAILY_DATA = "/volume1/homes/artemere-7601341/scripts/daily_data"
MSK = timezone(timedelta(hours=3))
API_BASE = "https://www.googleapis.com/fitness/v1/users/me"
TIMEOUT = 30

# Source preferences per data type. First match wins. None = use any.
SOURCE_PREFS = {
    "com.google.step_count.delta": ["com.sec.android.app.shealth", "com.google.android.fit"],
    "com.google.weight": ["com.picooc.international", "com.google.android.apps.fitness"],
    "com.google.body.fat.percentage": ["com.picooc.international"],
    "com.google.sleep.segment": ["com.sec.android.app.shealth"],
    "com.google.hydration": ["com.sec.android.app.shealth"],
}


def refresh_token():
    c = json.load(open(CRED))["installed"]
    t = json.load(open(TOK))
    data = urllib.parse.urlencode({
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": t["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(c["token_uri"], data=data)
    new = json.loads(urllib.request.urlopen(req).read())
    t["access_token"] = new["access_token"]
    json.dump(t, open(TOK, "w"), indent=2)
    return t["access_token"]


def list_sources(tok):
    """Return list of data source dicts."""
    req = urllib.request.Request(API_BASE + "/dataSources", headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read()).get("dataSource", [])


def pick_source(sources, data_type):
    """Pick best data source ID for given data_type using SOURCE_PREFS."""
    candidates = [s for s in sources if s.get("dataType", {}).get("name") == data_type]
    if not candidates:
        return None
    prefs = SOURCE_PREFS.get(data_type, [])
    # Prefer raw sources by preferred packageName
    for pkg in prefs:
        for s in candidates:
            if s.get("type") == "raw" and s.get("application", {}).get("packageName") == pkg:
                return s["dataStreamId"]
    # Fallback: prefer derived estimated_steps (Google aggregator)
    for s in candidates:
        sid = s.get("dataStreamId", "")
        if "estimated_steps" in sid:
            return sid
    # Fallback: any derived from gms
    for s in candidates:
        if s.get("type") == "derived" and s.get("application", {}).get("packageName") == "com.google.android.gms":
            return s["dataStreamId"]
    # Last resort: first one
    return candidates[0]["dataStreamId"] if candidates else None


def query_source(tok, source_id, day_msk):
    """GET dataset for one source for one MSK-day. Returns list of points."""
    day_end = day_msk + timedelta(days=1)
    start_ns = int(day_msk.astimezone(timezone.utc).timestamp() * 1_000_000_000)
    end_ns = int(day_end.astimezone(timezone.utc).timestamp() * 1_000_000_000)
    url = API_BASE + "/dataSources/" + urllib.parse.quote(source_id, safe="") + "/datasets/" + str(start_ns) + "-" + str(end_ns)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read()).get("point", [])


def _sum_int(points):
    return sum(v.get("intVal", 0) for p in points for v in p.get("value", []))


def _sum_fp(points):
    return sum(v.get("fpVal", 0.0) for p in points for v in p.get("value", []))


def _last_fp(points):
    last_val, last_ts = None, -1
    for p in points:
        ts = int(p.get("endTimeNanos", "0"))
        for v in p.get("value", []):
            if "fpVal" in v and ts > last_ts:
                last_ts, last_val = ts, v["fpVal"]
    return last_val


def _sleep_minutes(points):
    total_ns = 0
    for p in points:
        start_ns = int(p.get("startTimeNanos", "0"))
        end_ns = int(p.get("endTimeNanos", "0"))
        stage = None
        for v in p.get("value", []):
            if "intVal" in v:
                stage = v["intVal"]
        # stage: 1=awake, 2=sleep generic, 3=out-of-bed, 4=light, 5=deep, 6=REM
        if stage is not None and stage not in (1, 3):
            total_ns += (end_ns - start_ns)
    return round(total_ns / 60_000_000_000)


def aggregate_day(tok, data_type, day_msk):
    """Aggregate ALL sources for one data type for one MSK-day."""
    day_end = day_msk + timedelta(days=1)
    start_ms = int(day_msk.astimezone(timezone.utc).timestamp() * 1000)
    end_ms = int(day_end.astimezone(timezone.utc).timestamp() * 1000)
    body = {
        "aggregateBy": [{"dataTypeName": data_type}],
        "bucketByTime": {"durationMillis": 86400000},
        "startTimeMillis": start_ms,
        "endTimeMillis": end_ms,
    }
    req = urllib.request.Request(
        API_BASE + "/dataset:aggregate",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        resp = json.loads(r.read())
    # Flatten to list of points
    points = []
    for b in resp.get("bucket", []):
        for ds in b.get("dataset", []):
            for p in ds.get("point", []):
                points.append(p)
    return points


def collect_day(tok, day_msk):
    sources = list_sources(tok)
    out = {"date": day_msk.strftime("%Y-%m-%d"), "fetched_at": datetime.now(MSK).isoformat()}

    def collect_raw(label, data_type, extractor):
        """Raw single-source pull. Use for high-frequency types prone to derived double-counting (steps, sleep, hydration)."""
        sid = pick_source(sources, data_type)
        if not sid:
            out[label + "_src"] = "none"
            return
        try:
            points = query_source(tok, sid, day_msk)
            src = next((s for s in sources if s.get("dataStreamId") == sid), {})
            out[label + "_src"] = src.get("application", {}).get("packageName", src.get("type", "?"))
            val = extractor(points)
            if val is not None and val != 0:
                out[label] = val
        except urllib.error.HTTPError as e:
            out[label + "_err"] = f"HTTP{e.code}"
        except Exception as e:
            out[label + "_err"] = type(e).__name__

    def collect_aggregate(label, data_type, extractor):
        """Aggregate across all sources. Use for low-frequency types (weight, body fat) where derived correctly merges."""
        try:
            points = aggregate_day(tok, data_type, day_msk)
            out[label + "_src"] = "aggregate"
            val = extractor(points)
            if val is not None and val != 0:
                out[label] = val
        except urllib.error.HTTPError as e:
            out[label + "_err"] = f"HTTP{e.code}"
        except Exception as e:
            out[label + "_err"] = type(e).__name__

    collect_raw("steps", "com.google.step_count.delta", _sum_int)
    collect_aggregate("weight_kg", "com.google.weight", lambda pts: round(v, 1) if (v := _last_fp(pts)) is not None else None)
    collect_aggregate("body_fat_pct", "com.google.body.fat.percentage", lambda pts: round(v, 1) if (v := _last_fp(pts)) is not None else None)
    collect_raw("sleep_minutes", "com.google.sleep.segment", _sleep_minutes)
    collect_raw("hydration_l", "com.google.hydration", lambda pts: round(_sum_fp(pts), 2) if _sum_fp(pts) else None)

    return out


def parse_target_date(argv):
    now = datetime.now(MSK)
    if len(argv) > 1:
        try:
            y, m, d = map(int, argv[1].split("-"))
            return datetime(y, m, d, 0, 0, 0, tzinfo=MSK)
        except Exception:
            print(f"ERROR: bad date arg '{argv[1]}', expected YYYY-MM-DD", file=sys.stderr)
            sys.exit(2)
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def main():
    target = parse_target_date(sys.argv)
    tok = refresh_token()
    data = collect_day(tok, target)
    today_str = datetime.now(MSK).strftime("%Y-%m-%d")
    out_dir = Path(DAILY_DATA) / today_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "health.json"
    json.dump(data, open(out_path, "w"), ensure_ascii=False, indent=2)
    print(f"OK: {out_path}")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
