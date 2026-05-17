#!/usr/bin/env python3
"""Аудит TaskNotes ↔ Google Tasks связок (ежедневный, через daily_collect.sh).

Запускается каждое утро. Выводит компактный отчёт в stdout, который daily_collect
сохраняет в daily_data/{дата}/tasknotes_audit.txt. Утренний брифинг читает этот
файл — если строка не начинается с 'OK', упоминает в брифе.

Exit code:
    0 — всё связано (для Kuma в будущем, если решим вывести в push monitor)
    1 — обнаружен дрейф (пусть Kuma алёртит)
"""
import sys
import os
import re
import json
import urllib.request
import urllib.parse
import urllib.error
from collections import Counter

sys.path.insert(0, "/volume1/homes/artemere-7601341/scripts")
import _env  # noqa: F401

TOKEN = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_token.json"
CREDS = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json"
VAULT_ROOT = "/volume1/obsidian/TaskNotes"
ACTIVE_LISTS = ["На сегодня", "На завтра", "Недельные планы", "Идеи",
                "Жду ответа", "Финансовые задачи", "Продукты"]


def refresh_token():
    c = json.load(open(CREDS))["installed"]
    t = json.load(open(TOKEN))
    data = urllib.parse.urlencode({
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": t["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(c["token_uri"], data=data, method="POST")
    with urllib.request.urlopen(req) as r:
        new = json.loads(r.read())
    t["access_token"] = new["access_token"]
    json.dump(t, open(TOKEN, "w"), indent=2)
    return t["access_token"]


def at():
    return json.load(open(TOKEN))["access_token"]


def gapi(path):
    url = "https://tasks.googleapis.com/tasks/v1" + path
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + at()})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                refresh_token()
                continue
            raise


def parse_fm(text):
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    fm = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^([\w_]+)\s*:\s*(.*)$", line)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            fm[k] = v
    return fm


def main():
    vault_tasks = []
    no_gid = []
    for folder in ACTIVE_LISTS:
        d = os.path.join(VAULT_ROOT, folder)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.endswith(".md"):
                continue
            path = os.path.join(d, f)
            try:
                text = open(path, encoding="utf-8").read()
                fm = parse_fm(text)
                gid = fm.get("google_task_id", "").strip()
                title = fm.get("title", "").strip() or f[:-3]
                if gid:
                    vault_tasks.append((folder, f, gid, title))
                else:
                    no_gid.append((folder, f, title))
            except Exception as e:
                print(f"WARN parse {path}: {e}", file=sys.stderr)

    google_tasks = {}
    lists = gapi("/users/@me/lists?maxResults=100").get("items", [])
    for L in lists:
        if L["title"] not in ACTIVE_LISTS:
            continue
        res = gapi(f"/lists/{L['id']}/tasks?maxResults=100&showCompleted=false&showHidden=false")
        for t in res.get("items", []):
            google_tasks[t["id"]] = {
                "list_name": L["title"],
                "title": t.get("title", ""),
                "status": t.get("status", "needsAction"),
            }

    vault_gids = {gid for _, _, gid, _ in vault_tasks}
    google_gids = set(google_tasks.keys())

    dead = [(f, fn, gid, t) for f, fn, gid, t in vault_tasks if gid not in google_gids]
    wrong_list = []
    for folder, fn, gid, title in vault_tasks:
        if gid in google_gids and google_tasks[gid]["list_name"] != folder:
            wrong_list.append((folder, fn, gid, title, google_tasks[gid]["list_name"]))
    orphaned = [(gid, info) for gid, info in google_tasks.items() if gid not in vault_gids]
    gid_counts = Counter(gid for _, _, gid, _ in vault_tasks)
    dups = [(gid, c) for gid, c in gid_counts.items() if c > 1]

    issues = len(dead) + len(wrong_list) + len(orphaned) + len(no_gid) + len(dups)

    if issues == 0:
        print(f"OK: {len(vault_tasks)} vault ↔ {len(google_gids)} google tasks связаны 1:1 "
              f"по 7 активным спискам (на {os.popen('date +%F').read().strip()})")
        sys.exit(0)

    print(f"DRIFT: {issues} проблем в связке TaskNotes ↔ Google Tasks "
          f"(vault {len(vault_tasks)} с gid + {len(no_gid)} без gid, google {len(google_gids)})")

    if dead:
        print(f"\n[A] Vault → Google id умер ({len(dead)}):")
        for f, fn, gid, t in dead[:10]:
            print(f"  [{f}] {fn[:70]}  gid={gid[:20]}…")
        if len(dead) > 10:
            print(f"  ... ещё {len(dead) - 10}")
    if wrong_list:
        print(f"\n[B] Vault папка ≠ Google list ({len(wrong_list)}):")
        for f, fn, gid, t, g_list in wrong_list[:10]:
            print(f"  vault='{f}' vs google='{g_list}'  | {fn[:60]}")
    if orphaned:
        print(f"\n[C] Google task без vault файла ({len(orphaned)}):")
        for gid, info in orphaned[:10]:
            print(f"  [{info['list_name']}] '{info['title'][:60]}'  gid={gid[:20]}…")
    if no_gid:
        print(f"\n[D] Vault task без google_task_id ({len(no_gid)}):")
        for f, fn, t in no_gid[:10]:
            print(f"  [{f}] {fn[:70]}")
    if dups:
        print(f"\n[E] Дубли gid в vault ({len(dups)}):")
        for gid, c in dups[:10]:
            print(f"  gid={gid[:20]}…  count={c}")

    sys.exit(1)


if __name__ == "__main__":
    main()
