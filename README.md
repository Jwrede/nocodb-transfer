# nocodb-transfer

Copy a [NocoDB](https://github.com/nocodb/nocodb) base from one installation to another, or
write it to a directory and restore it later. The copy includes the schema, records, links
(has-many, many-to-many, one-to-one, self links), lookups, rollups, formulas, barcode and QR
columns, attachments and views (grid, gallery, kanban, form) with their filters, sorts and
hidden fields. A `verify` command then compares source and target record by record, including
the values NocoDB computes on the target (link counts, lookups, rollups, formulas).

It talks to the public NocoDB REST API with an API token. Python 3.9 or newer, no dependencies.

## Why

NocoDB can import from Airtable and can migrate a self-hosted base to NocoDB Cloud, but it has
no way to move a base from one installation to another, or from Cloud back to a self-hosted
server. CSV export works per table and loses links, lookups, rollups, formulas and views.

- [nocodb/nocodb #9596](https://github.com/nocodb/nocodb/issues/9596) "Ability to cross copy
  bases across installations": 81 reactions, open since October 2024, no answer from the
  maintainers. Comments ask for it for disaster recovery ("Due to the lack of an option to
  export the database, there is a feeling of insecurity"), for leaving NocoDB Cloud ("I was
  planning to then self-host if I liked it ... This is a deal breaker"), and for handing a
  template base to hundreds of other users. One comment notes that moving to and from
  Airtable is easier than moving between NocoDB installations.
- [Community forum](https://community.nocodb.com/t/how-to-export-all-databases-from-nocodb-cloud-to-self-hosted/1409):
  "How to export all databases from NocoDB Cloud to self-hosted", linked from the issue.
- [nocodb/nocodb #8012](https://github.com/nocodb/nocodb/issues/8012) asks for export access
  without backend access; [#384](https://github.com/nocodb/nocodb/issues/384) asked for a full
  backup feature in 2021.
- The only related code found on GitHub, piappl/nocodb_export (two scripts, December 2025),
  states in its README that link, lookup and rollup columns are not included.

## Install

```sh
pipx install git+https://github.com/Jwrede/nocodb-transfer
# or: uv tool install git+https://github.com/Jwrede/nocodb-transfer
```

Create an API token in each installation (Account settings, Tokens).

## Usage

```sh
# installation to installation, then compare
export NOCODB_FROM_URL=https://old.example.com NOCODB_FROM_TOKEN=...
export NOCODB_TO_URL=https://new.example.com   NOCODB_TO_TOKEN=...
nocodb-transfer copy --base "CRM" --verify

# backup to a directory, restore later
NOCODB_URL=https://nocodb.example.com NOCODB_TOKEN=... nocodb-transfer export --base "CRM" --out crm-2026-11-10
NOCODB_URL=https://new.example.com NOCODB_TOKEN=... nocodb-transfer import crm-2026-11-10 --title "CRM restored"

# import into an existing, empty base (for installations where the token cannot create bases)
nocodb-transfer import crm-2026-11-10 --base-id p5molgg1ngzfsnb

# compare two bases at any time
nocodb-transfer verify --base CRM --to-base p5molgg1ngzfsnb
```

`--base` takes a base id or title. `verify` and `copy --verify` exit with status 1 when they
find a difference.

## Example

Output of the CLI copy between two separate local NocoDB 2026.10.1 installations, using the
test base from `tests/fixture.py` (five tables, 17 plain field types, four kinds of links, derived
columns that depend on each other, deleted rows, attachments, nine views):

```
$ nocodb-transfer copy --base Fixture1 --title Fixture1 --verify
exported Authors: 256 records
exported Books: 40 records
exported Genres: 3 records
exported Staff: 4 records
exported Passports: 3 records
exported 5 tables, 306 records, 66 links, 2 files
importing into base Fixture1 (p5molgg1ngzfsnb)
imported Authors: 256 records
imported Books: 40 records
imported Genres: 3 records
imported Staff: 4 records
imported Passports: 3 records
imported 66 links
done: base p5molgg1ngzfsnb, 121 API requests
compared 5 tables, 306 records, 6012 values
no differences
```

## How it works

Export reads the table metadata, all records (1000 per page), the links of every relation
and the attachment files, and writes a snapshot directory:

```
manifest.json          tables and columns as NocoDB returns them, views with filters,
                       sorts, field visibility and kanban/gallery/form settings
records/<table>.jsonl  one record per line, writable fields only
links/<relation>.jsonl source record id -> linked record ids
files/<sha256>         attachment contents, deduplicated
```

Import rebuilds the base in the order NocoDB needs:

1. tables with their plain columns (select options keep their colours);
2. relations: each pair of link columns is created once from the parent side, then the
   generated reverse column gets the source title back;
3. lookups, rollups, formulas, barcode and QR columns, sorted so that a column is created
   after the columns it depends on (a formula over a lookup over a link works); formulas are
   rewritten from NocoDB's internal `{{column id}}` form to `{Title}`;
4. records in batches of 100, attachments re-uploaded, keeping a map from source to target
   record ids (ids differ when the source has deleted rows);
5. links through the links API, translated with that map;
6. views: the default grid view is renamed, the others are created, then filters (including
   nested filter groups), sorts, field visibility and order, kanban grouping, gallery cover,
   form texts and form field labels.

Verify reads both bases and compares table and column names and types, view names and types
with their filter and sort counts, and every field of every record in id order, including
computed fields. Attachments compare by title, size and type; record ids inside link values
are ignored.

## Tests

- `tests/test_schema.py`: offline tests on metadata captured from a real NocoDB 2026.10.1
  (relation pairing for hm, mm, oo and self links, dependency order, formula rewriting).
- `tests/test_integration.py`: builds the fixture base in a real NocoDB, exports it, imports
  it into a second installation and checks: no differences from `verify`, identical view
  details (filter trees, sorts, hidden fields, kanban grouping, gallery cover, form texts),
  identical attachment bytes, remapped record ids, `verify` finding a changed value and the
  rollup and formula that depend on it, and import into an existing empty base.
- CI runs the integration tests between two NocoDB containers, 2026.10.1 to 2026.10.1 and
  latest to latest, weekly as well. The NocoDB 2026.10.1 Docker image stores one-to-one links
  on a junction table, the npm build of the same version used for local tests on a foreign
  key; both forms are handled and tested.

```sh
PYTHONPATH=src:tests python -m unittest discover -s tests
NOCODB_URL=... NOCODB_TOKEN=... [NOCODB_TARGET_URL=... NOCODB_TARGET_TOKEN=...] \
  PYTHONPATH=src:tests python -m unittest tests.test_integration
```

## Limits

- Not copied: users, roles and base sharing, API tokens, webhooks, automations and scripts,
  Button columns, comments, audit history, calendar and map views, row colouring and other
  view settings not listed above.
- Values of User fields are not copied (users differ between installations); the column is
  created empty. CreatedAt, UpdatedAt, CreatedBy and auto numbers get new values on the target.
- Only NocoDB 2026.10 and later were tested. Building the test base on 0.263.8 failed (the
  link API of that version rejects the requests), so older sources are untested.
- Only bases on NocoDB's default data source were tested. Bases on an external database
  connection, and composite primary keys, are not supported.
- NocoDB Cloud was not tested. The client retries HTTP 429 and 5xx with backoff; on Cloud,
  create an empty base in the UI and use `--base-id`.
- One request per record that has links, so large, densely linked bases take a while.
  Attachment files are held in memory one at a time.
- The snapshot directory contains all data as plain JSON. Protect it like a database dump.

## License

MIT
