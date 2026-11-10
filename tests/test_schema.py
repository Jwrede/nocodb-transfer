"""Offline tests on table metadata captured from a real NocoDB (tests/data/tables.json,
written by the fixture in tests/fixture.py against NocoDB 2026.10.1)."""

import json
import os
import unittest

from nocodb_transfer import schema as S
from nocodb_transfer.verify import normalize

with open(os.path.join(os.path.dirname(__file__), "data", "tables.json")) as fh:
    DATA = json.load(fh)
TABLES = DATA["tables"]
BY_TITLE = {t["title"]: t for t in TABLES}


def col(table, title):
    return next(c for c in BY_TITLE[table]["columns"] if c["title"] == title)


class Relations(unittest.TestCase):
    def setUp(self):
        self.rels = S.relations(TABLES)

    def find(self, parent, child, typ):
        pid, cid = BY_TITLE[parent]["id"], BY_TITLE[child]["id"]
        found = [r for r in self.rels if r["parent"] == pid and r["child"] == cid and r["type"] == typ]
        self.assertEqual(len(found), 1, (parent, child, typ))
        return found[0]

    def test_one_relation_per_link_pair(self):
        self.assertEqual(len(self.rels), 4)

    def test_has_many_pairs_with_renamed_belongs_to(self):
        r = self.find("Authors", "Books", "hm")
        self.assertEqual(r["sides"]["parent"]["title"], "Books")
        self.assertEqual(r["sides"]["child"]["title"], "Written by")

    def test_many_to_many(self):
        r = self.find("Books", "Genres", "mm")
        self.assertEqual(r["sides"]["parent"]["title"], "Genres")
        self.assertEqual(r["sides"]["child"]["title"], "Books")

    def test_self_link(self):
        r = self.find("Staff", "Staff", "hm")
        self.assertEqual(r["sides"]["parent"]["title"], "Reports")
        # NocoDB marks the belongs-to side of a self link as a system column
        self.assertIsNone(r["sides"]["child"])

    def test_one_to_one_parent_is_side_without_foreign_key(self):
        r = self.find("Authors", "Passports", "oo")
        self.assertEqual(r["sides"]["parent"]["title"], "Passport")
        self.assertEqual(r["sides"]["child"]["title"], "Authors")


class DerivedOrder(unittest.TestCase):
    def test_dependencies_first(self):
        order = [c["title"] for _, c in S.derived_order(TABLES)]
        for before, after in [("Author name", "Label"), ("Label", "Shout"), ("Label", "QR"), ("Total pages", "Pages per star")]:
            self.assertLess(order.index(before), order.index(after), (before, after))
        self.assertEqual(len(order), len(set(order)))

    def test_cycle_is_reported(self):
        a = {"id": "ca", "title": "a", "uidt": "Formula", "colOptions": {"formula": "{{cb}}"}}
        b = {"id": "cb", "title": "b", "uidt": "Formula", "colOptions": {"formula": "{{ca}} + 1"}}
        with self.assertRaises(ValueError):
            S.derived_order([{"id": "t", "title": "t", "columns": [a, b]}])


class Specs(unittest.TestCase):
    def test_formula_uses_titles(self):
        c = col("Books", "Label")
        title_of = {x["id"]: x["title"] for t in TABLES for x in t["columns"]}
        self.assertEqual(S.formula_with_titles(c["colOptions"]["formula"], title_of), 'CONCAT({Title}, " by ", {Author name})')

    def test_unknown_formula_reference(self):
        with self.assertRaises(KeyError):
            S.formula_with_titles("{{cnope}}", {})

    def test_select_options_keep_title_and_color_only(self):
        spec = S.scalar_spec(col("Authors", "Kind"))
        self.assertEqual(spec["colOptions"]["options"][0], {"title": "poet", "color": "#ff0000", "order": 1})
        self.assertEqual(spec["colOptions"]["options"][2]["title"], 'Grüße, "quoted"')

    def test_lookup_and_rollup_map_column_ids(self):
        title_of = {}
        lk = col("Books", "Author name")
        colmap = {lk["colOptions"]["fk_relation_column_id"]: "new-rel", lk["colOptions"]["fk_lookup_column_id"]: "new-name"}
        spec = S.derived_spec(lk, colmap, title_of)
        self.assertEqual((spec["fk_relation_column_id"], spec["fk_lookup_column_id"]), ("new-rel", "new-name"))
        ro = col("Authors", "Total pages")
        colmap = {ro["colOptions"]["fk_relation_column_id"]: "r", ro["colOptions"]["fk_rollup_column_id"]: "p"}
        self.assertEqual(S.derived_spec(ro, colmap, title_of)["rollup_function"], "sum")

    def test_kinds(self):
        a = BY_TITLE["Authors"]
        kinds = {c["title"]: S.kind(c) for c in a["columns"]}
        self.assertEqual(kinds["Id"], "pk")
        self.assertEqual(kinds["CreatedAt"], "system")
        self.assertEqual(kinds["Books"], "relation")
        self.assertEqual(kinds["Total pages"], "derived")
        self.assertEqual(kinds["Photo"], "scalar")
        fks = [c for t in TABLES for c in t["columns"] if c["uidt"] == "ForeignKey"]
        self.assertTrue(fks)
        self.assertTrue(all(S.kind(c) == "system" for c in fks))


class Normalize(unittest.TestCase):
    def test_attachment_compares_title_size_type(self):
        c = {"uidt": "Attachment"}
        a = [{"path": "download/x/a.txt", "title": "a.txt", "size": 3, "mimetype": "text/plain", "signedPath": "x"}]
        b = [{"path": "download/y/a_1.txt", "title": "a.txt", "size": 3, "mimetype": "text/plain"}]
        self.assertEqual(normalize(c, a, set()), normalize(c, b, set()))

    def test_nested_ids_are_ignored(self):
        c = {"uidt": "LinkToAnotherRecord"}
        self.assertEqual(normalize(c, {"Id": 4, "Name": "x"}, {"Id"}), normalize(c, {"Id": 9, "Name": "x"}, {"Id"}))

    def test_json_and_numbers(self):
        self.assertEqual(normalize({"uidt": "JSON"}, '{"a": 1}', set()), {"a": 1})
        self.assertEqual(normalize({"uidt": "Number"}, 3, set()), normalize({"uidt": "Number"}, 3.0, set()))
        self.assertIs(normalize({"uidt": "Checkbox"}, 1, set()), True)


if __name__ == "__main__":
    unittest.main()
