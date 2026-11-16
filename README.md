# Emergency Blood Management System (EBMS)

Academic project combining DBMS and Operating Systems concepts. Hospital requests run as processes, blood units are shared resources, and reservations are database transactions with an audit trail. OS algorithms run in C; Python/Flask orchestrates and stores everything in SQLite.

**Not for clinical use.** Blood-compatibility, inventory, and expiry rules are medical workflow rules enforced by the database layer. The Banker's algorithm is an OS resource-allocation simulation and does not replace them.

## 1. Project overview
- Hospitals create blood requests (processes). Each request claims units of one component type.
- A scheduler picks the highest emergency tier (Critical > High > Normal), breaks ties by earliest arrival, then reserves the earliest-expiring compatible units in one transaction.
- Before a reservation is approved, the C Banker's engine checks that granting it keeps the system in a safe state.
- A separate "OS Simulation (C Algorithms)" section runs CPU scheduling, Banker's, deadlock detection, producer-consumer, and readers-writers on real C code.

## 2. Architecture
```
Browser (HTML/CSS/JS)  --JSON-->  Flask (backend/app.py)  --SQL-->  SQLite (ebms.db)
                                      |                            (engine.py, db.py)
                                      |  os_bridge.py: argument list, no shell,
                                      |  size + timeout limits, strict output checks
                                      v
                              C engine: os_algorithms/bin/ebms_os[.exe]
                              (scheduler, banker, deadlock, producer_consumer, readers_writers)
```
Python owns all database access and transactions. C owns only the OS algorithms and never touches SQL.

## 3. Technology stack
- C11 (gcc on Windows via MinGW-w64; gcc on Linux), pthreads on Linux, Win32 threads on Windows
- Python 3, Flask 3 (JSON API and static frontend), ReportLab for PDF reports, standard library `sqlite3` and `subprocess`
- SQLite with `BEGIN IMMEDIATE` transactions, CHECK/FK/UNIQUE constraints, partial unique index
- Vanilla HTML, CSS, JavaScript (no build step)

## 4. Folder structure
```
ebms/
  README.md
  requirements.txt                 Flask + ReportLab
  backend/
    app.py                         Flask routes (DB API + OS simulation API)
    engine.py                      matching, reservation transaction, scheduler, intake, expiry, Banker's gate
    os_bridge.py                   Python -> C subprocess bridge (validation, timeouts, error mapping)
    banker.py                      Python reference Banker's (tests and differential check only)
    db.py                          SQLite connections and BEGIN IMMEDIATE helper
    schema.sql                     core + additive final-phase tables and constraints
    final_phase.py                 dashboard, analytics, compatibility, audit, PDF and additive schema helpers
    seed.py                        demo data: blood groups, components, eight hospitals, units, requests
    migrate.py                     safe, repeatable hospital and final-phase schema upgrade
    rwlock.py                      in-process readers-writers lock guarding DB reads and writes
    ebms.db                        database, already upgraded to the eight-hospital list
    test_core.py                   14 original tests (unchanged)
    test_os_bridge.py              bridge failure modes, real-engine checks, Python-vs-C Banker's comparison
    test_upgrade.py                hospitals, urgency mapping, reservation gate, Flask API smoke tests
    test_final_phase.py            compatibility, dashboard, reporting, RBAC and transfer regression tests
  os_algorithms/
    include/                       common.h, json.h, ebms_sync.h, scheduler.h, banker.h, deadlock.h,
                                   producer_consumer.h, readers_writers.h
    src/                           main.c, common.c, json_parser.c, scheduler.c, banker.c, deadlock.c,
                                   producer_consumer.c, readers_writers.c
    tests/                         ctest.h, test_json.c, test_scheduler.c, test_banker.c, test_deadlock.c,
                                   test_producer_consumer.c, test_readers_writers.c
    Makefile                       Linux/macOS (and MinGW make) build and test targets
    build.ps1                      Windows PowerShell build and test script
  frontend/
    index.html                     page, including the new OS Simulation section
    style.css                      existing styles plus a small OS-panel block
    app.js                         existing app logic plus five OS panels
```

## 5. DBMS implementation
- **Schema:** preserves the original tables and adds `users`, `hospital_transfer`, `transfer_item`, `transfer_history`, and `request_history`; `blood_unit.hospital_id` assigns stock to a hospital for transfers.
- **Constraints:** CHECK on statuses, urgencies, quantities and date order; FKs everywhere; `UNIQUE` on hospital name.
- **No double booking:** partial unique index `uq_active_unit` allows at most one ACTIVE reservation per unit.
- **Indexes:** `idx_units_match` for compatibility lookup, `idx_requests_ready` for the ready queue, `idx_audit_entity_time` for audit history.
- **Transactions:** `db.transaction()` opens `BEGIN IMMEDIATE`, so check-then-write steps are race-free. A failed reservation rolls back fully and records the reason in the audit trail.

## 6. OS modules implemented in C
| Module | What is implemented | Where |
|---|---|---|
| CPU scheduling | FCFS, SJF (NP), SRTF, Priority (NP and P), Round Robin, Gantt, idle intervals, CT/TAT/WT/RT, averages | `src/scheduler.c` |
| Banker's algorithm | Safety check, safe sequence, resource-request decision, invalid-state rejection | `src/banker.c` |
| Deadlock detection | Wait-for graph, DFS cycle detection, victim selection, self-loops, resource-model assumptions | `src/deadlock.c` |
| Producer-consumer | Bounded buffer with real threads, mutex and two condition variables, safe termination, invariant checks | `src/producer_consumer.c` |
| Readers-writers | Writer-preference lock with real threads, concurrent readers, exclusive writers, invariant checks | `src/readers_writers.c` |

Shared pieces: `src/json_parser.c` (bounded JSON parser), `src/common.c` (output buffer, error helpers), `src/main.c` (single command-line entry point), `include/ebms_sync.h` (threads and locks for Linux and Windows).

## 7. CPU scheduling algorithms
- **Tick-based simulation:** time advances one unit at a time, so idle periods, preemption, and quantum expiry are exact.
- **Formulas:** TAT = CT − AT, WT = TAT − BT, RT = first CPU start − AT.
- **Tie-breaking:** each policy compares (primary key, arrival time, input order). FCFS uses arrival. SJF uses burst. SRTF uses remaining time. Priority uses the priority value, where lower means higher priority. Round Robin uses a FIFO queue, and a process arriving on the same tick a quantum expires enters before the preempted process.
- **Edge cases tested:** simultaneous arrivals, CPU idle at start and between arrivals, equal remaining times, quantum larger than burst, invalid pids, duplicates, zero burst, missing quantum or priority, and totals over the output limit.
- **Limits:** up to 100 processes, arrival ≤ 100000, burst ≤ 10000, total burst ≤ 5000 ticks.

## 8. Banker's algorithm
- **Safety:** repeatedly finds a process whose remaining need fits the work vector, adds its allocation back, and reports the safe sequence (or "unsafe").
- **Request decision:** rejects a request that exceeds declared need, rejects one that exceeds available, and otherwise tentatively grants it on copies and rejects it if the resulting state is unsafe.
- **Invalid states:** allocation above maximum, negative or out-of-range values, and mismatched matrix shapes are errors, never silently repaired.
- **In reservations:** a resource type is a component (RBC, PLT, FFP). Each active request is one process. The check runs inside the reservation transaction. An unsafe or failed check means no approval.
- **Not a deadlock proof:** an unsafe state only means completion is not guaranteed.

## 9. Deadlock detection
- **Model:** single-instance resources by default. A wait-for edge P → Q means P waits for a resource held by Q. A directed cycle is a deadlock.
- **Multi-instance:** accepted, but a cycle is reported as "cycle found, not confirmed" because a cycle is necessary but not sufficient when resources have several instances.
- **Victim:** the cycle member holding the fewest units, ties broken by input order.
- **Input checks:** unknown processes, duplicate edges or processes, bad names, negative held units, and unknown resource models are rejected.

## 10. Producer-consumer synchronization
- **Buffer:** fixed capacity, circular array guarded by one mutex.
- **Waiting:** `not_full` (producers sleep when full), `not_empty` (consumers sleep when empty).
- **Termination:** producers finish, main joins them, sets `closed`, and consumers drain the buffer and exit. Thread-creation failures set `aborted` and wake everyone.
- **Checks:** every item is `producer*N + seq`, so duplicates, losses, unknown items, and per-producer reordering are detected. Checksums must match.

## 11. Readers-writers synchronization
- **Policy:** writer preference. A reader waits if a writer is active or waiting. A writer waits while any reader or writer is active.
- **Starvation:** a continuous stream of writers can starve readers. This is documented in the output and in the code.
- **Checks:** writer exclusion, torn-read detection (`a == b` under the lock), operation counts, and final version.

## 12. Python-to-C communication
- `os_bridge.run_module(module, payload)` writes one JSON object to stdin and reads one JSON object from stdout.
- **Safety:** `subprocess.run` with an argument list and no shell. Payload is validated and capped at 64 KB before execution. Timeout is 5 s (10 s for the threaded modules).
- **Exit codes:** 0 = ok, 2 = invalid input (JSON error on stdout), 3 = internal failure.
- **Failure mapping:** missing executable → unavailable, timeout → timeout, nonzero exit → failed, malformed or `ok:false` output → failed or malformed. Stderr is logged server-side and never returned.
- **No per-request compile:** the engine is built once with `build.ps1` or `make`.

## 13. Flask API overview
| Method and path | Purpose |
|---|---|
| GET `/api/meta` | hospitals (eight names), components, blood groups, dropdown urgencies |
| GET `/api/inventory` | available units by component and group |
| GET `/api/requests` | request queue (ROUTINE shown as SCHEDULE) |
| POST `/api/requests` | create a process (201) |
| POST `/api/scheduler/tick` | run one reservation tick (Banker's gate included) |
| POST `/api/donations` | intake (producer), wakes WAITING requests |
| POST `/api/expiry/run` | expiry monitor |
| GET `/api/banker` | live safety check from the C engine (`safe: null` if the check could not run) |
| GET `/api/audit`, `/api/query-plan` | filtered/paginated audit log with CSV/PDF export and query plan |
| GET `/api/dashboard`, `/api/analytics` | database-backed dashboard and analytics data |
| POST `/api/matching` | component-aware compatibility simulation with FEFO-ordered stock |
| GET `/api/expiry` | expiring/expired units with group/date filters |
| GET `/api/requests/<id>/history` | request timeline and assigned units |
| GET/POST `/api/transfers`, PATCH `/api/transfers/<id>` | reserve and progress inter-hospital transfers |
| GET `/api/reports/<type>` | downloadable PDF reports |
| POST `/api/auth/setup`, `/api/auth/login`, `/api/auth/logout`, `/api/users` | bootstrap admin, session login, and role-based user management |
| GET `/api/os/status` | whether the C engine is built |
| POST `/api/os/scheduler`, `/api/os/banker`, `/api/os/deadlock`, `/api/os/producer-consumer`, `/api/os/readers-writers` | OS simulations |

Errors: 400 for invalid input, 503 when the engine is not built, 504 on timeout, 500 for engine failure. Error text never includes file paths.

## 14. Database transaction integration
Reservation flow in `engine.reserve_for_request`:
1. Validate the request exists and is READY or WAITING.
2. `BEGIN IMMEDIATE`, set state RUNNING.
3. Select compatible, non-expired units earliest-expiry-first. Fewer than needed means a shortfall.
4. Call the C Banker's gate. Not granted means a shortfall. A missing or failing engine is also a denial, so nothing is approved silently.
5. Mark units RESERVED, insert reservations, set COMPLETED, `COMMIT`.
6. On any failure: `ROLLBACK`, then a new short transaction sets WAITING and writes a ROLLBACK audit row with the reason.

The C result does not provide database atomicity. Atomicity comes only from SQLite transactions.

## 15. Hospital name changes
The active hospital list is exactly these eight names:
1. Shri Mahant Indiresh Hospital
2. Graphic Era Hospital
3. Max Super Speciality Hospital
4. Kanishk Surgical & Super Specialty Hospital
5. Health Line Hospital
6. Power Life Care Hospital
7. MS Galaxy Hospital
8. Velmed Hospital

**Existing data handling (`backend/migrate.py`):**
- The three original demo hospitals (City General, St. Mary Emergency, Riverside Clinic) are renamed **in place**. Their `hospital_id` values (1, 2, 3) do not change, so requests, reservations, and audit rows still point at the same hospitals.
- Each rename is written to `hospital_name_history`, which keeps the original name for historical screens.
- The other five names are inserted with new IDs (4 to 8). No rows are deleted.
- Hospitals that are not in the target list would be reported, not removed. None exist in the shipped database.
- The migration is idempotent. Running it again changes nothing.
- Fresh databases get the eight names from `seed.py`.

Verified on the shipped database: requests 8, reservations 37, audit rows 91, units 84, all unchanged, and no foreign-key violations.

## 16. ROUTINE-to-SCHEDULE dropdown change
- **Display:** the urgency dropdown shows **CRITICAL, URGENT, SCHEDULE**, populated from `/api/meta`. EMERGENCY is no longer offered for new requests.
- **Storage:** `SCHEDULE` is stored as the existing internal value `ROUTINE`. The CHECK constraint, existing rows, and the seed data stay unchanged, so no table rebuild is needed.
- **Mapping:** `engine.URGENCY_ALIASES = {"SCHEDULE": "ROUTINE"}`. The request queue displays a stored ROUTINE as SCHEDULE.
- **Priority:** CRITICAL > URGENT (shown as HIGH) > ROUTINE (shown as NORMAL). The ready queue uses the existing C Priority-NP scheduler with strict tiers and FIFO tie-breaks; the matching Python fallback preserves the same ordering if the C executable is unavailable.
- **Legacy values:** existing ROUTINE and EMERGENCY rows still work. The backend still accepts EMERGENCY, but the dropdown no longer offers it.
- **OS scheduling algorithms are untouched.** The dropdown does not select an algorithm.

## 17. Installation instructions (Windows, PowerShell)
```powershell
cd ebms
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
If activation is blocked: `Set-ExecutionPolicy -Scope Process Bypass`.

## 18. Windows build instructions
Requires a MinGW-w64 gcc on PATH (verify with `gcc --version`). The script lives in `os_algorithms\`.
```powershell
cd ebms\os_algorithms
.\build.ps1          # builds bin\ebms_os.exe
.\build.ps1 -Test    # builds the engine, then runs the C test programs
```
Compiler flags: `-std=c11 -Wall -Wextra -O2 -Iinclude`. No `-pthread` on Windows: the Win32 threading path is used.

Linux or macOS: `cd os_algorithms && make && make test` (`make test-asan` and `make test-tsan` run sanitizers).

## 19. How to run the application
```powershell
cd backend
python seed.py          # only for a brand-new database (safe to rerun)
python migrate.py       # only for an existing database (safe to rerun)
python app.py           # open http://localhost:5000
```
`app.py` also seeds and migrates on startup. The OS Simulation panels need the engine built first (step 18). The page shows "C engine: not built yet" otherwise.

## 20. How to run tests
From `backend/` (after building the engine):
```powershell
python -m unittest test_core test_os_bridge test_upgrade -v
```
From `os_algorithms/` (Windows: `.\build.ps1 -Test`):
```powershell
make test          # Linux/macOS
```

## 21. Known limitations
- **Verified platform:** the C engine was built and tested with gcc on Linux, and the sanitizer runs were done on Linux. The Windows build script and Win32 threading path were not run in this environment. Run `build.ps1 -Test` on Windows before relying on it.
- **Resource model:** one component per request; the Banker's check sees one resource type per process. Multi-component requests are not implemented.
- **Process limit:** the Banker's check accepts at most 50 active requests. Beyond that the check reports "unavailable" and reservations wait.
- **Simulation scope:** scheduling uses a unit-time tick model, not a real kernel. Producer-consumer and readers-writers are threaded simulations. Their counts and invariants are deterministic, but waits and maximum concurrency vary between runs.
- **Deadlock:** single-instance model only. Multi-instance cycles are reported but never confirmed.
- **Locations:** the three original hospitals keep their placeholder locations (North wing, Central, East). The five new hospitals have "Not specified" as their location. Replace these with real locations.
- **Process per reservation:** each reservation starts a C process (milliseconds). This is fine for a demo, not for high volume.
- **JSON:** the C parser accepts integers only, with no floats or exponents. This is intentional.
- **Database:** still SQLite; the PostgreSQL port is not done.
- **Not implemented:** donor and donation tables, issue workflow, recovery tests, two-phase commit simulation, MLFQ.

## 22. Viva questions and answers
**Q: Why is the scheduler a tick simulation?** A: It makes idle time, preemption, and quantum expiry exact and easy to verify, which a real kernel makes hard to test.

**Q: What is the difference between SRTF and SJF?** A: SRTF is preemptive and always runs the process with the least remaining time. SJF non-preemptive picks the shortest burst only when the CPU is free.

**Q: Why does Round Robin admit new arrivals before the preempted process?** A: That is the standard convention, and it is documented as the tie-breaking rule.

**Q: What does the Banker's algorithm guarantee?** A: If the system starts safe and every request is granted only when the resulting state is safe, every process can finish in some order. It does not detect deadlock.

**Q: Does an unsafe state mean deadlock?** A: No. Unsafe means completion is not guaranteed. Deadlock is an actual circular wait.

**Q: How is a deadlock found?** A: A DFS on the wait-for graph finds a back edge, which gives the cycle. Under the single-instance model that cycle is a deadlock.

**Q: Why not compile C on each request?** A: Compilation is slow and is a build concern. The binary is built once and each request only runs it.

**Q: Why use an argument list instead of a shell command?** A: It avoids shell injection because user values never become part of a command string.

**Q: What happens if the C check fails?** A: The reservation is not approved. The transaction rolls back and the reason is written to the audit trail.

**Q: Why does producer-consumer use two condition variables?** A: One wakes producers when space appears and the other wakes consumers when items appear. A single variable would wake the wrong kind of thread.

**Q: What is writer preference and what is its cost?** A: Waiting writers block new readers, so writers do not starve. The cost is that a steady stream of writers can starve readers.

**Q: Why is SCHEDULE stored as ROUTINE?** A: It avoids changing the schema and existing data, and the label is a display concern.

**Q: Why are hospital IDs kept during the rename?** A: Foreign keys stay valid, and historical rows still point at the same hospital.


## 23. Final phase additions
- **Dashboard and analytics:** `/api/dashboard` and `/api/analytics` derive counts and charts from SQLite. The dashboard polls every 30 seconds and runs the idempotent expiry sweep.
- **Compatibility simulation:** RBC uses the seeded donor table; plasma applies the reverse ABO direction (Rh ignored); platelets use a simplified ABO approximation. These are teaching rules only, not clinical guidance.
- **Scheduling:** strict Critical > High > Normal priority, FIFO arrival tie-breaks, FEFO unit selection, no expired allocations, reservation/audit transactions. The separate C CPU algorithms remain unchanged.
- **Audit and tracking:** request history captures Pending, Scheduled, Reserved, Completed, and Failed; audit supports event/actor/transaction/date filters, pagination, CSV, and PDF.
- **Transfers:** source units must be assigned to a hospital, are reserved in one transaction, and move through Pending → Approved → In Transit → Completed. Repeated active source/destination/stock/quantity requests are rejected; failures are recorded after rollback.
- **Reports:** PDF endpoints include inventory, group stock, requests, emergencies, expiry, transfers, donations, and audit trail. Filters accept `from`, `to`, and `group` where relevant.
- **RBAC:** authentication is enabled by default. First-run admin setup is available once, then ADMIN, STAFF, and HOSPITAL accounts use hashed passwords and Flask sessions. Set `EBMS_SECRET_KEY` to a long random value for stable sessions and `EBMS_COOKIE_SECURE=1` behind HTTPS. `EBMS_AUTH_REQUIRED=0` is only for local legacy API smoke testing.

## 24. Windows setup and run
From PowerShell in the extracted `ebms` directory:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:EBMS_SECRET_KEY = "replace-with-a-long-random-secret"
cd backend
# Existing database: run migration first. For a brand-new database, app.py seeds automatically.
python migrate.py
python app.py
```
Open `http://127.0.0.1:5000`. On first use, open **User Management**, create the first admin with a unique username and password (12+ characters), then sign in. Build the existing C OS engine in a separate terminal with MinGW-w64 on PATH:
```powershell
cd ..\os_algorithms
.\build.ps1 -Test
```
Run the Python tests from `backend`:
```powershell
python -m unittest test_core test_os_bridge test_upgrade test_final_phase -v
```
For an existing database, `python migrate.py` is repeatable and does not delete records. Back up `backend\ebms.db` before upgrading. The app also applies the additive schema migration automatically on the first API request.

## 25. Known remaining limitations
- A separate reference dashboard image was not included with the supplied ZIP; the redesign follows the visual requirements in the prompt.
- The included hospital units in the demo database are central/unassigned stock. Transfers require hospital-assigned units; assign hospital during donation intake.
- The frontend includes a hospital directory, not full hospital CRUD. User management creates users but does not yet edit/deactivate accounts.
- Tracking records scheduler events, but request rejection/cancellation controls are not implemented. Transfer failure/cancellation states exist in schema, while the UI supports the main approval-to-completion path.
- Analytics are database-backed and filterable, but visualizations are compact bar charts rather than a full charting library; monthly data is grouped from existing blood-unit collection dates.
- Audit PDF is a lightweight generated document with text rows, not a branded graphical template with a logo asset.
- API endpoints, role scoping, transfer lifecycle, PDF downloads, and core browser DOM bindings were smoke-tested using the Flask/Werkzeug packages bundled with the uploaded project. The published requirements install a clean Flask + ReportLab environment; ReportLab-specific rendering should be rechecked after installation on Windows.
- This project remains an academic simulation. Blood compatibility and transfusion allocation require qualified medical verification.


## Donor registry, screening, tokens, and workflow updates

The latest additive update introduces donor registration scoped to the registering hospital, generated donor registration tokens, configurable academic service pricing, and authorized screening records for HIV, HBV, HCV, syphilis, and malaria. Hospital accounts can list only donor records associated with their own hospital; screening APIs are staff/admin-only. The donor-screening and pricing UI is available in the separate **Donor Registry & Screening** and **Pricing & Tokens** navigation sections. The algorithm flowchart is in **Workflow Algorithm**, away from the main dashboard. The donor mapping is a simplified educational red-cell compatibility guide; O− is shown as the common universal red-cell donor concept.

Apply the additive migration with the existing command:

```powershell
cd backend
python migrate.py
```

### Deployment note (localhost to Vercel

EBMS currently uses Flask APIs and a SQLite file, so deploying the complete app as Vercel serverless functions is not recommended: serverless filesystem storage is not durable for a live database. For a student demo, deploy the Flask backend with persistent storage (for example, a service that supports persistent disks) and then host a static frontend separately on Vercel after configuring its API base URL and CORS. Keep the secret key and database credentials in deployment environment variables. Never expose the SQLite database or secret key as static assets.

All blood screening, donor eligibility, compatibility, pricing, and transfusion decisions in this project are academic simulations and must not be used clinically.
