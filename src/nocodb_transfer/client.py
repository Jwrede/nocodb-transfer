"""Small HTTP client for the NocoDB v2 REST API (standard library only)."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class ApiError(Exception):
    def __init__(self, method, path, status, body):
        super().__init__(f"{method} {path} -> HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


class Client:
    def __init__(self, url, token, retries=4, timeout=60, sleep=time.sleep):
        self.url = url.rstrip("/")
        self.token = token
        self.retries = retries
        self.timeout = timeout
        self.sleep = sleep
        self.requests = 0

    def _headers(self, extra=None):
        h = {"xc-token": self.token, "Accept": "application/json"}
        if extra:
            h.update(extra)
        return h

    def request(self, method, path, body=None, params=None, raw=None, content_type=None):
        url = self.url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        headers = {}
        if raw is not None:
            data = raw
            headers["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        delay = 1.0
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=self._headers(headers))
            self.requests += 1
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = resp.read()
                    if not payload:
                        return None
                    try:
                        return json.loads(payload)
                    except ValueError:
                        return payload.decode(errors="replace")
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                # NocoDB Cloud rate-limits per token; retry 429 and 5xx.
                if (e.code == 429 or e.code >= 500) and attempt < self.retries:
                    self.sleep(delay)
                    delay *= 2
                    continue
                raise ApiError(method, path, e.code, text) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt < self.retries:
                    self.sleep(delay)
                    delay *= 2
                    continue
                raise

    def get(self, path, **params):
        return self.request("GET", path, params=params or None)

    def post(self, path, body=None):
        return self.request("POST", path, body=body if body is not None else {})

    def patch(self, path, body):
        return self.request("PATCH", path, body=body)

    def delete(self, path):
        return self.request("DELETE", path)

    # -- meta ---------------------------------------------------------------

    def version(self):
        try:
            return self.get("/api/v1/version").get("releaseVersion")
        except Exception:
            return None

    def bases(self):
        return self.get("/api/v2/meta/bases")["list"]

    def base(self, base_id):
        return self.get(f"/api/v2/meta/bases/{base_id}")

    def create_base(self, title):
        return self.post("/api/v2/meta/bases", {"title": title})

    def tables(self, base_id):
        return self.get(f"/api/v2/meta/bases/{base_id}/tables")["list"]

    def table(self, table_id):
        return self.get(f"/api/v2/meta/tables/{table_id}")

    def create_table(self, base_id, title, columns, meta=None):
        body = {"title": title, "columns": columns}
        if meta:
            body["meta"] = meta
        return self.post(f"/api/v2/meta/bases/{base_id}/tables", body)

    def create_column(self, table_id, column):
        return self.post(f"/api/v2/meta/tables/{table_id}/columns", column)

    def update_column(self, column_id, column):
        return self.patch(f"/api/v2/meta/columns/{column_id}", column)

    def views(self, table_id):
        return self.get(f"/api/v2/meta/tables/{table_id}/views")["list"]

    # -- data ---------------------------------------------------------------

    def iter_records(self, table_id, sort_field, page=1000):
        offset = 0
        while True:
            res = self.get(f"/api/v2/tables/{table_id}/records", limit=page, offset=offset, sort=sort_field)
            rows = res.get("list", [])
            yield from rows
            info = res.get("pageInfo", {})
            if info.get("isLastPage", True) or not rows:
                return
            offset += len(rows)

    def count(self, table_id):
        return self.get(f"/api/v2/tables/{table_id}/records/count")["count"]

    def insert(self, table_id, rows):
        return self.post(f"/api/v2/tables/{table_id}/records", rows)

    def linked_ids(self, table_id, column_id, record_id, pk_title, page=1000):
        out = []
        offset = 0
        while True:
            res = self.get(
                f"/api/v2/tables/{table_id}/links/{column_id}/records/{record_id}",
                fields=pk_title, limit=page, offset=offset,
            )
            if isinstance(res, dict) and isinstance(res.get("list"), dict):
                one = res["list"]  # one-to-one and belongs-to answer with a single object
                return [one[pk_title]] if pk_title in one else []
            if isinstance(res, dict) and "list" in res:
                rows = res["list"]
                out.extend(r[pk_title] for r in rows)
                if res.get("pageInfo", {}).get("isLastPage", True) or not rows:
                    return out
                offset += len(rows)
            elif isinstance(res, dict) and res:
                # belongs-to / one-to-one return a single object
                return [res[pk_title]] if pk_title in res else []
            else:
                return out

    def link(self, table_id, column_id, record_id, ids, pk_title):
        return self.post(
            f"/api/v2/tables/{table_id}/links/{column_id}/records/{record_id}",
            [{pk_title: i} for i in ids],
        )

    def upload(self, filename, content, mimetype):
        boundary = uuid.uuid4().hex
        safe = filename.replace('"', "_")
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe}"\r\n'
            f"Content-Type: {mimetype or 'application/octet-stream'}\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        res = self.request(
            "POST", "/api/v2/storage/upload", raw=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return res[0]

    def download(self, attachment):
        """Return the bytes of an attachment cell entry."""
        if attachment.get("signedUrl"):
            url = attachment["signedUrl"]
        elif attachment.get("signedPath"):
            url = self.url + "/" + attachment["signedPath"].lstrip("/")
        elif attachment.get("path"):
            url = self.url + "/" + attachment["path"].lstrip("/")
        else:
            url = attachment["url"]
        delay = 1.0
        for attempt in range(self.retries + 1):
            headers = self._headers() if url.startswith(self.url) else {}
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read()
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt < self.retries:
                    self.sleep(delay)
                    delay *= 2
                    continue
                raise
