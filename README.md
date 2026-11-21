# Teinté Release 1

Guest-first foundation shade discovery and evidence-led cross-brand comparison for a Philippines-first catalogue. The application is a small Python modular monolith with static responsive pages, server-side APIs, SQLite for local use, and an optional PostgreSQL runtime. No public accounts, photo analysis, persistent profile, analytics, ads, affiliate links, or commerce are included.

## Controlling source and choices

The controlling product source is `Teinte_FINAL_COMPLETE_PRD_v2.5_Frontend_Design_Incorporated.pdf`: its cover says it supersedes v2.4 and preserves its product rules while adding Sections 24–25. The project `claude.md`, `agents.md`, and architecture Markdown still cite v2.4; those references are stale. v2.5 also describes itself as a v2.4 consolidated document in a later operating-rule paragraph, an inherited internal label that conflicts with the cover. This implementation follows the explicit v2.5 cover and its frontend design guide. Frontend design uses the approved mauve/rose/ivory palette, serif wordmark/headlines, editorial whitespace, pill selectors, rounded cards, keyboard-visible focus, and mobile layouts. It omits account and photo-analysis journeys from the concept reference as v2.5 directs.

The supplied files are now loaded into the local catalogue using a source-trusting demo mode requested for this project. Each imported value is labeled `Source-derived`; its source references are preserved and it is not presented as independently verified. Ambiguous product assignments, conflicts, and match endpoints that cannot be resolved from the supplied rows stay out of public matching rather than being guessed. The extraction audit reports 956 shade observations and 4,809 relationship claims; these are source-row counts, not verified unique totals. The source folder lacks the registered `Full Shade Matrix-5.pdf`.

## Run locally

Requires Python 3.10 or later. The local development database uses the standard library only.

```sh
python3 -m venv .venv
. .venv/bin/activate
export TEINTE_ADMIN_PASSWORD='use-a-long-unique-password'
python3 server.py
```

Open `http://127.0.0.1:8000`. Public site: `/`. Protected editorial tools: `/admin.html`. Health check: `/api/health`. The local database is created at `data/teinte.sqlite3`; restrict access to the host and back it up before editorial work.

Set `TEINTE_DB_PATH` to change the local SQLite path. On first start the schema is created from `migrations/001_initial_sqlite.sql`. The app never seeds source rows into the public catalogue automatically.

## Stage source materials

Keep the supplied source directory read-only. Run the repeatable importer with explicit paths:

```sh
python3 import_sources.py \
  --source-dir '/Users/glaireann/Downloads/Final Teinte/Teinte_Complete_Shade_Catalogue_and_Matching_Database' \
  --master-catalogue '/Users/glaireann/Downloads/Final Teinte/Teinte_Master_Catalogue.xlsx'
```

It registers PDF/workbook checksums, stores original CSV rows and workbook cell-address values in `staged_record`, records source file and row provenance, and uses stable IDs so reruns do not duplicate rows. It does not resolve identities, normalize shade descriptions, create match edges, or publish data. Inspect `data/import-report.json` after each run; correct and retry any reported row errors. The original inputs remain untouched.

To load the provided catalogue into the app without typing products and shades one by one, run:

```sh
python3 seed_catalogue.py
```

This preloads the 26 listed products, all 956 shade observations, and source claims whose directed endpoints can be matched to those shades. Source matrix IDs link each shade row to its product anchor. Duplicate observations for the same product and shade are combined with source references retained. Conflicting depth/undertone values remain blank. The script uses stable IDs and is safe to rerun. Its summary is written to `data/catalogue-seed-report.json`. The current load contains 713 distinct product/shade records and 217 usable directed claims. Other claims remain in staging when the source or target shade cannot be uniquely matched. Imported profile results say they are source-derived and have not been independently checked.

In the Admin page, review staged records and assign a disposition/reason; these dispositions do not create canonical published facts. Add canonical brand/product/shade records only when their exact identity and field-specific evidence are reviewed. Unknown attributes remain null. Product and shade names/codes are never used as identifiers.

## Admin and publication

Set `TEINTE_ADMIN_PASSWORD` in the server environment. Admin sign-in uses an HttpOnly, SameSite=Strict session cookie with an eight-hour expiry and a hashed server-side token. Login attempts are rate-limited in-process. Set `TEINTE_SECURE_COOKIES=1` behind HTTPS. There is no public signup or password value in source files.

The Admin page supports staged-row disposition, correction queue review, brand/product/shade entry, shade edits, archive, claim entry, publication QA, and audit inspection. The preloaded source-derived catalogue is already available to the public site and visible in Admin. The server exposes no hard-delete route. Rollback is available as an audited compensating change through `POST /api/admin/rollback` with an `event_id` and reason; original audit events remain intact.

Correction reports are anonymous by default, rate-limited to five per source address per hour, size-limited, and never modify catalogue data. Optional contact data is visible only to authenticated admins. Its retention period is 90 days. Configure the daily cleanup command below; until it runs, expired contact details may remain stored. Keep database files/backups access-controlled and encrypted at rest in production.

## Matching and public data

Known-shade selection asks for product type, then brand/product, then shade. Results use a short classification: “Exact match (as labeled by source)” only when the source says exact; other claims are labeled “Not exact” with the source’s category. The original source wording and its file/line reference remain visible. Claims are never reversed or chained.

Manual-profile matching validates one of seven ordered depths and one of four undertones. Results are labeled “Exact profile match” when both match, or “Nearby profile match” when undertone matches and depth is adjacent. Imported source-derived values are shown with a clear note that they were not independently checked. Unknown undertone is not Neutral. Input is request-scoped and not stored. No photo, account, or user history is collected. Match snapshots are not enabled in Release 1.

## Data structures used in the application

`data_structures.py` contains small request-scoped structures. SQLite/PostgreSQL remains the durable source of truth; these in-memory structures organize results without changing database records.

- **Lists (arrays):** Python lists collect search/profile results and match records. Search appends eligible rows, sorts them, and returns up to 80 results. Scanning `n` candidate rows is O(n); sorting `r` results is O(r log r).
- **Correction queue:** `FIFOQueue` uses `collections.deque` (first in, first out). The admin overview loads the oldest 100 pending correction reports in timestamp/ID order and drains that bounded queue into the response. The database keeps the full queue; after reviewed items are closed, the next oldest items appear. Enqueue/dequeue are O(1); loading/sending `k` rows is O(k). Closed reports are shown after pending reports.
- **Directed match graph:** `DirectedMatchGraph` uses an adjacency list keyed by source shade ID. The known-shade endpoint loads only published, resolvable outgoing claims for the requested shade, then returns its direct neighbors. It never reverses an edge or follows a path transitively. Looking up a node is O(1) average plus O(d) to copy its `d` outgoing claims.
- **Catalogue tree and recursion:** `/api/catalogue/tree` loads published brand/product/shade rows, builds a three-level tree keyed by stable IDs, and recursively serializes it. The public catalogue’s “Browse by brand and product” control requests this endpoint and recursively renders collapsible branches. Building and serializing `n` shade rows is O(n); recursion depth is fixed at three levels, so it is bounded and does not depend on user-provided nesting.

The algorithms are deliberately limited to the actual product needs: the queue preserves review order, the graph represents directed evidence-backed claims, and the tree supports hierarchical catalogue browsing. A linked list is not used because random lookup and database-backed records are a better fit for this application.

## PostgreSQL deployment

Install the production driver with `pip install -r requirements.txt`, provision a managed PostgreSQL database, and set `TEINTE_DATABASE_URL` to its TLS-required connection string. Set `TEINTE_SECURE_COOKIES=1`, `TEINTE_ADMIN_PASSWORD`, and a persistent writable `TEINTE_DB_PATH` only if using SQLite locally. Apply the idempotent `migrations/002_postgres.sql` before starting the server. The runtime uses Postgres when `TEINTE_DATABASE_URL` is set. Keep development, staging, and production credentials/databases separate. Do not put secrets in the repository or browser.

Run behind an HTTPS reverse proxy with request body limits and host-level rate limits. Configure the proxy to pass only trusted forwarded headers; the app itself does not trust arbitrary proxy headers. Ensure the host supports persistent connections, database backups, Python 3.10+, TLS to PostgreSQL, and controlled scheduled jobs. Keep admin access restricted. The standard-library server is intended for a small single-app deployment behind that proxy; validate expected traffic, worker timeouts, platform limits, and security review before public launch.

## Backup, restore, and recovery

For PostgreSQL, enable daily provider backups with a target 30-day retention when available, and schedule an independent encrypted `pg_dump --format=custom` to access-controlled storage. Test restore to a separate database before launch and periodically. Example:

```sh
pg_dump --format=custom --file=teinte-backup.dump "$TEINTE_DATABASE_URL"
createdb teinte_restore_check
pg_restore --dbname=teinte_restore_check --clean --if-exists teinte-backup.dump
```

For local SQLite, stop the server and copy `data/teinte.sqlite3` to encrypted storage. Restore by replacing the file while the service is stopped. Restore the database and application version together; verify `/api/health`, admin login, public empty/published states, and an explicit known-shade/profile query. If a release or data edit is faulty, redeploy the prior application version or perform an audited admin rollback. Never delete a referenced source row to repair production data.

Schedule daily cleanup:

```sh
python3 maintenance.py
```

The job removes correction contact values past their retention date and expired admin sessions. No share snapshots are stored or need purging.

## Environment variables

| Variable | Purpose |
|---|---|
| `TEINTE_ADMIN_PASSWORD` | Protected admin password; required for admin login. |
| `TEINTE_SECURE_COOKIES` | Set to `1` in HTTPS production. |
| `TEINTE_DB_PATH` | SQLite database path when PostgreSQL is not configured. |
| `TEINTE_DATABASE_URL` | PostgreSQL TLS connection string; switches runtime to PostgreSQL. |
| `HOST` | Bind address; defaults to `127.0.0.1`. |
| `PORT` | HTTP port; defaults to `8000`. |

## Checks and current limitations

Run `python3 -m py_compile server.py data_structures.py import_sources.py seed_catalogue.py maintenance.py` and `python3 -m unittest discover -s tests -v`. Run the app and `/api/health` for a local smoke check. The supplied data is loaded in source-derived mode as requested; it is not independently verified, and unresolved rows are not matchable. Before public launch, resolve the missing source matrix if needed, identify the data controller/contact, complete Philippine privacy/legal review, configure HTTPS/backups/restore and daily retention cleanup, and complete accessibility/performance/browser review.
