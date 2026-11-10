"""Pure functions over NocoDB table metadata: classify columns, pair relations,
order derived columns, rewrite formulas. No network access here."""

import re

RELATION = {"Links", "LinkToAnotherRecord"}
DERIVED = {"Lookup", "Rollup", "Formula", "Barcode", "QrCode"}
# Columns NocoDB fills itself. They are recreated, but values are not written.
READ_ONLY = {"CreatedTime", "LastModifiedTime", "CreatedBy", "LastModifiedBy", "AutoNumber", "User"}
SKIPPED = {"Button", "ForeignKey", "Order"}
SELECT = {"SingleSelect", "MultiSelect"}

FORMULA_REF = re.compile(r"\{\{(\w+)\}\}")


def is_system(col):
    return bool(col.get("system")) or col.get("uidt") in ("ForeignKey", "Order")


def pk_column(table):
    pks = [c for c in table["columns"] if c.get("pk")]
    if len(pks) != 1:
        raise ValueError(f"table {table['title']!r} has {len(pks)} primary key columns; only single-column keys are supported")
    return pks[0]


def kind(col):
    """Return one of: pk, system, relation, derived, skipped, scalar."""
    if col.get("pk"):
        return "pk"
    if is_system(col):
        return "system"
    uidt = col.get("uidt")
    if uidt in RELATION:
        return "relation"
    if uidt in DERIVED:
        return "derived"
    if uidt in SKIPPED:
        return "skipped"
    return "scalar"


def writable(col):
    return kind(col) == "scalar" and col.get("uidt") not in READ_ONLY


def scalar_spec(col):
    """Column definition accepted by the create-table / create-column APIs."""
    spec = {"title": col["title"], "uidt": col["uidt"]}
    for key in ("meta", "rqd", "cdf", "description"):
        if col.get(key) not in (None, "", {}):
            spec[key] = col[key]
    if col["uidt"] in SELECT:
        opts = (col.get("colOptions") or {}).get("options") or []
        spec["colOptions"] = {
            "options": [
                {k: o[k] for k in ("title", "color", "order") if o.get(k) is not None} for o in opts
            ]
        }
    return spec


def column_index(tables):
    """column id -> (table, column)"""
    idx = {}
    for t in tables:
        for c in t["columns"]:
            idx[c["id"]] = (t, c)
    return idx


def relations(tables):
    """Group the two sides of every link into one relation.

    Returns a list of dicts: type (hm, mm, oo), parent (table id), child (table id),
    sides: {"parent": column or None, "child": column or None}.
    For hm the parent side is the has-many column and the child side the
    belongs-to column; for mm and oo "parent" is the side the relation is created from.
    """
    idx = column_index(tables)
    groups = {}
    order = []
    for t in tables:
        for c in t["columns"]:
            if kind(c) != "relation":
                continue
            o = c.get("colOptions") or {}
            typ = o.get("type")
            if typ == "mm":
                key = ("mm", o.get("fk_mm_model_id"))
            elif typ in ("hm", "bt"):
                key = ("hb", o.get("fk_child_column_id"))
            elif typ == "oo" and o.get("fk_mm_model_id"):
                # newer NocoDB builds one-to-one links on a junction table
                key = ("oj", o.get("fk_mm_model_id"))
            elif typ == "oo":
                key = ("oo", o.get("fk_child_column_id"))
            else:
                continue
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append((t, c))
    out = []
    for key in order:
        sides = groups[key]
        rel = {"key": f"{key[0]}:{key[1]}", "sides": {"parent": None, "child": None}}
        if key[0] == "hb":
            rel["type"] = "hm"
            for t, c in sides:
                o = c["colOptions"]
                if o["type"] == "hm":
                    rel["parent"], rel["child"] = t["id"], o["fk_related_model_id"]
                    rel["sides"]["parent"] = c
                else:
                    rel["child"], rel["parent"] = t["id"], o["fk_related_model_id"]
                    rel["sides"]["child"] = c
        elif key[0] == "oo":
            rel["type"] = "oo"
            fk_table = idx.get(key[1], (None, None))[0]
            for t, c in sides:
                o = c["colOptions"]
                if fk_table is not None and t["id"] == fk_table["id"] and t["id"] != o["fk_related_model_id"]:
                    rel["child"], rel["parent"] = t["id"], o["fk_related_model_id"]
                    rel["sides"]["child"] = c
                else:
                    rel["parent"], rel["child"] = t["id"], o["fk_related_model_id"]
                    rel["sides"]["parent"] = c
        else:
            rel["type"] = "mm" if key[0] == "mm" else "oo"
            t, c = sides[0]
            rel["parent"], rel["child"] = t["id"], c["colOptions"]["fk_related_model_id"]
            rel["sides"]["parent"] = c
            if len(sides) > 1:
                rel["sides"]["child"] = sides[1][1]
        out.append(rel)
    return out


def derived_deps(col):
    o = col.get("colOptions") or {}
    u = col["uidt"]
    if u == "Lookup":
        return [o.get("fk_relation_column_id"), o.get("fk_lookup_column_id")]
    if u == "Rollup":
        return [o.get("fk_relation_column_id"), o.get("fk_rollup_column_id")]
    if u == "Formula":
        return FORMULA_REF.findall(o.get("formula") or "")
    if u == "Barcode":
        return [o.get("fk_barcode_value_column_id")]
    if u == "QrCode":
        return [o.get("fk_qr_value_column_id")]
    return []


def derived_order(tables):
    """Derived columns of all tables, ordered so that dependencies come first.
    Raises ValueError on a cycle."""
    cols = {}
    for t in tables:
        for c in t["columns"]:
            if kind(c) == "derived":
                cols[c["id"]] = (t, c)
    out, state = [], {}

    def visit(cid, stack):
        if state.get(cid) == 2:
            return
        if state.get(cid) == 1:
            raise ValueError("cyclic derived columns: " + " -> ".join(stack + [cid]))
        state[cid] = 1
        for dep in derived_deps(cols[cid][1]):
            if dep in cols:
                visit(dep, stack + [cid])
        state[cid] = 2
        out.append(cols[cid])

    for cid in cols:
        visit(cid, [])
    return out


def formula_with_titles(formula, title_of):
    """Turn NocoDB's stored formula ({{column id}}) into the {Title} form the
    create-column API accepts."""

    def sub(m):
        cid = m.group(1)
        if cid not in title_of:
            raise KeyError(f"formula references unknown column {cid}")
        return "{" + title_of[cid] + "}"

    return FORMULA_REF.sub(sub, formula)


def derived_spec(col, colmap, title_of):
    """Create-column payload for a derived column. colmap maps source column ids
    to target column ids; title_of maps source column ids to titles."""
    o = col.get("colOptions") or {}
    u = col["uidt"]
    spec = {"title": col["title"], "uidt": u}
    if col.get("meta"):
        spec["meta"] = col["meta"]
    if col.get("description"):
        spec["description"] = col["description"]
    if u == "Lookup":
        spec["fk_relation_column_id"] = colmap[o["fk_relation_column_id"]]
        spec["fk_lookup_column_id"] = colmap[o["fk_lookup_column_id"]]
    elif u == "Rollup":
        spec["fk_relation_column_id"] = colmap[o["fk_relation_column_id"]]
        spec["fk_rollup_column_id"] = colmap[o["fk_rollup_column_id"]]
        spec["rollup_function"] = o["rollup_function"]
    elif u == "Formula":
        spec["formula_raw"] = formula_with_titles(o.get("formula") or "", title_of)
    elif u == "Barcode":
        spec["fk_barcode_value_column_id"] = colmap[o["fk_barcode_value_column_id"]]
        if o.get("barcode_format"):
            spec["barcode_format"] = o["barcode_format"]
    elif u == "QrCode":
        spec["fk_qr_value_column_id"] = colmap[o["fk_qr_value_column_id"]]
    return spec
