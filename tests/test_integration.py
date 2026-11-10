"""Integration tests against a real NocoDB.

NOCODB_URL / NOCODB_TOKEN: source installation (required, otherwise skipped).
NOCODB_TARGET_URL / NOCODB_TARGET_TOKEN: second installation for the target
(optional; without it the copy goes into a second base of the same installation).
"""

import os
import tempfile
import time
import unittest

import fixture
from nocodb_transfer.client import Client
from nocodb_transfer.transfer import Log, export_base, import_snapshot
from nocodb_transfer.verify import compare

URL, TOKEN = os.environ.get("NOCODB_URL"), os.environ.get("NOCODB_TOKEN")


@unittest.skipUnless(URL and TOKEN, "NOCODB_URL and NOCODB_TOKEN not set")
class RealNocoDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = Client(URL, TOKEN)
        cls.dst = Client(os.environ.get("NOCODB_TARGET_URL") or URL, os.environ.get("NOCODB_TARGET_TOKEN") or TOKEN)
        cls.tag = str(int(time.time()))
        cls.base = fixture.build(cls.src, "fixture-" + cls.tag)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.log = Log(quiet=True)
        cls.manifest = export_base(cls.src, cls.base, cls.tmp.name, cls.log)
        cls.result = import_snapshot(cls.dst, cls.tmp.name, title="copy-" + cls.tag, log=cls.log)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_export_counts(self):
        counts = {t["title"]: self.manifest["counts"][t["id"]] for t in self.manifest["tables"]}
        self.assertEqual(counts, {"Authors": 256, "Books": 40, "Genres": 3, "Staff": 4, "Passports": 3})
        self.assertEqual(self.manifest["links"], 40 + 20 + 3 + 3)  # hm, mm, self hm, oo

    def test_no_warnings(self):
        self.assertEqual(self.log.warnings, [])

    def test_records_links_and_computed_values_match(self):
        diffs, stats = compare(self.src, self.base, self.dst, self.result["base_id"])
        self.assertEqual(diffs, [])
        self.assertEqual(stats["records"], 306)
        self.assertGreater(stats["values"], 5000)

    def test_views_match(self):
        self.assertEqual(fixture.view_summary(self.src, self.base), fixture.view_summary(self.dst, self.result["base_id"]))

    def test_attachment_bytes(self):
        def files(client, base):
            t = next(t for t in client.tables(base) if t["title"] == "Authors")
            out = []
            for r in client.iter_records(t["id"], "Id"):
                for a in r.get("Photo") or []:
                    out.append((r["Name"], a["title"], client.download(a)))
            return out

        a, b = files(self.src, self.base), files(self.dst, self.result["base_id"])
        self.assertEqual(len(a), 7)  # 9 uploaded, one row with 2 files deleted
        self.assertEqual(a, b)

    def test_ids_with_gaps_are_remapped(self):
        # source has deleted rows, so target ids differ; links must follow the records
        mapping = self.result["records"][next(t["id"] for t in self.manifest["tables"] if t["title"] == "Authors")]
        self.assertTrue(any(old != new for old, new in mapping.items()))

    def test_verify_detects_a_change(self):
        t = next(t for t in self.dst.tables(self.result["base_id"]) if t["title"] == "Books")
        self.dst.request("PATCH", f"/api/v2/tables/{t['id']}/records", body=[{"Id": 1, "Pages": 999}])
        try:
            diffs, _ = compare(self.src, self.base, self.dst, self.result["base_id"])
            self.assertTrue(any("Pages" in d for d in diffs), diffs)
            self.assertTrue(any("Total pages" in d for d in diffs), diffs)
        finally:
            self.dst.request("PATCH", f"/api/v2/tables/{t['id']}/records", body=[{"Id": 1, "Pages": 10}])

    def test_import_into_existing_empty_base(self):
        empty = self.dst.create_base("empty-" + self.tag)
        res = import_snapshot(self.dst, self.tmp.name, base_id=empty["id"], log=Log(quiet=True))
        self.assertEqual(res["base_id"], empty["id"])
        diffs, _ = compare(self.src, self.base, self.dst, empty["id"])
        self.assertEqual(diffs, [])
        with self.assertRaises(SystemExit):
            import_snapshot(self.dst, self.tmp.name, base_id=empty["id"], log=Log(quiet=True))


if __name__ == "__main__":
    unittest.main()
