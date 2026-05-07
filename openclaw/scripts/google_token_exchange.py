#!/usr/bin/env python3
"""Обменивает auth code на токен без PKCE."""
import json
import urllib.request
import urllib.parse
import sys

CREDENTIALS_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json"
TOKEN_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_token.json"

with open(CREDENTIALS_FILE) as f:
    creds = json.load(f)["installed"]

code = sys.argv[1]

data = urllib.parse.urlencode({
    "code": code,
    "client_id": creds["client_id"],
    "client_secret": creds["client_secret"],
    "redirect_uri": "http://localhost",
    "grant_type": "authorization_code",
}).encode()

req = urllib.request.Request(creds["token_uri"], data=data, method="POST")
try:
    with urllib.request.urlopen(req) as r:
        token = json.loads(r.read())
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)
    print("OK token saved")
    print(f"Expires in: {token.get('expires_in')} sec")
    print(f"Scope: {token.get('scope')}")
except Exception as e:
    import traceback
    traceback.print_exc()
