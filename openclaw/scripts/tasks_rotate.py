#!/usr/bin/env python3
"""Утренний автоматический перенос задач в Google Tasks.

Логика:
1. Читает список "На сегодня". Если непусто - это незакрытые с прошлого дня.
   Сохраняет их в daily_data/YYYY-MM-DD/tasks_leftover.txt и пишет предупреждение в лог.
2. Берёт все задачи из "На завтра", переносит в "На сегодня" (insert + delete).
3. Сохраняет финальный список "На сегодня" (после переноса) в tasks_today.txt.

ID списков:
  TODAY = V0xnRHpSN0taU2ppMHJnOQ
  TOMORROW = M1FJeEJ0WWxxWDctNlEyVA
"""
import json
import urllib.request
import urllib.parse
import urllib.error
import sys
from datetime import date
from pathlib import Path

CREDENTIALS_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json"
TOKEN_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_token.json"
BASE_URL = "https://tasks.googleapis.com/tasks/v1"
DATA_ROOT = Path("/volume1/homes/artemere-7601341/scripts/daily_data")

LIST_TODAY = "V0xnRHpSN0taU2ppMHJnOQ"
LIST_TOMORROW = "M1FJeEJ0WWxxWDctNlEyVA"


def refresh_token():
    with open(CREDENTIALS_FILE) as f:
        creds = json.load(f)["installed"]
    with open(TOKEN_FILE) as f:
        token = json.load(f)
    data = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": token["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(creds["token_uri"], data=data, method="POST")
    with urllib.request.urlopen(req) as r:
        new = json.loads(r.read())
    token["access_token"] = new["access_token"]
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)
    return token["access_token"]


def access_token():
    return json.load(open(TOKEN_FILE))["access_token"]


def api(path, method="GET", body=None):
    token = access_token()
    url = BASE_URL + path
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": "Bearer " + token}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            if r.status == 204:
                return {}
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            t = refresh_token()
            req.headers["Authorization"] = "Bearer " + t
            with urllib.request.urlopen(req) as r:
                if r.status == 204:
                    return {}
                return json.loads(r.read())
        raise


def list_tasks(list_id):
    items = []
    page_token = None
    while True:
        params = {"showCompleted": "false", "maxResults": "100"}
        if page_token:
            params["pageToken"] = page_token
        r = api("/lists/" + list_id + "/tasks?" + urllib.parse.urlencode(params))
        items.extend(r.get("items", []))
        page_token = r.get("nextPageToken")
        if not page_token:
            break
    return items


def insert_task(list_id, title, notes=None, due=None):
    body = {"title": title}
    if notes:
        body["notes"] = notes
    if due:
        body["due"] = due
    return api("/lists/" + list_id + "/tasks", method="POST", body=body)


def delete_task(list_id, task_id):
    api("/lists/" + list_id + "/tasks/" + task_id, method="DELETE")


def fmt_task(t):
    line = "  ⬜ " + t.get("title", "")
    if t.get("notes"):
        first = t["notes"].split(chr(10))[0]
        line += " — " + first[:80]
    if t.get("due"):
        line += "  [due " + t["due"][:10] + "]"
    return line


def main():
    today = date.today().isoformat()
    out_dir = DATA_ROOT / today
    out_dir.mkdir(parents=True, exist_ok=True)

    log = []
    log.append("=== tasks_rotate " + today + " ===")

    leftover = list_tasks(LIST_TODAY)
    log.append("На сегодня перед переносом: " + str(len(leftover)) + " задач")

    leftover_path = out_dir / "tasks_leftover.txt"
    if leftover:
        with open(leftover_path, "w", encoding="utf-8") as f:
            f.write("# Незакрытые задачи из 'На сегодня' (вчерашние, не перенесённые)" + chr(10))
            for t in leftover:
                f.write(fmt_task(t) + chr(10))
        log.append("WARN: leftover saved to " + str(leftover_path))
    else:
        if leftover_path.exists():
            leftover_path.unlink()
        log.append("OK: 'На сегодня' был пустой на утро")

    tomorrow = list_tasks(LIST_TOMORROW)
    log.append("На завтра к переносу: " + str(len(tomorrow)) + " задач")

    moved = 0
    failed = []
    for t in tomorrow:
        try:
            insert_task(
                LIST_TODAY,
                title=t.get("title", ""),
                notes=t.get("notes"),
                due=t.get("due"),
            )
            delete_task(LIST_TOMORROW, t["id"])
            moved += 1
        except Exception as e:
            failed.append((t.get("title", "?"), str(e)))
    log.append("Перенесено: " + str(moved) + ", ошибок: " + str(len(failed)))
    for title, err in failed:
        log.append("  FAIL '" + title + "': " + err)

    today_now = list_tasks(LIST_TODAY)
    today_path = out_dir / "tasks_today.txt"
    with open(today_path, "w", encoding="utf-8") as f:
        if today_now:
            for t in today_now:
                f.write(fmt_task(t) + chr(10))
        else:
            f.write("(пусто)" + chr(10))
    log.append("Финал 'На сегодня': " + str(len(today_now)) + " задач, сохранён в " + str(today_path))

    rotate_log = out_dir / "tasks_rotate.log"
    with open(rotate_log, "w", encoding="utf-8") as f:
        f.write(chr(10).join(log) + chr(10))

    print(chr(10).join(log))


if __name__ == "__main__":
    main()
