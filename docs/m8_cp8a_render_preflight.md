# M8-CP8A — Render launch preflight and owner decision sheet

**2026-11-05: preparation only; no provisioning, billing, Git connection, deployment, production secrets or private
Excel upload authorized or performed.** CP2 is complete: PR #46 merged as
`fbca73d312da4a3217e8751546e039db883b4783`; approved tree `0e34effc1d5c301a2eabef1c13fe28a49b601329`;
[merged-main CI](https://github.com/cavigidor/halsa-inventory/actions/runs/42099574909) passed 2093 / 8 skipped /
1 xfailed, Python 3.12.16. This is engineering evidence, **not hosted acceptance**.

Use the existing FastAPI / SQLite app, three passwords and one service. Classic is unchanged, local-only, and its
Mac acceptance is deferred; it neither blocks this launch nor becomes a hosted route. D-070 / D-071 / D-073 /
D-074 / D-075 remain in force: only `receivables.collections` certified, 60-day Cari freshness on bound evidence,
32 other metrics withheld, no obsolete stock recommendations or invoice-age-as-overdue claims.

## 1. Owner decision sheet — record approve / defer, date and reviewer privately

| Decision | Proposed choice | Approval needed before |
|---|---|---|
| Paid synthetic rehearsal | One Render Hobby workspace, Standard 2 GB service + 5 GB disk; budget below | Creating/linking a service or any billable resource, including a synthetic-only deployment |
| Data location and KVKK | Frankfurt app/disk; proposed backup in Backblaze B2 EU Central (Amsterdam), subject to review | Any real ERP file, personal data or production backup leaves the Mac |
| Access disclosure | Keep the existing role matrix below and confirm the existing disclosure inventory in `m8_cp1_access.md` §6 for external hosting | Real-data release / sharing the bookmark |
| Extra network restriction | Recommend no extra gate for the initial simple father workflow, if the owner's security review accepts the existing controls; otherwise defer launch and specify the required restriction | Publicly reachable service creation; no gate is silently added or assumed approved |
| Backup operations | Client-encrypted off-instance backups, daily and after publication; proposed retention 14 daily / 4 weekly / 3 monthly; owner receives failure/staleness alerts | Real-data upload; approve provider, region, retention, key custody and cost first |
| Release | Synthetic HTTPS / persistence / off-instance restore evidence first; separate explicit permission for real uploads and father access | Each corresponding phase; approving this document is not permission to execute it |

**KVKK review action, not a legal sign-off:** the owner / qualified adviser must identify the controller/processor
roles, processing basis, data categories/recipients, Render and backup contracts/subprocessors, retention and actual
access locations (including the US owner/admin). Record the applicable Article 9 transfer mechanism and check current
adequacy decisions or appropriate safeguards. If using the Board's standard contract, Article 9(5) requires notice to
the Authority within five business days of signature. Frankfurt/EU storage, encryption or a GDPR DPA alone must not
be recorded as completed KVKK acceptance. Confirm that providers support the chosen mechanism before real transfers.
[KVKK Article 9](https://www.kvkk.gov.tr/Icerik/6649/Personal-Data-Protection-Law).

| Session | May see | May change |
|---|---|---|
| Anonymous | Login pages and constant `/livez` only; no dashboard data | Nothing |
| Website viewer | Normal gated dashboard; disclosed customer names and CRM / follow-up / fair notes; no account-level Cari balances | Nothing |
| Financial viewer | Website-viewer content + fresh VERIFIED Cari list / Customer 360 balance fields + permitted Yönetim finances | Nothing |
| Owner | Authorized content, diagnostics, Upload Center and administration | Upload / validate / publish and existing owner-only operations, with CSRF |

The father gets the website password and, if authorized, the financial password. He never needs the owner password,
Render, a terminal or an installed client. Shared passwords can be forwarded and do not provide a per-person audit
trail. Keep the existing login throttling, HTTPS, Secure cookies, HSTS, logout/revocation and fail-closed behavior.
An optional network gate must accommodate both US and Istanbul access; do not assume a fixed home IP.

## 2. Exact Render setup — future authorized action only

| Setting | Value |
|---|---|
| Workspace | Hobby ($0 workspace fee; paid compute allowed) |
| Service | One **Web Service**, native Python runtime; one instance, no autoscaling |
| Repository / branch | `cavigidor/halsa-inventory`, `main`; reviewed runtime baseline `fbca73d312da4a3217e8751546e039db883b4783` |
| Root directory | Repository root (leave Root Directory blank) |
| Region | Frankfurt, subject to the decision above; select at creation |
| Compute | Standard, 2 GB RAM |
| Build | `pip install -r requirements.txt` |
| Start | `sh deploy/start_hosted.sh` (already supplies `--workers 1`) |
| Health check | `/livez` |
| Persistent disk | 5 GB, mount **`/var/data`** |
| Auto-Deploy | **Off**; also disable PR preview environments / automatic preview creation |
| URL | Render-provided HTTPS `onrender.com` URL initially; no domain purchase needed |
| Pre-deploy command | Blank; the mounted disk is not available to pre-deploy jobs |

Set these in Render's **private service environment**, not in Git or a committed `.env`:

| Variable | Value |
|---|---|
| `PYTHON_VERSION` | **`3.12.16`** (full patch version, matches verified CI; not `3.12.x`) |
| `HALSA_DATA_ROOT` | **`/var/data`** |
| `FORWARDED_ALLOW_IPS` | `*` for Render's proxy; verify HTTPS behavior below |
| `ENABLE_AI` | `false` |
| `AI_PROVIDER` | `disabled` |
| `HALSA_VIEWER_PASSWORD_HASH` | Locally generated website-viewer hash |
| `HALSA_FINANCE_PASSWORD_HASH` | Locally generated financial-viewer hash |
| `HALSA_OWNER_PASSWORD_HASH` | Locally generated owner hash |

Leave `DATA_FOLDER`, `UPLOAD_DIR`, `AGENT_DB`, `DASHBOARD_PATH` and optional owner workbook path overrides unset.
Do not copy the Mac `.env` or private files to Render. Leave API keys unset. Keep Render's supplied `PORT` (normally
10000); the start script binds `0.0.0.0:$PORT`. It ignores `.env` and keeps one Uvicorn worker.

Derived paths are exactly `/var/data/data`, `/var/data/uploads`, `/var/data/state/agent_state.db`, and
`/var/data/published/dashboard.html` (+ manifest). The root must already be the mounted disk; the app creates its
subdirectories and SQLite schema. A missing mount still refuses startup; do not bypass it or create a fallback root.
Backups/restores may use explicitly created children under the mount; that is distinct from the initial root.

**First-create boundary:** Render's **Create Web Service starts the first build/deploy**; Auto-Deploy Off controls
subsequent Git-triggered deploys, not that initial action. Review all settings, disk and synthetic-only secrets before
clicking Create. Recheck `main` / reviewed SHA immediately beforehand; if it moved to an unreviewed runtime, stop.
For later manual releases use **Deploy a specific commit**, record the actual SHA and recheck Auto-Deploy Off.
No deploy hooks or CI deployment integration are needed.

## 3. Monthly cost estimate — USD, checked 2026-11-05, not a purchase

| Item | Estimate |
|---|---|
| Hobby workspace | $0/month |
| Standard 2 GB service | $25/month |
| 5 GB persistent disk | 5 × $0.25 = $1.25/month |
| Render base | **$26.25/month** |
| Proposed B2 backup storage | $6.95/TB/month; e.g. **20 GB retained ≈ $0.14/month**, before any free allowance |
| Example total with 20 GB backup | **About $26.39/month**, before extra usage and taxes |

The backup size is a planning assumption, not measured retained usage or a guaranteed cap. Allow approximately $1/month
for small initial backup storage (a $27.25 base-plus-allowance budget), and review actual growth/retention. Restic has
no service subscription; installing and operating it still needs approval and setup. Render outbound bandwidth/build
usage and backup restore downloads above provider allowances can add charges. Monitor usage and alerts; do not claim
a hard spending cap. No Pro workspace, second app service, managed database or custom domain is required.
[Render pricing](https://render.com/pricing), [B2 pricing](https://www.backblaze.com/cloud-storage/pricing).

## 4. Reproducible phased acceptance — all boxes currently NOT RUN on Render

Record a private evidence sheet: time, reviewed/deployed SHA, service region/settings, checks, pass/fail and counts-only
results. Keep passwords, hashes, cookies, tokens, Excel rows, backup keys and screenshots with business data out of
GitHub/chat. A green `/livez` alone proves neither usable data nor recovery.

### A. Provision only after explicit authorization
1. Record approval for spending, Frankfurt synthetic rehearsal, access/network choice and Git connection. Confirm the
   repository still has no unexpected deployment integration. Use the exact setup above, configure the disk/secrets
   before Create, and capture the deployed commit SHA. Creation is a deployment authorization boundary.
2. Confirm one instance, one worker, mount 5 GB `/var/data`, Auto-Deploy Off and previews disabled. Do not create a
   separate cron/worker service: it cannot access this disk. Use only synthetic workbooks at this phase.

### B. Generate the three hashes locally — owner types passwords privately
In a clean local checkout of the reviewed code, with its Python environment activated:

```sh
python -m services.owner_auth hash-password --viewer
python -m services.owner_auth hash-password --finance
python -m services.owner_auth hash-password
```

Each command prompts without echo and prints its environment assignment. Choose three distinct strong passwords in
the owner's password manager; the hash command checks against other roles already configured locally. Do not pass
passwords as command arguments or paste passwords/hashes into this PR/chat. Copy only the hash value into the matching
private Render variable; preserve `$` characters exactly. Use unique **synthetic-rehearsal passwords** first, and
replace them with fresh production hashes and revoke rehearsal sessions before real-data acceptance.

### C. First HTTPS smoke and role boundaries
1. Wait for a successful build/start. In private Shell checks, confirm `data/`, `uploads/`, `state/`, `published/` and
   initialized `state/agent_state.db` under `/var/data`. No source selection/page is expected before publication.
2. Anonymous HTTPS `GET /livez` → 200, exactly `{"status":"ok"}`, no cookie/generation/data; `HEAD` → empty body.
   Anonymous `/` shows only the Turkish password page; `/api/health` and data APIs refuse unauthenticated access.
3. In separate private browser sessions, test website, financial and owner passwords. Verify Secure/HttpOnly cookies,
   HSTS and logout. No password submission over HTTP is accepted; Render must redirect HTTP to HTTPS.
4. Website viewer cannot read `/api/finance/cari` (401) or owner diagnostics / mutations (403); financial viewer also
   cannot upload, publish or edit. Owner alone can perform writes, with CSRF; a write lacking CSRF is refused. Direct
   API access must enforce the same permissions. Before source publication, withheld/no-data responses are expected.

### D. Upload/validate/publish a known synthetic batch
Generate only invented fixtures on the owner's local computer (repository root, test dependencies available):

```sh
STOCKAGENT_NO_DOTENV=1 AI_PROVIDER=disabled python - <<'PY'
import datetime as dt
import tempfile
from pathlib import Path
from tests.fixtures import fair_synth as F
from tests.test_m8_d073_cari_promotion import _cari
folder = Path(tempfile.mkdtemp(prefix="halsa-hosted-synthetic-"))
F.write_all(str(folder))
cari = folder / f"Cari {(dt.date.today() - dt.timedelta(days=5)).isoformat()}.xlsx"
_cari().to_excel(cari, index=False)
for p in (folder / F.STOCK_FILE, folder / F.SALES_FILE, cari):
    print(p)
PY
```

Upload **only the three printed paths** through owner `/yukleme`; assign Stock, Sales and Cari respectively. Do not
select the fixture's other aging file or any private export. Validate, inspect the preview, then publish explicitly.
Expected: publication succeeds; owner accuracy status has 33 metrics with only the factual Cari list displayable on
fresh bound evidence. Other 32 stay withheld; source findings remain visible. No metric certification is changed.
The known synthetic Cari list has 4 accounts with signed balances **-1500.25, 2010.50, -700.00, 0.00** (from the existing
CP2 fixture). Owner/financial sessions may read them; website viewer sees no Cari records in HTML/API/Customer 360.
Record matching page/API generation IDs, source hashes and upload history privately; verify original synthetic input
hashes unchanged. Try an invalid synthetic batch: it must not replace the accepted publication. Never force approval.

### E. Hosted persistence check
Save one clearly synthetic CRM note. Record generation, source selection/hashes, upload batch history, the note and
owner session. Use Render **Restart service** on the same disk (not a new service). Recheck login/session, the same
accepted core generation, exact signed Cari rows, CRM note, workbooks and history after warm-up. Confirm role separation
again. Record startup/rebuild duration; liveness may be green before data warm-up finishes. Any failure blocks real data.

### F. Encrypted off-instance backup and restore — actual open operations prerequisite
CP2 has a consistent backup tool. **Restic installation, its credentials, a recurring in-service schedule, retention
and failure/staleness notification are not supplied/configured by CP2 or this docs PR.** Complete that small operations
setup separately before real data; do not call a manual copy an accepted daily backup. Render cron/one-off jobs cannot
mount the app disk. Keep the app at one worker; schedule one non-overlapping operator job in the mounted service.

Proposed destination: a private B2 EU Central (Amsterdam) bucket, approved under the same data-transfer review.
Use restic's recommended S3-compatible backend and a bucket-scoped key, with repository password / access keys only
in private operator configuration. Owner keeps an independent recovery copy of the repository password. Initialize
the encrypted repository once after approval; never print its password. Agree on retention before any pruning.
[Restic repository setup](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html),
[B2 regions](https://www.backblaze.com/docs/en/cloud-storage-data-regions).

With restic available and `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` supplied
privately for the approved S3 endpoint, the operator procedure is:

1. Run `python -m services.backup create --dest /var/data/halsa-backups --include-uploads` in the live service. Record
   the **actual output directory**; do not guess its timestamp. Run `python -m services.backup verify <that-directory>`.
2. Run `restic backup <that-directory> --tag halsa` and record its exact snapshot ID. Set restic's cache under
   `/var/data` (e.g. private `RESTIC_CACHE_DIR=/var/data/restic-cache`), not the ephemeral code directory. A successful
   restic snapshot must exist off the instance; a Render disk snapshot alone is insufficient.
3. Before deleting any staging copy, verify the remote snapshot with `restic check --read-data` and download that
   **explicit snapshot ID** with `restic restore <snapshot-id> --target <new-empty-download-directory>` on an isolated
   recovery machine/directory. Do not use the live unencrypted staging copy as proof of off-instance recovery.
4. Locate its restored `BACKUP_MANIFEST.json`. Privately transfer the verified downloaded backup to a fresh
   directory on the synthetic service disk using Render SSH/SCP; verify it there again. Use the containing directory
   with `services.backup verify`, then
   `python -m services.backup restore <restored-backup-directory> --target /var/data/halsa-restore-drill-<unique-id>`
   on the synthetic host. The target must be new/empty; never overwrite live state. Ensure free disk space first.
5. For the synthetic drill only, record the original root, temporarily set the root to that restored directory,
   restart and verify the same generation, balances, CRM and upload history through real HTTPS logins. Revoke restored
   sessions if required. Switch back to `/var/data` and restart, retaining both copies until reviewed. Do not overwrite
   or delete the live root, use raw SQLite copies, or restore Render disk snapshots over the live database.
6. Demonstrate an automatic scheduled run, retention, failure alert and stale-backup alert to the owner; record snapshot
   age, duration, storage and a successful off-instance recovery. Confirm recovery works without credentials stored
   only on the failed service. Only then accept backup operations. No backup command is executed in CP8A.

### G. Real monthly uploads — separate release permission
Require completed data-location/KVKK/disclosure decisions, synthetic HTTPS/access/persistence acceptance and the
scheduled encrypted-backup + off-instance restore evidence. Obtain explicit permission for real data. Rotate all
rehearsal passwords to owner-generated production hashes; revoke rehearsal sessions; repeat login tests. Upload
current actual Excel only through owner Upload Center, verify source/date selections and preview, and publish
explicitly. Do not repair originals, promote metrics, bypass withheld sections or infer overdue debt. A stale/invalid
Cari report stays withheld despite certification; obtain valid current source evidence instead. Take a verified
backup around publication. No real rows, workbooks, DBs, generated HTML or secrets enter GitHub.

### H. Owner US and father Istanbul acceptance
Owner verifies the real authorized views and upload workflow from the US. Father opens one HTTPS bookmark in Istanbul,
uses the website password and, only for finance, the financial password; checks Turkish labels, readability, tabs,
refresh/logout and required finance access. He never uploads or administers. Check his actual browser/phone network,
not merely a simulated region. Record separate owner/father pass or fail privately. No launch-complete claim until
these checks and recovery evidence pass. For failures, keep the gate intact and preserve the previous accepted
publication; stop further uploads, record the problem and seek a scoped fix. Do not reset state or improvise rules.

## 5. Current blockers and first executable step

- Owner approval for any spend/Git connection/creation is absent in CP8A. **Do not click Create.**
- Frankfurt and backup location / provider transfer safeguards and access disclosure are not signed off.
- Production secrets and hosted HTTPS/access/persistence checks have not been performed.
- Scheduled encrypted off-instance backups, alerts and actual hosted restore are not configured or accepted.
- Current real-file publication and actual US/Istanbul browser acceptance have not occurred. CI cannot replace them.
- No additional database, frontend, authentication service, metric promotion or CP2 rebuild is needed for this scope.

**Recommended first deployment step, only after review:** owner records approval for the exact-cost, synthetic-only
Render rehearsal and access/network choice. Then configure the one service/disk/environment above and create it for
synthetic HTTPS acceptance. Real ERP data remains on the Mac until the separate legal/backup/release gates pass.

## Sources (checked 2026-11-05)
- [Render pricing](https://render.com/pricing) and [workspace plans](https://render.com/docs/new-workspace-plans)
- [Web service creation](https://render.com/docs/web-services), [deploy/restart controls](https://render.com/docs/deploys)
- [Disk limitations](https://render.com/docs/disks), [regions](https://render.com/docs/regions), [Python version](https://render.com/docs/python-version)
- [KVKK Article 9](https://www.kvkk.gov.tr/Icerik/6649/Personal-Data-Protection-Law)
- [B2 pricing](https://www.backblaze.com/cloud-storage/pricing), [B2 data regions](https://www.backblaze.com/docs/en/cloud-storage-data-regions)
- [Restic setup](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html), [retention](https://restic.readthedocs.io/en/stable/060_forget.html)
