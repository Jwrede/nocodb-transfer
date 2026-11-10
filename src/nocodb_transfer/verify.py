"""Compare two NocoDB bases: schema, views, record values (including links,
lookups, rollups and formulas, which NocoDB computes on the target)."""

import json

from . import schema as S
from .transfer import read_tables, resolve_base, VIEW_TYPES


def _compared(col):
    k = S.kind(col)
    if k == "scalar":
        return col["uidt"] not in S.READ_ONLY
    return k in ("relation", "derived")


def _strip(v, ignore):
    if isinstance(v, dict):
        return {k: _strip(x, ignore) for k, x in sorted(v.items()) if k not in ignore}
    if isinstance(v, list):
        return [_strip(x, ignore) for x in v]
    return v


def normalize(col, v, ignore):
    u = col["uidt"]
    if v is None or v == [] or v == "":
        return None
    if u == "Attachment":
        return sorted([a.get("title"), a.get("size"), a.get("mimetype")] for a in v)
    if u == "Checkbox":
        return bool(v)
    if u == "JSON" and isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, (dict, list)):
        v = _strip(v, ignore)
        if isinstance(v, list):
            try:
                v = sorted(v, key=lambda x: json.dumps(x, sort_keys=True))
            except TypeError:
                pass
        return v
    return v


def _rows(client, table, cols, ignore):
    pk = S.pk_column(table)["title"]
    out = []
    for r in client.iter_records(table["id"], pk):
        out.append({c["title"]: normalize(c, r.get(c["title"]), ignore) for c in cols})
    return out


def _views(client, table):
    out = []
    for v in sorted(client.views(table["id"]), key=lambda v: (v.get("order") or 0)):
        item = (v["title"], VIEW_TYPES.get(v["type"], v["type"]))
        if v["type"] in (2, 3, 4):
            nf = len(client.get(f"/api/v2/meta/views/{v['id']}/filters").get("list", []))
            ns = len(client.get(f"/api/v2/meta/views/{v['id']}/sorts").get("list", []))
            item = item + (nf, ns)
        out.append(item)
    return out


def compare(src, src_base, dst, dst_base, max_diffs=50):
    """Return (diffs, stats)."""
    a_tables = read_tables(src, resolve_base(src, src_base)["id"])
    b_tables = read_tables(dst, resolve_base(dst, dst_base)["id"])
    ignore = set()
    for t in a_tables + b_tables:
        for c in t["columns"]:
            if c.get("pk") or S.is_system(c):
                ignore.add(c["title"])
    diffs, stats = [], {"tables": 0, "records": 0, "values": 0}
    b_by_title = {t["title"]: t for t in b_tables}
    for t in a_tables:
        u = b_by_title.get(t["title"])
        if u is None:
            diffs.append(f"table {t['title']!r} missing in target")
            continue
        stats["tables"] += 1
        a_cols = {c["title"]: c for c in t["columns"] if _compared(c)}
        b_cols = {c["title"]: c for c in u["columns"] if _compared(c)}
        for title, c in a_cols.items():
            if title not in b_cols:
                diffs.append(f"{t['title']}.{title}: column missing in target")
            elif b_cols[title]["uidt"] != c["uidt"]:
                diffs.append(f"{t['title']}.{title}: type {c['uidt']} vs {b_cols[title]['uidt']}")
        for title in b_cols:
            if title not in a_cols:
                diffs.append(f"{t['title']}.{title}: extra column in target")
        av, bv = _views(src, t), _views(dst, u)
        if av != bv:
            diffs.append(f"{t['title']}: views differ: {av} vs {bv}")
        cols = [c for title, c in a_cols.items() if title in b_cols]
        ar, br = _rows(src, t, cols, ignore), _rows(dst, u, [b_cols[c["title"]] for c in cols], ignore)
        if len(ar) != len(br):
            diffs.append(f"{t['title']}: {len(ar)} records vs {len(br)}")
        for i, (x, y) in enumerate(zip(ar, br)):
            stats["records"] += 1
            for k in x:
                stats["values"] += 1
                if x[k] != y.get(k):
                    diffs.append(f"{t['title']} record #{i + 1} {k}: {x[k]!r} vs {y.get(k)!r}")
                    if len(diffs) >= max_diffs:
                        return diffs, stats
    for t in b_tables:
        if t["title"] not in {x["title"] for x in a_tables}:
            diffs.append(f"table {t['title']!r} only in target")
    return diffs, stats
