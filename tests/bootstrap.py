"""Wait for a fresh NocoDB, sign in as the admin from NC_ADMIN_EMAIL /
NC_ADMIN_PASSWORD and print a new API token. Used by CI."""

import json
import sys
import time
import urllib.request

url, email, password = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]


def call(path, body=None, headers=None):
    req = urllib.request.Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


deadline = time.time() + 240
while True:
    try:
        call("/api/v1/health")
        break
    except Exception:
        if time.time() > deadline:
            sys.exit(f"{url} did not become healthy")
        time.sleep(3)
auth = call("/api/v1/auth/user/signin", {"email": email, "password": password})["token"]
print(call("/api/v1/tokens", {"description": "ci"}, {"xc-auth": auth})["token"])
