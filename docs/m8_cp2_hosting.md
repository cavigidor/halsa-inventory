# M8-CP2 — Minimal hosting-ready runtime (D-076)

**Status:** code-only hosting readiness merged as PR #46, main `fbca73d312da4a3217e8751546e039db883b4783`;
merged-main CI 2093 passed / 8 skipped / 1 xfailed. **Not deployed**.
Next: [CP8A setup, owner decisions and acceptance](m8_cp8a_render_preflight.md).
No hosting account, service, disk, domain,
password or backup destination was created; no repository was connected to a deployment service. Nothing here
authorizes a deployment (`docs/m8_father_first_launch.md`, CP8).

The target experience is unchanged:
- The father opens one HTTPS link and enters the website password.
- He enters the financial password only for Cari balances or the Yönetim finances (D-074 / D-075).
- He installs nothing and does no maintenance.
- The owner uploads the monthly Excel reports through the Upload Center.

## 1. Architecture (unchanged, one of everything)

One web service runs one process: Python 3.12, FastAPI, **one Uvicorn worker**. It serves the HTML / JS pages and the
API, with SQLite, on **one persistent disk** mounted at one directory. The host's load balancer terminates TLS.

There is no PostgreSQL, no second service, no separate frontend host, no user accounts and no new login. In-process
locks (`PUBLISH_LOCK`, the refresh flock) assume a single writer, and SQLite is a local file. Two instances or two
workers would break that assumption — **never scale out**.

## 2. Host assessment — Render, paid web service, Frankfurt, persistent disk (default candidate)

Storage / pricing rechecked on 2026-11-05; initial host assessment 2026-11-03 (sources at the end):

| Topic | Render | Consequence for Halsa |
|---|---|---|
| Region | Frankfurt is one of the five regions; **a service cannot change region later** | choose Frankfurt at creation (closest to Istanbul; EU). KVKK review of storing the data there is still the owner's decision (CP8) |
| Disk | paid services only; **only files under the mount path persist**, everything else is lost at every deploy / restart; mount path absolute | all state under `HALSA_DATA_ROOT` = the mount (§3); the app refuses to start otherwise |
| Disk + scaling | **one instance only** with a disk; **no zero-downtime deploys** (old instance stopped first, a few seconds offline) | matches the single-writer design; deploy only when the father is not using it |
| Disk snapshots | automatic **daily snapshot, kept 7 days**, encrypted at rest; whole-disk restore only; Render warns against restoring a live database from a snapshot | not a backup plan by itself; our backup writes a consistent SQLite copy that a snapshot can carry (§6) |
| One-off jobs / pre-deploy / cron | **cannot access the service's disk** | backups run inside the web service (shell or an in-service schedule), not as a Render cron job |
| TLS | the load balancer terminates HTTPS (free managed certificate) and forwards HTTP; HTTP is redirected to HTTPS | trust the proxy headers (§4) |
| Port | bind `0.0.0.0` on `$PORT` (default 10000) | `deploy/start_hosted.sh` |
| Auto-deploy | per service: **On Commit** (default) / After CI Checks Pass / **Off** | set **Off** at creation; deploy a reviewed commit manually |
| `X-Forwarded-For` | Render's handling of a client-supplied value is not clearly documented (a 2021 thread conflicts) | the per-client login throttle may be keyed on a spoofable address; the **global** per-role throttle still caps guessing (§4) |
| Size / price | Starter 512 MB ($7/mo), **Standard 2 GB ($25/mo)**; disk $0.25 / GB / month; Hobby workspace $0/mo + compute (Pro optional) | see sizing below |

**Sizing — a material finding.** The workload was measured read-only on byte-identical copies of the real 5 Oct
exports (owner's Mac, 2026-11-04). Peak memory was about **1.0 GB**:
- restart re-derivation: 55 s;
- Customer 360 build: 60 s (6,754 customers / 182,121 lines);
- one controlled refresh: 55 s.

Render **Starter (512 MB) would run out of memory**; the minimum is **Standard (2 GB)**. A 5 GB disk is ample, since a
monthly set is about 42 MB, with uploads kept, plus backups.

**No blocker** otherwise: the paid Frankfurt service with a disk fits the design. Approximate base cost on the
free **Hobby workspace** is **$26.25/month**: $25 Standard (2 GB) + $1.25 for a 5 GB disk ($0.25/GB/month).
Paid compute does not require a Pro workspace; Pro is optional for its team / platform features. Additional usage,
off-site backup and taxes are excluded. Confirm current pricing before ordering; no resources were purchased.

## 3. Persistent storage — one mounted directory

`HALSA_DATA_ROOT` names the mount (absolute). `services/paths.py` fills the persistent paths under it:

| Variable | Hosted path (Render example: `HALSA_DATA_ROOT=/var/data`) | Holds |
|---|---|---|
| `DATA_FOLDER` | `/var/data/data` | the published workbooks (content-addressed `yukleme-<sha>.xlsx`), `sources.json`, `owner_sources.json` |
| `UPLOAD_DIR` | `/var/data/uploads` | Upload Center blobs, batches, previews (staging) |
| `AGENT_DB` | `/var/data/state/agent_state.db` | SQLite: sessions, action decisions, CRM / follow-ups, fair campaigns, Veri Kontrol history and notes, Upload Center history |
| `DASHBOARD_PATH` | `/var/data/published/dashboard.html` | the published page, `dashboard.generation.json` (accepted generation), `dashboard.prev.html` |

For first start, mount the disk at **`/var/data` and set `HALSA_DATA_ROOT=/var/data`**. Render creates the
mount; the app creates `data/`, `uploads/`, `state/` and `published/` beneath it. Do not point the initial root at
`/var/data/halsa`: that child is not created by the mount, and startup correctly refuses a missing root.
Leave the four derived path overrides unset in the host environment (do not copy the local `.env` defaults).
A genuinely missing root still refuses startup; the app never creates a missing mount. Render forbids `/`,
`/opt/render/project/src` and similar exact paths.

**Rules (enforced at startup; the server refuses to start otherwise — fail closed):**
- the root exists, is a directory, is absolute and is writable;
- every persistent path is absolute and inside the root (symlinks resolved);
- an optional owner / Hedef workbook path (`KREDI_PATH`, `GIDER_GELIR_PATH`, `BUTCE_PATH`, `HEDEF_PATH`) must also be
  inside the root — on the host, owner workbooks are uploaded through the Upload Center instead.

A path left on the ephemeral disk would silently lose data at the next deploy, hence the refusal.

Without `HALSA_DATA_ROOT` nothing changes (local use keeps the old defaults). The owner's Mac files were not moved
or modified.

**Atomic writes:** every publication step stages its temporary file **next to its target** (same directory, so the
same filesystem and an atomic `os.replace`):
- `.kopya` for published workbooks;
- `in-*.part` for uploads;
- `.sel-*` for selections;
- `.gen-*` / `.new` for the manifest and the page.

CP2 adds `fsync` of the staged file and of the directory around these renames, so a host restart right after a
publication cannot leave an empty or renamed-but-unwritten file. No temporary file is written to `/tmp` or the code
directory.

## 4. Startup, HTTPS and secrets

Start command (the host's "Start Command"): `sh deploy/start_hosted.sh`.

```sh
python -m uvicorn app:app --host 0.0.0.0 --port "$PORT" --workers 1 \
    --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS" --no-server-header --timeout-graceful-shutdown 20
```

The script requires `HALSA_DATA_ROOT`, ignores any `.env` file (`STOCKAGENT_NO_DOTENV=1`) and keeps AI off by default
(`ENABLE_AI=false`, `AI_PROVIDER=disabled`). The build command is `pip install -r requirements.txt` (Python 3.12).

**Private environment variables** (host dashboard → Environment; never in Git):

| Variable | Value |
|---|---|
| `HALSA_DATA_ROOT` | `/var/data` (the disk mount itself) |
| `HALSA_VIEWER_PASSWORD_HASH`, `HALSA_FINANCE_PASSWORD_HASH`, `HALSA_OWNER_PASSWORD_HASH` | scrypt hashes generated on the owner's computer (`python -m services.owner_auth hash-password [--viewer\|--finance]`); three different passwords |
| `FORWARDED_ALLOW_IPS` | `*` on Render (see below) |
| `MANUAL_INFLATION_RATE` | as today (optional) |
| `PYTHON_VERSION` | `3.12.16` (full version required by Render; matches verified CI) |

**HTTPS and the proxy headers.** Render's TLS proxy forwards plain HTTP with `X-Forwarded-Proto: https`. Uvicorn
rewrites the scheme, client address and Host only for connections from `FORWARDED_ALLOW_IPS`.
- **Set (`*` on Render):** the instance port is reachable only through Render's proxy, not from the internet. The app
  then sees `https`, so password logins work, the session cookie gets `Secure`, and HSTS is sent.
- **Left unset:** the default `127.0.0.1` trusts nothing behind the proxy. Every login answers `403 https_required`.
  This fails closed — a password is never accepted over an unverified scheme. Tested: `tests/test_m8_cp2_hosting.py`.
- **Residual risk:** with `*`, the client address comes from `X-Forwarded-For`. If Render passes a client-supplied
  value through, an attacker could rotate it to dodge the per-client throttle (5 failures / 15 min). The **global**
  per-role throttle (20 failures / 15 min, `docs/m8_cp1_access.md` §3) and scrypt still bound guessing. A future
  network gate (CP8 decision) would remove this.

`HALSA_COOKIE_SECURE` is not needed on Render, since the scheme is forwarded. The D-074 / D-075 protections are
unchanged:
- website password before anything;
- the financial password for Cari and Yönetim finances;
- the owner password for every change;
- CSRF, same-origin, throttling and revocation.

## 5. Liveness — `GET /livez` (host health check path)

- Public. Answers `200 {"status":"ok"}` (also to `HEAD`), `Cache-Control: no-store`, with **no cookie, no
  generation header, no data**.
- It is answered **without reading the session store**: no SQLite access, no idle-timer update. It also reads no
  context, source, generation, configuration or provider, and starts no import, refresh or AI call.
- Registered in `services/access.py` (`public`, `NO_SESSION`) and `services/accuracy.py` (`system`).
- It answers "the process is up". Readiness and data state stay behind a session: `/api/health` is still `viewer`
  (anonymous → 401).
- Tests: `tests/test_m8_cp2_hosting.py`. Session, SQLite, gate, context and provider are all patched to raise; the
  probe still answers.

## 6. Backup and restore

**Minimum recovery set:**

| Part | Path | How it is copied |
|---|---|---|
| State database | `state/agent_state.db` | SQLite's online backup API — a consistent snapshot even while the server writes — then `PRAGMA integrity_check` |
| Data | `data/` | file copy (published workbooks + selection files) |
| Published page | `published/` | file copy (`dashboard.html` + `dashboard.generation.json`) |
| Upload staging (optional) | `uploads/` (`--include-uploads`) | file copy; only needed to re-open older batches |

After a restore the server **re-validates** the published generation against the restored workbooks before serving
anything (accuracy rules unchanged).

```sh
# on the host (Render: the service's Shell tab), while the app runs:
python -m services.backup create --dest /var/data/halsa-backups      # → halsa-backup-<UTC>/ + BACKUP_MANIFEST.json
python -m services.backup verify /var/data/halsa-backups/halsa-backup-20261104T020000Z
# restore = into a NEW, empty directory (never over the live one), then point the service at it:
python -m services.backup restore /var/data/halsa-backups/halsa-backup-… --target /var/data/halsa-restored-20261104
#   → set HALSA_DATA_ROOT=/var/data/halsa-restored-20261104 and redeploy; keep the old directory until verified
```

**Backup:**
- It holds the publication lock (`<dashboard>.refresh.lock`, the same one every refresh / Upload Center publication
  holds). If a publication is running it stops and says so.
- It compares the selection / manifest digests before and after, and aborts if they changed.
- It never copies a temporary file of an in-flight write or a SQLite journal.
- Output is `0700` / `0600`; failed backups are deleted.

**Restore:**
- It verifies every SHA-256 of the backup manifest and refuses unsafe or duplicate paths.
- It refuses a non-empty target, so nothing is ever overwritten. The restored SQLite file is integrity-checked.
- Restored sessions are valid again. Run `python -m services.owner_auth revoke-sessions` after a restore if that is not
  wanted.

**Tested:**
- with synthetic data while the real server was running;
- restored into a new directory, then the real start command was run on it: the same core generation was served, the
  Cari rows were equal and the CRM change was present;
- an uncommitted SQLite transaction is not in the copy;
- changed files and unsafe paths are refused;
- the command waits for a running publication.

**Off-instance, encrypted (documented, not configured — required before real-data launch):**
1. **Daily in-service job** (Render cron jobs cannot reach the disk). A small scheduled step inside the web service, or
   the owner via Shell, runs `services.backup create`, then encrypts and sends the folder off the instance:
   - preferred: `restic` (encrypted, deduplicated, retention) to an EU object store (e.g. Backblaze B2 EU / S3
     Frankfurt) — repository password and bucket keys in private env vars;
   - minimal alternative: `tar czf - <backup> | age -r <owner public key>` uploaded to the store; the private key
     stays offline with the owner.
2. **Render's daily disk snapshot** (7 days) then also carries the last consistent `halsa-backups/` copy. It is useful
   for fast recovery, but it is not off-instance and not a substitute.
3. **Restore drill on the host** (restore into a new directory, switch `HALSA_DATA_ROOT`, check login / page / Cari /
   CRM) before the real data goes live.

## 7. Persistence acceptance (synthetic, CI)

`tests/test_m8_cp2_hosting.py::test_hosted_runtime_persists_across_restart_and_fails_closed` runs the **real start
command** (`sh deploy/start_hosted.sh`, Uvicorn, one worker, `app:app`, production accuracy gate, real logins). Its
environment uses the documented `HALSA_DATA_ROOT=/var/data` layout under a temporary test prefix (no system
`/var/data` is touched), `PORT` and synthetic password hashes, with no derived path overrides. A configuration test
reads `.env.example` and checks the four exact `/var/data/...` paths. No pre-created `halsa` child is needed.

1. Starts on the empty mount itself; creates all four persistent subdirectories and an initialized, integrity-checked
   SQLite database. `/livez` is the constant data-free 200 response; anonymous `/api/health` stays 401. Anonymous
   `GET /` gives the site-password page; owner `GET /` shows nothing published.
2. With the documented `FORWARDED_ALLOW_IPS=*` and `X-Forwarded-Proto: https` from the proxy: website, financial
   and owner logins work, their cookies are `Secure`, and HSTS is set. The same request without it: `403 https_required`. Owner login.
3. The owner uploads, validates and publishes stock + sales + a dated Cari report through the Upload Center API.
4. `GET /` 200, page generation = status generation, displayed = `receivables.collections`, exactly 1 VERIFIED.
   `/api/finance/cari` gives the exact synthetic balances; the website viewer gets 401 on the list and 403 on upload.
5. A CRM interaction is saved. A backup is taken **while the server runs**.
6. Every file is under the disk (data / published / state / uploads); no published page was written into the code directory. SIGTERM;
   start again on the same disk.
7. The owner's **session** survived (SQLite). Same core generation served and gated. Selection and uploaded workbooks
   are byte-identical. Cari rows, the CRM note and the Upload Center history are all present.
8. & 9. Stopped:
   - **a changed Cari workbook** → after the restart nothing is served (503 neutral page, no generation, list 409);
   - restored bytes but **a broken manifest** → the same (no obsolete or unverified page, ever).
10. The backup restored into a new directory and the real start command run on it: the same core generation is
    served, with the same Cari rows and CRM note.

**2026-11-05 correction checkpoint:** 17 CP2 + 20 unchanged Classic tests passed together (37). Full local suite
with AI disabled and browser / JavaScript tests required: **2094 passed, 7 skipped, 1 xfailed** (453.74 s).
Exact pushed-head GitHub CI is recorded on PR #46; its shallow checkout skips the independent Git-history hash test
(the complete clone runs it). Classic code / fixtures / tests equal merged main; owner acceptance is postponed.

## 8. Remaining steps to a real HTTPS link (owner + separate authorization — not done here)

1. **Owner already chose Render.** Before real-data hosting, confirm region (Frankfurt candidate) under KVKK, the disclosure
   inventory (`docs/m8_cp1_access.md` §6); whether to add a network gate.
2. Create the Render workspace / Standard web service / 5 GB disk at `/var/data` with the owner's payment account.
   Region Frankfurt, **Auto-Deploy Off**, health check path `/livez`, start command `sh deploy/start_hosted.sh`.
3. Generate the three password hashes on the owner's computer and paste them into the host's private environment.
   Set `HALSA_DATA_ROOT=/var/data` and `FORWARDED_ALLOW_IPS=*`.
4. Manual deploy of the reviewed main commit. Check `/livez`, the site-password page, the three logins over HTTPS, and
   that `/api/health` is 401 without a session.
5. Configure and test the off-instance encrypted backup and a restore drill on the host (§6).
6. Owner uploads the real monthly reports through `/yukleme`; check the page and the Cari list. Then the father's
   bookmark from Istanbul; optional custom domain.
7. Only then: the father-facing link.

Sources:
- [Render — Persistent Disks](https://render.com/docs/disks)
- [Render — Web services](https://render.com/docs/web-services)
- [Render — Deploys](https://render.com/docs/deploys)
- [Render — Regions](https://render.com/docs/regions)
- [Render feedback — X-Forwarded-For](https://feedback.render.com/features/p/send-the-correct-xforwardedfor)
- [Render — Pricing](https://render.com/pricing)
- [Render — Workspace plans](https://render.com/docs/new-workspace-plans)
