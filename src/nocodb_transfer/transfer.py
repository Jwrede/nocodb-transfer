"""Export a NocoDB base to a snapshot directory and import it into another
NocoDB installation."""

import hashlib
import json
import os
import re

from . import __version__
from . import schema as S

FORMAT = 1
VIEW_TYPES = {1: "form", 2: "gallery", 3: "grid", 4: "kanban"}
VIEW_CREATE = {1: "forms", 2: "galleries", 3: "grids", 4: "kanbans"}
FORM_FIELDS = ("heading", "subheading", "success_msg", "redirect_url", "submit_another_form", "show_blank_form")
FORM_COLUMN_FIELDS = ("label", "help", "description", "required")
FILTER_FIELDS = ("comparison_op", "comparison_sub_op", "value", "logical_op", "is_group")


class Log:
    def __init__(self, quiet=False):
        self.quiet = quiet
        self.warnings = []

    def info(self, msg):
        if not self.quiet:
            print(msg, flush=True)

    def warn(self, msg):
        self.warnings.append(msg)
        print("warning: " + msg, flush=True)


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def resolve_base(client, base):
    """Accept a base id or a base title."""
    for b in client.bases():
        if b["id"] == base:
            return b
    matches = [b for b in client.bases() if b["title"] == base]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(f"several bases are titled {base!r}; pass the base id")
    # Cloud tokens may not list bases across workspaces; try the id directly.
    try:
        return client.base(base)
    except Exception:
        raise SystemExit(f"base {base!r} not found") from None


def read_tables(client, base_id):
    return [client.table(t["id"]) for t in client.tables(base_id)]


# ---------------------------------------------------------------- export


def _filters(client, view_id):
    def children(fid):
        res = client.get(f"/api/v2/meta/filters/{fid}/children")
        out = []
        for f in res.get("list", []):
            if f.get("is_group"):
                f["children"] = children(f["id"])
            out.append(f)
        return out

    out = []
    for f in client.get(f"/api/v2/meta/views/{view_id}/filters").get("list", []):
        if f.get("is_group"):
            f["children"] = children(f["id"])
        out.append(f)
    return out


def read_views(client, table_id):
    out = []
    for v in sorted(client.views(table_id), key=lambda v: (v.get("order") or 0)):
        item = {"id": v["id"], "title": v["title"], "type": v["type"], "order": v.get("order"),
                "is_default": v.get("is_default")}
        if v["type"] not in VIEW_TYPES:
            item["unsupported"] = True
            out.append(item)
            continue
        item["filters"] = _filters(client, v["id"]) if v["type"] != 1 else []
        item["sorts"] = client.get(f"/api/v2/meta/views/{v['id']}/sorts").get("list", []) if v["type"] != 1 else []
        item["columns"] = [
            {k: c.get(k) for k in ("fk_column_id", "show", "order")}
            for c in client.get(f"/api/v2/meta/views/{v['id']}/columns").get("list", [])
        ]
        if v["type"] == 4:
            d = client.get(f"/api/v2/meta/kanbans/{v['id']}")
            item["detail"] = {"fk_grp_col_id": d.get("fk_grp_col_id"), "fk_cover_image_col_id": d.get("fk_cover_image_col_id")}
        elif v["type"] == 2:
            d = client.get(f"/api/v2/meta/galleries/{v['id']}")
            item["detail"] = {"fk_cover_image_col_id": d.get("fk_cover_image_col_id")}
        elif v["type"] == 1:
            d = client.get(f"/api/v2/meta/forms/{v['id']}")
            item["detail"] = {k: d.get(k) for k in FORM_FIELDS}
            item["form_columns"] = [
                dict({"fk_column_id": c["fk_column_id"]}, **{k: c.get(k) for k in FORM_COLUMN_FIELDS})
                for c in d.get("columns", [])
            ]
        out.append(item)
    return out


def export_base(client, base, out_dir, log=None, with_files=True):
    log = log or Log()
    b = resolve_base(client, base)
    tables = read_tables(client, b["id"])
    for t in tables:
        S.pk_column(t)  # fail early on composite keys
    os.makedirs(os.path.join(out_dir, "records"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "links"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "files"), exist_ok=True)
    by_id = {t["id"]: t for t in tables}
    counts, files = {}, 0
    for t in tables:
        pk = S.pk_column(t)["title"]
        cols = [c for c in t["columns"] if S.writable(c)]
        for c in t["columns"]:
            if S.kind(c) == "skipped" and c["uidt"] == "Button":
                log.warn(f"{t['title']}.{c['title']}: Button columns are not copied")
            if c["uidt"] == "User":
                log.warn(f"{t['title']}.{c['title']}: User column is created, values are not copied (users differ between installations)")
        n = 0
        with open(os.path.join(out_dir, "records", t["id"] + ".jsonl"), "w") as fh:
            for row in client.iter_records(t["id"], pk):
                fields = {}
                for c in cols:
                    v = row.get(c["title"])
                    if v is None:
                        continue
                    if c["uidt"] == "Attachment" and with_files:
                        items = []
                        for a in v if isinstance(v, list) else []:
                            data = client.download(a)
                            sha = hashlib.sha256(data).hexdigest()
                            p = os.path.join(out_dir, "files", sha)
                            if not os.path.exists(p):
                                with open(p, "wb") as f:
                                    f.write(data)
                                files += 1
                            items.append({"file": sha, "title": a.get("title"), "mimetype": a.get("mimetype"), "size": len(data)})
                        v = items
                    elif c["uidt"] == "Attachment":
                        continue
                    fields[c["title"]] = v
                fh.write(json.dumps({"pk": row[pk], "fields": fields}) + "\n")
                n += 1
        counts[t["id"]] = n
        log.info(f"exported {t['title']}: {n} records")

    rels = S.relations(tables)
    link_count = 0
    for rel in rels:
        parent, child = by_id[rel["parent"]], by_id.get(rel["child"])
        if child is None:
            log.warn(f"relation {rel['key']} points to a table outside this base; skipped")
            continue
        ppk, cpk = S.pk_column(parent)["title"], S.pk_column(child)["title"]
        pairs = {}
        if rel["sides"]["parent"] is not None:
            col = rel["sides"]["parent"]
            for row in client.iter_records(parent["id"], ppk):
                v = row.get(col["title"])
                if not v:
                    continue
                if isinstance(v, dict) and cpk in v:
                    ids = [v[cpk]]
                else:
                    ids = client.linked_ids(parent["id"], col["id"], row[ppk], cpk)
                if ids:
                    pairs[row[ppk]] = ids
        else:
            col = rel["sides"]["child"]
            for row in client.iter_records(child["id"], cpk):
                v = row.get(col["title"])
                if isinstance(v, dict) and ppk in v:
                    pairs.setdefault(v[ppk], []).append(row[cpk])
        with open(os.path.join(out_dir, "links", _safe(rel["key"]) + ".jsonl"), "w") as fh:
            for k, ids in pairs.items():
                fh.write(json.dumps({"from": k, "to": ids}) + "\n")
                link_count += len(ids)
    views = {t["id"]: read_views(client, t["id"]) for t in tables}
    manifest = {
        "format": FORMAT,
        "tool": f"nocodb-transfer {__version__}",
        "source": {"url": client.url, "version": client.version(), "base_id": b["id"]},
        "base": {"title": b["title"], "description": b.get("description")},
        "tables": tables,
        "views": views,
        "counts": counts,
        "links": link_count,
        "files": with_files,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    log.info(f"exported {len(tables)} tables, {sum(counts.values())} records, {link_count} links, {files} files")
    return manifest


# ---------------------------------------------------------------- import


def _new_columns(before, after):
    old = {c["id"] for c in before["columns"]}
    return [c for c in after["columns"] if c["id"] not in old]


def _value(col, v, upload):
    u = col["uidt"]
    if u == "Checkbox":
        return bool(v)
    if u == "Attachment":
        return [upload(a) for a in v]
    return v


def import_snapshot(client, snap_dir, base_id=None, title=None, log=None, batch=100):
    log = log or Log()
    with open(os.path.join(snap_dir, "manifest.json")) as fh:
        m = json.load(fh)
    if m.get("format") != FORMAT:
        raise SystemExit(f"unsupported snapshot format {m.get('format')}")
    tables = m["tables"]
    src = {t["id"]: t for t in tables}
    title_of = {c["id"]: c["title"] for t in tables for c in t["columns"]}

    if base_id:
        base = client.base(base_id)
        if client.tables(base_id):
            raise SystemExit(f"target base {base_id} is not empty")
    else:
        base = client.create_base(title or m["base"]["title"])
    bid = base["id"]
    log.info(f"importing into base {base.get('title')} ({bid})")

    tmap, colmap = {}, {}
    # 1. tables with scalar columns
    for t in tables:
        specs = [S.scalar_spec(c) for c in t["columns"] if S.kind(c) == "scalar"]
        created = client.create_table(bid, t["title"], specs)
        tmap[t["id"]] = created["id"]
        _map_by_title(t, created, colmap)
    # 2. relations
    current = {tid: client.table(tid) for tid in tmap.values()}
    rel_cols = {}
    for rel in S.relations(tables):
        if rel["child"] not in tmap:
            continue
        p_side, c_side = rel["sides"]["parent"], rel["sides"]["child"]
        tp, tc = tmap[rel["parent"]], tmap[rel["child"]]
        name = p_side["title"] if p_side else src[rel["child"]]["title"]
        uidt = p_side["uidt"] if p_side else ("Links" if rel["type"] != "oo" else "LinkToAnotherRecord")
        before_p, before_c = current[tp], current[tc]
        client.create_column(tp, {"title": name, "uidt": uidt, "parentId": tp, "childId": tc, "type": rel["type"]})
        after_p = client.table(tp)
        after_c = after_p if tc == tp else client.table(tc)
        new_p = _new_columns(before_p, after_p)
        new_c = new_p if tc == tp else _new_columns(before_c, after_c)
        made = next(c for c in new_p if c["title"] == name and S.kind(c) == "relation")
        reverse = next((c for c in new_c if S.kind(c) == "relation" and c["id"] != made["id"]), None)
        rel_cols[rel["key"]] = made["id"]
        if p_side:
            colmap[p_side["id"]] = made["id"]
        if c_side is not None and reverse is not None:
            colmap[c_side["id"]] = reverse["id"]
            if reverse["title"] != c_side["title"]:
                client.update_column(reverse["id"], {"title": c_side["title"], "uidt": reverse["uidt"]})
        elif reverse is not None:
            log.warn(f"{src[rel['parent']]['title']}.{name}: source has no reverse link; target keeps {reverse['title']!r}")
        current[tp] = client.table(tp)
        current[tc] = current[tp] if tc == tp else client.table(tc)
    # 3. derived columns in dependency order
    for t, c in S.derived_order(tables):
        try:
            spec = S.derived_spec(c, colmap, title_of)
        except KeyError as e:
            log.warn(f"{t['title']}.{c['title']}: depends on a column that was not copied ({e}); skipped")
            continue
        tid = tmap[t["id"]]
        before = client.table(tid)
        client.create_column(tid, spec)
        after = client.table(tid)
        made = next(x for x in _new_columns(before, after) if x["title"] == c["title"])
        colmap[c["id"]] = made["id"]
    # 4. records
    pkmap = {}
    files = {}

    def upload(a):
        sha = a["file"]
        if sha not in files:
            with open(os.path.join(snap_dir, "files", sha), "rb") as fh:
                files[sha] = client.upload(a.get("title") or sha, fh.read(), a.get("mimetype"))
        return dict(files[sha], title=a.get("title") or files[sha].get("title"))

    for t in tables:
        tid = tmap[t["id"]]
        tpk = S.pk_column(client.table(tid))["title"]
        cols = {c["title"]: c for c in t["columns"] if S.writable(c)}
        mapping = pkmap[t["id"]] = {}
        chunk = []

        def flush():
            if not chunk:
                return
            res = client.insert(tid, [r for _, r in chunk])
            for (old, _), new in zip(chunk, res):
                mapping[old] = new[tpk]
            chunk.clear()

        path = os.path.join(snap_dir, "records", t["id"] + ".jsonl")
        with open(path) as fh:
            for line in fh:
                rec = json.loads(line)
                row = {k: _value(cols[k], v, upload) for k, v in rec["fields"].items() if k in cols}
                chunk.append((rec["pk"], row))
                if len(chunk) >= batch:
                    flush()
        flush()
        log.info(f"imported {t['title']}: {len(mapping)} records")
    # 5. links
    links = 0
    for rel in S.relations(tables):
        if rel["key"] not in rel_cols:
            continue
        path = os.path.join(snap_dir, "links", _safe(rel["key"]) + ".jsonl")
        if not os.path.exists(path):
            continue
        tp = tmap[rel["parent"]]
        col = rel_cols[rel["key"]]
        ppk = S.pk_column(client.table(tp))["title"]
        cmap, pmap = pkmap[rel["child"]], pkmap[rel["parent"]]
        with open(path) as fh:
            for line in fh:
                item = json.loads(line)
                ids = [cmap[i] for i in item["to"] if i in cmap]
                if ids and item["from"] in pmap:
                    client.link(tp, col, pmap[item["from"]], ids, ppk)
                    links += len(ids)
    log.info(f"imported {links} links")
    # 6. views
    for t in tables:
        _import_views(client, tmap[t["id"]], m["views"].get(t["id"], []), colmap, log, t["title"])
    log.info(f"done: base {bid}, {client.requests} API requests")
    return {"base_id": bid, "tables": tmap, "columns": colmap, "records": pkmap, "links": links}


def _map_by_title(src_table, dst_table, colmap):
    by_title = {c["title"]: c for c in dst_table["columns"]}
    for c in src_table["columns"]:
        k = S.kind(c)
        if k in ("pk", "scalar", "system") and c["title"] in by_title:
            colmap[c["id"]] = by_title[c["title"]]["id"]
    # system columns may carry different titles; map by uidt as a fallback
    for c in src_table["columns"]:
        if c["id"] not in colmap and S.kind(c) in ("pk", "system"):
            same = [d for d in dst_table["columns"] if d["uidt"] == c["uidt"] and d.get("pk") == c.get("pk")]
            if len(same) == 1:
                colmap[c["id"]] = same[0]["id"]


def _create_filters(client, view_id, filters, colmap, log, where, parent=None):
    for f in filters:
        body = {k: f.get(k) for k in FILTER_FIELDS if f.get(k) is not None}
        if f.get("fk_column_id"):
            if f["fk_column_id"] not in colmap:
                log.warn(f"{where}: filter on a column that was not copied; skipped")
                continue
            body["fk_column_id"] = colmap[f["fk_column_id"]]
        if parent:
            body["fk_parent_id"] = parent
        made = client.post(f"/api/v2/meta/views/{view_id}/filters", body)
        if f.get("is_group"):
            _create_filters(client, view_id, f.get("children", []), colmap, log, where, made["id"])


def _import_views(client, table_id, views, colmap, log, table_title):
    existing = sorted(client.views(table_id), key=lambda v: (v.get("order") or 0))
    default = existing[0] if existing else None
    used_default = False
    for v in views:
        where = f"{table_title} / view {v['title']!r}"
        if v.get("unsupported"):
            log.warn(f"{where}: view type {v['type']} is not copied")
            continue
        if v["type"] == 3 and not used_default and default is not None:
            vid = default["id"]
            used_default = True
            if default["title"] != v["title"]:
                client.patch(f"/api/v2/meta/views/{vid}", {"title": v["title"]})
        else:
            body = {"title": v["title"]}
            if v["type"] == 4:
                grp = (v.get("detail") or {}).get("fk_grp_col_id")
                if grp in colmap:
                    body["fk_grp_col_id"] = colmap[grp]
            vid = client.post(f"/api/v2/meta/tables/{table_id}/{VIEW_CREATE[v['type']]}", body)["id"]
        detail = {k: (colmap.get(x) if k.startswith("fk_") else x)
                  for k, x in (v.get("detail") or {}).items() if x is not None}
        if v["type"] == 4 and detail:
            client.patch(f"/api/v2/meta/kanbans/{vid}", detail)
        elif v["type"] == 2 and detail:
            client.patch(f"/api/v2/meta/galleries/{vid}", detail)
        elif v["type"] == 1 and detail:
            client.patch(f"/api/v2/meta/forms/{vid}", detail)
        _create_filters(client, vid, v.get("filters", []), colmap, log, where)
        for s in v.get("sorts", []):
            if s.get("fk_column_id") in colmap:
                client.post(f"/api/v2/meta/views/{vid}/sorts", {"fk_column_id": colmap[s["fk_column_id"]], "direction": s.get("direction") or "asc"})
        target_cols = {c["fk_column_id"]: c for c in client.get(f"/api/v2/meta/views/{vid}/columns").get("list", [])}
        for c in v.get("columns", []):
            tc = target_cols.get(colmap.get(c["fk_column_id"]))
            if tc is None:
                continue
            want = {"show": bool(c.get("show")), "order": c.get("order")}
            if bool(tc.get("show")) != want["show"] or tc.get("order") != want["order"]:
                client.patch(f"/api/v2/meta/views/{vid}/columns/{tc['id']}", want)
        if v["type"] == 1 and v.get("form_columns"):
            fcols = {c["fk_column_id"]: c for c in client.get(f"/api/v2/meta/forms/{vid}").get("columns", [])}
            for c in v["form_columns"]:
                tc = fcols.get(colmap.get(c["fk_column_id"]))
                body = {k: c[k] for k in FORM_COLUMN_FIELDS if c.get(k) is not None}
                if tc is not None and body:
                    client.patch(f"/api/v2/meta/form-columns/{tc['id']}", body)
