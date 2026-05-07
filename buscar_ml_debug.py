import urllib.parse
import urllib.request
import json
import os

client_id = os.environ.get("ML_CLIENT_ID")
client_secret = os.environ.get("ML_SECRET_KEY")
refresh_token = os.environ.get("ML_REFRESH_TOKEN")

data = urllib.parse.urlencode({
    "grant_type": "refresh_token",
    "client_id": client_id,
    "client_secret": client_secret,
    "refresh_token": refresh_token
}).encode()

req = urllib.request.Request("https://api.mercadolibre.com/oauth/token", data=data, method="POST")
req.add_header("Content-Type", "application/x-www-form-urlencoded")

with urllib.request.urlopen(req) as r:
    token_data = json.loads(r.read())
    token = token_data["access_token"]
    print(f"Token OK: {token[:30]}...")

url = "https://api.mercadolibre.com/sites/MLC/search?q=molinillo+cafe&limit=5"
req2 = urllib.request.Request(url)
req2.add_header("Authorization", f"Bearer {token}")

try:
    with urllib.request.urlopen(req2) as r:
        data = json.loads(r.read())
        print(f"Resultados: {len(data.get('results', []))}")
except urllib.error.HTTPError as e:
    print(f"Error {e.code}: {e.read().decode()}")
