"""nocodb-transfer: export, import, copy and verify NocoDB bases across installations."""

import argparse
import os
import sys
import tempfile

from . import __version__
from .client import Client
from .transfer import Log, export_base, import_snapshot
from .verify import compare


def _client(url, token, which):
    url = url or os.environ.get(f"NOCODB_{which}URL") or os.environ.get("NOCODB_URL")
    token = token or os.environ.get(f"NOCODB_{which}TOKEN") or os.environ.get("NOCODB_TOKEN")
    if not url or not token:
        name = which.lower().rstrip("_") or "nocodb"
        raise SystemExit(f"{name}: need a URL and an API token (flags or NOCODB_{which}URL / NOCODB_{which}TOKEN)")
    return Client(url, token)


def main(argv=None):
    p = argparse.ArgumentParser(prog="nocodb-transfer", description=__doc__)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="write a base (schema, records, links, files, views) to a directory")
    e.add_argument("--url"), e.add_argument("--token")
    e.add_argument("--base", required=True, help="base id or title")
    e.add_argument("--out", required=True, help="snapshot directory")
    e.add_argument("--no-files", action="store_true", help="skip attachment files")

    i = sub.add_parser("import", help="create a base from a snapshot directory")
    i.add_argument("snapshot")
    i.add_argument("--url"), i.add_argument("--token")
    g = i.add_mutually_exclusive_group()
    g.add_argument("--base-id", help="import into this existing, empty base (needed on NocoDB Cloud)")
    g.add_argument("--title", help="title of the new base (default: source title)")

    c = sub.add_parser("copy", help="export from one installation and import into another")
    c.add_argument("--from-url"), c.add_argument("--from-token")
    c.add_argument("--to-url"), c.add_argument("--to-token")
    c.add_argument("--base", required=True, help="source base id or title")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--to-base-id", help="existing, empty target base")
    g.add_argument("--title", help="title of the new target base")
    c.add_argument("--keep", help="keep the intermediate snapshot in this directory")
    c.add_argument("--verify", action="store_true", help="compare source and target afterwards")

    v = sub.add_parser("verify", help="compare two bases record by record")
    v.add_argument("--from-url"), v.add_argument("--from-token")
    v.add_argument("--to-url"), v.add_argument("--to-token")
    v.add_argument("--base", required=True), v.add_argument("--to-base", required=True)

    a = p.parse_args(argv)
    log = Log()
    if a.cmd == "export":
        export_base(_client(a.url, a.token, ""), a.base, a.out, log, with_files=not a.no_files)
    elif a.cmd == "import":
        import_snapshot(_client(a.url, a.token, ""), a.snapshot, base_id=a.base_id, title=a.title, log=log)
    elif a.cmd == "copy":
        src, dst = _client(a.from_url, a.from_token, "FROM_"), _client(a.to_url, a.to_token, "TO_")
        with tempfile.TemporaryDirectory() as tmp:
            d = a.keep or tmp
            m = export_base(src, a.base, d, log)
            res = import_snapshot(dst, d, base_id=a.to_base_id, title=a.title, log=log)
        if a.verify:
            return _report(*compare(src, m["source"]["base_id"], dst, res["base_id"]), log)
    elif a.cmd == "verify":
        src, dst = _client(a.from_url, a.from_token, "FROM_"), _client(a.to_url, a.to_token, "TO_")
        return _report(*compare(src, a.base, dst, a.to_base), log)
    if log.warnings:
        print(f"{len(log.warnings)} warning(s)", file=sys.stderr)
    return 0


def _report(diffs, stats, log):
    print(f"compared {stats['tables']} tables, {stats['records']} records, {stats['values']} values")
    for d in diffs:
        print("diff: " + d)
    print("no differences" if not diffs else f"{len(diffs)} difference(s)")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
