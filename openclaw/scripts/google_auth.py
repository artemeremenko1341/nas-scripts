#!/usr/bin/env python3
"""Генерирует ссылку авторизации Google без PKCE."""
import json
import urllib.parse
import secrets

CREDENTIALS_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json"
STATE_FILE = "/tmp/google_auth_state.json"

with open(CREDENTIALS_FILE) as f:
    creds = json.load(f)["installed"]

state = secrets.token_urlsafe(16)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/contacts",
    "profile",
    "email",
]

params = {
    "response_type": "code",
    "client_id": creds["client_id"],
    "redirect_uri": "http://localhost",
    "scope": " ".join(SCOPES),
    "state": state,
    "access_type": "offline",
    "prompt": "consent",
}

auth_url = creds["auth_uri"] + "?" + urllib.parse.urlencode(params)

with open(STATE_FILE, "w") as f:
    json.dump({"state": state}, f)

print(auth_url)
