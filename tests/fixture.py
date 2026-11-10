"""Build a source base in a real NocoDB that exercises what nocodb-transfer copies."""

import json

from nocodb_transfer.client import ApiError


def _col(table, title):
    return next(c for c in table["columns"] if c["title"] == title)


def link(client, table_id, spec):
    """Create a link column; NocoDB before 2026 rejects uidt Links for some types."""
    try:
        return client.create_column(table_id, spec)
    except ApiError as e:
        if spec["uidt"] != "Links":
            raise
        print("retrying link column with LinkToAnotherRecord:", e)
        return client.create_column(table_id, dict(spec, uidt="LinkToAnotherRecord"))


def build(client, title):
    base = client.create_base(title)
    bid = base["id"]
    old = (client.version() or "").startswith("0.")
    create_table = client.create_table

    def create(bid, name, columns):
        # NocoDB 0.x expects the primary key column in the create-table request
        if old:
            columns = [{"title": "Id", "column_name": "id", "uidt": "ID"}] + columns
        return create_table(bid, name, columns)

    authors = create(bid, "Authors", [
        {"title": "Name", "uidt": "SingleLineText"},
        {"title": "Bio", "uidt": "LongText"},
        {"title": "E-mail", "uidt": "Email"},
        {"title": "Site", "uidt": "URL"},
        {"title": "Phone", "uidt": "PhoneNumber"},
        {"title": "Score", "uidt": "Number"},
        {"title": "Weight", "uidt": "Decimal", "meta": {"precision": 2}},
        {"title": "Fee", "uidt": "Currency", "meta": {"currency_locale": "de-DE", "currency_code": "EUR"}},
        {"title": "Share", "uidt": "Percent"},
        {"title": "Stars", "uidt": "Rating", "meta": {"max": 5}},
        {"title": "Active", "uidt": "Checkbox"},
        {"title": "Born", "uidt": "Date", "meta": {"date_format": "YYYY-MM-DD"}},
        {"title": "Seen", "uidt": "DateTime"},
        {"title": "Kind", "uidt": "SingleSelect", "colOptions": {"options": [
            {"title": "poet", "color": "#ff0000"}, {"title": "novelist", "color": "#00ff00"}, {"title": "Grüße, \"quoted\""}]}},
        {"title": "Tags", "uidt": "MultiSelect", "colOptions": {"options": [{"title": "a"}, {"title": "b"}, {"title": "c"}]}},
        {"title": "Extra", "uidt": "JSON"},
        {"title": "Photo", "uidt": "Attachment"},
        {"title": "Notiz (intern) / ü", "uidt": "SingleLineText"},
    ])
    books = create(bid, "Books", [
        {"title": "Title", "uidt": "SingleLineText"},
        {"title": "Pages", "uidt": "Number"},
    ])
    genres = create(bid, "Genres", [{"title": "Label", "uidt": "SingleLineText"}])
    people = create(bid, "Staff", [{"title": "Name", "uidt": "SingleLineText"}])
    passports = create(bid, "Passports", [{"title": "Number", "uidt": "SingleLineText"}])
    A, Bk, G, P, Pp = authors["id"], books["id"], genres["id"], people["id"], passports["id"]

    link(client, A, {"title": "Books", "uidt": "Links", "parentId": A, "childId": Bk, "type": "hm"})
    link(client, Bk, {"title": "Genres", "uidt": "Links", "parentId": Bk, "childId": G, "type": "mm"})
    link(client, P, {"title": "Reports", "uidt": "Links", "parentId": P, "childId": P, "type": "hm"})
    link(client, A, {"title": "Passport", "uidt": "LinkToAnotherRecord", "parentId": A, "childId": Pp, "type": "oo"})
    books = client.table(Bk)
    # rename the generated belongs-to side so the copy has to restore the title
    bt = next(c for c in books["columns"] if c["uidt"] == "LinkToAnotherRecord" and c["colOptions"]["type"] == "bt")
    client.update_column(bt["id"], {"title": "Written by", "uidt": bt["uidt"]})
    authors, books = client.table(A), client.table(Bk)

    client.create_column(Bk, {"title": "Author name", "uidt": "Lookup",
                              "fk_relation_column_id": _col(books, "Written by")["id"],
                              "fk_lookup_column_id": _col(authors, "Name")["id"]})
    client.create_column(A, {"title": "Total pages", "uidt": "Rollup",
                             "fk_relation_column_id": _col(authors, "Books")["id"],
                             "fk_rollup_column_id": _col(books, "Pages")["id"], "rollup_function": "sum"})
    client.create_column(Bk, {"title": "Label", "uidt": "Formula", "formula_raw": 'CONCAT({Title}, " by ", {Author name})'})
    client.create_column(Bk, {"title": "Shout", "uidt": "Formula", "formula_raw": "UPPER({Label})"})
    client.create_column(A, {"title": "Pages per star", "uidt": "Formula", "formula_raw": "IF({Stars} > 0, {Total pages} / {Stars}, 0)"})
    books = client.table(Bk)
    client.create_column(Bk, {"title": "Code", "uidt": "Barcode", "fk_barcode_value_column_id": _col(books, "Title")["id"], "barcode_format": "CODE128"})
    client.create_column(Bk, {"title": "QR", "uidt": "QrCode", "fk_qr_value_column_id": _col(books, "Label")["id"]})

    # records: 260 authors (crosses insert batches), with deleted rows so ids have gaps
    files = [client.upload("one.txt", b"first file\n", "text/plain"),
             client.upload("pixel.png", bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4c70000000049454e44ae426082"), "image/png")]
    rows = []
    for i in range(260):
        r = {"Name": f"Author {i}", "Score": i % 17, "Active": i % 3 == 0, "Stars": i % 6,
             "Kind": ["poet", "novelist", 'Grüße, "quoted"'][i % 3], "Notiz (intern) / ü": f"ü{i}"}
        if i % 5 == 0:
            r.update({"Bio": f"line one\nline two {i}", "E-mail": f"a{i}@example.com", "Site": "https://example.com/x",
                      "Phone": "+49 30 123456", "Weight": 1.25 + i, "Fee": 10.5, "Share": 12, "Born": "1990-02-03",
                      "Seen": "2024-05-06 07:08:09+00:00", "Tags": "a,c", "Extra": {"i": i, "nested": [1, "x"]}})
        if i % 50 == 0:
            r["Photo"] = [files[0], files[1]] if i % 100 == 0 else [files[0]]
        rows.append(r)
    ids = []
    for k in range(0, len(rows), 100):
        ids += [x["Id"] for x in client.insert(A, rows[k:k + 100])]
    gone = ids[3:6] + ids[100:101]
    client.request("DELETE", f"/api/v2/tables/{A}/records", body=[{"Id": i} for i in gone])
    alive = [i for i in ids if i not in gone]
    book_ids = [x["Id"] for x in client.insert(Bk, [{"Title": f"Book {i}", "Pages": 10 + i} for i in range(40)])]
    genre_ids = [x["Id"] for x in client.insert(G, [{"Label": n} for n in ("crime", "poetry", "sci-fi")])]
    staff = [x["Id"] for x in client.insert(P, [{"Name": n} for n in ("boss", "lead", "dev1", "dev2")])]
    pass_ids = [x["Id"] for x in client.insert(Pp, [{"Number": f"P-{i}"} for i in range(3)])]
    authors, books, people = client.table(A), client.table(Bk), client.table(P)
    for n, b in enumerate(book_ids):
        client.link(A, _col(authors, "Books")["id"], alive[n % 7 + 2], [b], "Id")
    for b in book_ids[:10]:
        client.link(Bk, _col(books, "Genres")["id"], b, genre_ids[: (b % 3) + 1], "Id")
    client.link(P, _col(people, "Reports")["id"], staff[0], [staff[1]], "Id")
    client.link(P, _col(people, "Reports")["id"], staff[1], staff[2:], "Id")
    for a, p in zip(alive[:3], pass_ids):
        client.link(A, _col(authors, "Passport")["id"], a, [p], "Id")

    # views
    va = client.post(f"/api/v2/meta/tables/{A}/grids", {"title": "Active poets"})
    client.post(f"/api/v2/meta/views/{va['id']}/filters", {"fk_column_id": _col(authors, "Active")["id"], "comparison_op": "checked", "logical_op": "and"})
    grp = client.post(f"/api/v2/meta/views/{va['id']}/filters", {"is_group": True, "logical_op": "and"})
    client.post(f"/api/v2/meta/views/{va['id']}/filters", {"fk_parent_id": grp["id"], "fk_column_id": _col(authors, "Kind")["id"], "comparison_op": "eq", "value": "poet", "logical_op": "or"})
    client.post(f"/api/v2/meta/views/{va['id']}/filters", {"fk_parent_id": grp["id"], "fk_column_id": _col(authors, "Score")["id"], "comparison_op": "gt", "value": "10", "logical_op": "or"})
    client.post(f"/api/v2/meta/views/{va['id']}/sorts", {"fk_column_id": _col(authors, "Score")["id"], "direction": "desc"})
    vcols = client.get(f"/api/v2/meta/views/{va['id']}/columns")["list"]
    hide = next(c for c in vcols if c["fk_column_id"] == _col(authors, "Bio")["id"])
    client.patch(f"/api/v2/meta/views/{va['id']}/columns/{hide['id']}", {"show": False})
    kb = client.post(f"/api/v2/meta/tables/{A}/kanbans", {"title": "By kind"})
    client.patch(f"/api/v2/meta/kanbans/{kb['id']}", {"fk_grp_col_id": _col(authors, "Kind")["id"]})
    gal = client.post(f"/api/v2/meta/tables/{A}/galleries", {"title": "Faces"})
    client.patch(f"/api/v2/meta/galleries/{gal['id']}", {"fk_cover_image_col_id": _col(authors, "Photo")["id"]})
    form = client.post(f"/api/v2/meta/tables/{A}/forms", {"title": "Sign up"})
    client.patch(f"/api/v2/meta/forms/{form['id']}", {"heading": "Join us", "subheading": "Two fields", "success_msg": "Thanks"})
    fcol = next(c for c in client.get(f"/api/v2/meta/forms/{form['id']}")["columns"] if c["fk_column_id"] == _col(authors, "Name")["id"])
    client.patch(f"/api/v2/meta/form-columns/{fcol['id']}", {"label": "Your name", "help": "as printed", "required": True})
    client.patch(f"/api/v2/meta/views/{client.views(Bk)[0]['id']}", {"title": "All books"})
    return bid


def view_summary(client, base_id):
    """Details verify() does not look at: filter trees, hidden columns, kanban
    grouping, gallery cover, form texts. Column ids are replaced by titles."""
    out = {}
    for t in client.tables(base_id):
        meta = client.table(t["id"])
        title = {c["id"]: c["title"] for c in meta["columns"]}
        for v in client.views(t["id"]):
            key = f"{t['title']}/{v['title']}"
            item = {"type": v["type"]}
            if v["type"] != 1:
                def tree(fs):
                    res = []
                    for f in fs:
                        e = [title.get(f.get("fk_column_id")), f.get("comparison_op"), f.get("value"), f.get("logical_op"), bool(f.get("is_group"))]
                        if f.get("is_group"):
                            e.append(tree(client.get(f"/api/v2/meta/filters/{f['id']}/children")["list"]))
                        res.append(e)
                    return res
                item["filters"] = tree(client.get(f"/api/v2/meta/views/{v['id']}/filters")["list"])
                item["sorts"] = [[title.get(s["fk_column_id"]), s["direction"]] for s in client.get(f"/api/v2/meta/views/{v['id']}/sorts")["list"]]
                item["hidden"] = sorted(title.get(c["fk_column_id"]) for c in client.get(f"/api/v2/meta/views/{v['id']}/columns")["list"] if not c.get("show"))
            if v["type"] == 4:
                item["group"] = title.get(client.get(f"/api/v2/meta/kanbans/{v['id']}").get("fk_grp_col_id"))
            if v["type"] == 2:
                item["cover"] = title.get(client.get(f"/api/v2/meta/galleries/{v['id']}").get("fk_cover_image_col_id"))
            if v["type"] == 1:
                d = client.get(f"/api/v2/meta/forms/{v['id']}")
                item["form"] = [d.get("heading"), d.get("subheading"), d.get("success_msg")]
                item["fields"] = sorted(json.dumps([title.get(c["fk_column_id"]), c.get("label"), c.get("help"), bool(c.get("required"))]) for c in d["columns"] if c.get("label"))
            out[key] = item
    return out
