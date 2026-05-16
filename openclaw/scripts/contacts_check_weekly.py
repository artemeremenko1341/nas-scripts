"""Weekly contacts check — find vault cards missing in Google Contacts.

Запускается из DSM cron раз в неделю (понедельник 03:45 МСК).
Сканирует vault карточки в `2 База знаний/` и `1 Входящие/` с полями
контактный_телефон/email/дата_рождения, сравнивает с Google People API,
сохраняет дифф в daily/{дата}/contacts_check.json для утреннего брифа.
"""
import json
import re
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

CRED = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json"
TOK = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_token.json"

VAULT_DIRS = [
    Path("/volume1/obsidian/2 База знаний"),
    Path("/volume1/obsidian/1 Входящие"),
]
DATA_DIR = Path("/volume1/homes/artemere-7601341/scripts/openclaw/data/reviews")
DAILY_DIR_ROOT = DATA_DIR / "daily"
LOG_PATH = DATA_DIR / "contacts_check.log"

PHONE_KEYS = ["контактный_телефон", "телефон", "phone"]
EMAIL_KEYS = ["email", "почта"]
BDAY_KEYS = ["дата_рождения", "birthday"]
ALL_CONTACT_KEYS = set(PHONE_KEYS + EMAIL_KEYS + BDAY_KEYS)


def _refresh():
    c = json.load(open(CRED))["installed"]
    t = json.load(open(TOK))
    data = urllib.parse.urlencode({
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": t["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(c["token_uri"], data=data)
    with urllib.request.urlopen(req) as r:
        new = json.loads(r.read())
    t["access_token"] = new["access_token"]
    json.dump(t, open(TOK, "w"), indent=2)
    return t["access_token"]


def gapi(path, method="GET", retried=False):
    tok = json.load(open(TOK))["access_token"]
    url = "https://people.googleapis.com/v1/" + path
    req = urllib.request.Request(url, method=method,
                                  headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 401 and not retried:
            _refresh()
            return gapi(path, method, True)
        raise


CULTURAL_NAME_KW = ["(писатель)", "(режиссер)", "(режиссёр)", "(композитор)", "(поэт)"]
CULTURAL_POS_KW = ["режиссёр", "режиссер", "писатель", "композитор", "драматург", "прозаик", "поэт"]


def is_cultural(vc):
    nm = vc["name"].lower()
    if any(kw in nm for kw in CULTURAL_NAME_KW):
        return True
    pos = (vc.get("position") or "").lower()
    if any(kw in pos for kw in CULTURAL_POS_KW):
        return True
    # эвристика: birthday до 1920 + нет phone/email → мёртвый/исторический
    bday = vc.get("birthday") or ""
    if isinstance(bday, str) and len(bday) >= 4:
        try:
            year = int(bday[:4])
            if 1500 <= year < 1920 and not vc.get("phone") and not vc.get("email"):
                return True
        except ValueError:
            pass
    return False


def parse_frontmatter(text):
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    result = {}
    for line in text[3:end].split("\n"):
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if v:
            result[k] = v
    return result


def norm_phone(p):
    if not p:
        return None
    d = re.sub(r"\D", "", p)
    return d[-10:] if len(d) >= 10 else d


def norm_email(e):
    return (e or "").strip().lower() or None


def norm_name(n):
    if not n:
        return ""
    s = re.sub(r"[^а-яёa-z\s]", " ", n.lower())
    return " ".join(sorted(s.split()))


# === Vault scan ===
def scan_vault():
    rows = []
    for d in VAULT_DIRS:
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            fm = parse_frontmatter(text)
            if not (set(fm) & ALL_CONTACT_KEYS):
                continue
            phone = next((fm[k] for k in PHONE_KEYS if k in fm and fm[k]), None)
            email = next((fm[k] for k in EMAIL_KEYS if k in fm and fm[k]), None)
            bday = next((fm[k] for k in BDAY_KEYS if k in fm and fm[k]), None)
            if not (phone or email or bday):
                continue
            rows.append({
                "name": f.stem,
                "phone": phone,
                "email": email,
                "birthday": bday,
                "position": fm.get("должность"),
                "work": fm.get("место_работы"),
                "type": fm.get("person_type"),
                "file": str(f),
            })
    return rows


# === Google fetch ===
def fetch_google_contacts():
    out = []
    page_token = None
    while True:
        params = {
            "personFields": "names,emailAddresses,phoneNumbers,birthdays,metadata",
            "pageSize": 1000,
        }
        if page_token:
            params["pageToken"] = page_token
        resp = gapi("people/me/connections?" + urllib.parse.urlencode(params))
        out.extend(resp.get("connections", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


# === Diff ===
def build_indexes(google_contacts):
    by_phone, by_email, by_name = {}, {}, {}
    for c in google_contacts:
        names = c.get("names") or []
        display = names[0].get("displayName", "") if names else ""
        family = names[0].get("familyName", "") if names else ""
        given = names[0].get("givenName", "") if names else ""
        entry = {
            "resource": c.get("resourceName"),
            "display_name": display,
        }
        for p in (c.get("phoneNumbers") or []):
            ph = norm_phone(p.get("value"))
            if ph:
                by_phone.setdefault(ph, []).append(entry)
        for e in (c.get("emailAddresses") or []):
            em = norm_email(e.get("value"))
            if em:
                by_email.setdefault(em, []).append(entry)
        if display:
            by_name.setdefault(norm_name(display), []).append(entry)
        if family and given:
            by_name.setdefault(norm_name(family + " " + given), []).append(entry)
    return by_phone, by_email, by_name


def main():
    log_f = LOG_PATH.open("a", encoding="utf-8")
    def log(*a):
        msg = " ".join(str(x) for x in a)
        print(msg, flush=True)
        log_f.write(f"{datetime.now().isoformat()} {msg}\n")

    log(f"=== contacts_check_weekly run started ===")
    vault = scan_vault()
    log(f"vault entries with contact data: {len(vault)}")

    try:
        google = fetch_google_contacts()
    except Exception as e:
        log(f"FATAL fetch Google: {e}")
        sys.exit(1)
    log(f"Google contacts: {len(google)}")

    by_phone, by_email, by_name = build_indexes(google)

    new_to_google = []
    matched = []
    weak = []
    cultural_skipped = []
    for vc in vault:
        if is_cultural(vc):
            cultural_skipped.append(vc["name"])
            continue
        p, e, n = norm_phone(vc.get("phone")), norm_email(vc.get("email")), norm_name(vc["name"])
        matches = set()
        by_what = []
        if p and p in by_phone:
            for m in by_phone[p]:
                matches.add(m["resource"])
            by_what.append("phone")
        if e and e in by_email:
            for m in by_email[e]:
                matches.add(m["resource"])
            by_what.append("email")
        if n and n in by_name:
            for m in by_name[n]:
                matches.add(m["resource"])
            by_what.append("name")
        if not matches:
            new_to_google.append(vc)
        elif "phone" in by_what or "email" in by_what:
            matched.append(vc["name"])
        else:
            weak.append(vc["name"])

    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = DAILY_DIR_ROOT / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    out_path = daily_dir / "contacts_check.json"

    result = {
        "checked_at": datetime.now().isoformat(),
        "vault_total": len(vault),
        "google_total": len(google),
        "missing_in_google": new_to_google,
        "weak_name_only_matches": weak,
        "matched_count": len(matched),
        "cultural_skipped": cultural_skipped,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"missing in Google: {len(new_to_google)}")
    log(f"weak name-only matches: {len(weak)}")
    log(f"cultural skipped: {len(cultural_skipped)}")
    log(f"saved → {out_path}")
    log_f.close()



KUMA_URL = "http://127.0.0.1:3001/api/push/Xrieo8jZU2EchYgausWC"

def kuma_push(status, msg):
    try:
        import urllib.parse, urllib.request
        url = KUMA_URL + "?status=" + status + "&msg=" + urllib.parse.quote(str(msg)[:200]) + "&ping="
        urllib.request.urlopen(url, timeout=6)
    except Exception:
        pass

if __name__ == "__main__":
    try:
        main()
        kuma_push("up", "ok")
    except Exception as e:
        kuma_push("down", f"FATAL: {type(e).__name__}: {e}")
        raise
