"""Print the link columns of every base (CI diagnostics)."""

import json
import os
import sys

sys.path.insert(0, "src")
from nocodb_transfer.client import Client  # noqa: E402

for url, token in [(os.environ["NOCODB_URL"], os.environ["NOCODB_TOKEN"]), (os.environ["NOCODB_TARGET_URL"], os.environ["NOCODB_TARGET_TOKEN"])]:
    c = Client(url, token)
    print("==", url, c.version())
    for b in c.bases():
        for t in c.tables(b["id"]):
            for col in c.table(t["id"])["columns"]:
                if col["uidt"] in ("Links", "LinkToAnotherRecord"):
                    o = col.get("colOptions") or {}
                    print(b["title"], t["title"], col["title"], col["uidt"], col["id"], "system=%s" % col.get("system"),
                          json.dumps({k: o.get(k) for k in ("type", "fk_child_column_id", "fk_parent_column_id", "fk_related_model_id", "fk_mm_model_id")}))
