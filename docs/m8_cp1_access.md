# M8-CP1 / CP1B — Web access: website viewer, financial viewer, owner (D-060, D-061, superseded for launch by D-074)

**Status:** CP1 (D-060 / D-061) implemented and merged (PR #30); **CP1B (D-074) adds the website password — draft PR,
not merged, not deployed.** One metric, the factual Cari list (`receivables.collections`), is VERIFIED-certified
(D-073) and shown only on fresh, generation-bound evidence.

> **D-074 (owner requirement, 2026-11-01): nothing is viewable without a password.** The D-060 / D-061 "public viewer"
> (open the link, no password) is retired. An anonymous visitor gets only a Turkish site-password page (and the owner
> login page `/giris`); every page and every data API needs a session. This is the access model for the eventual
> launch; it is **not** by itself approval to host, and it does not decide whether account-level Cari balances may be
> shown to every holder of the website password on a hosted site (see §6 and §5).

Halsa Analysis is ONE **browser-based web application** (existing FastAPI + HTML/JS + SQLite). Nobody installs
anything. No usernames, accounts, e-mail login, OAuth or MFA (owner decision).

## 1. Access model (D-074, revising D-061)

| Level | How | Can |
|---|---|---|
| **Anonymous** | opens the link without a session | only the Turkish **site-password page** (any bookmarked page shows it at the same address; after the password the page itself opens) and the owner-login page `/giris` — no data, no API |
| **1. Website viewer** | one shared **website password** (`HALSA_VIEWER_PASSWORD_HASH`), entered once per session on the site-password page | the normal read-only dashboard: every tab, the action queue, Günlük Satış, Customer 360 search, CRM / follow-ups, fair campaigns — customer names, CRM / follow-up / fair notes (approved by D-061 for viewers) — and the **VERIFIED Cari list (account-level signed balances, D-073)**; nothing changeable |
| **2. Financial viewer** (the owner's father and other trusted financial viewers) | separate **financial viewing password**, in the Yönetim tab (the site password first, or directly — the financial session also opens the dashboard) | everything a website viewer sees + the **financial sections of Yönetim** (Kredi, Gider/Gelir, Bütçe) — **read-only**, nothing else |
| **3. Owner administrator** | separate **owner password** at `/giris` (no site password needed) | everything, incl. full Yönetim (operations, system state, known issues, reload), Excel uploads, validation, publication, CRM / fair / action edits, data review and every administrative operation |

The three passwords must differ (the provisioning command refuses a password already used by another role). A login
never downgrades a session (an owner who opens the site page stays owner). The financial and website passwords can
never create an owner session. **Logging out ends viewing too** (back to the site-password page).

**No login reveals an unverified number.** The accuracy gate applies identically at every level; every analytical
metric except the VERIFIED Cari list (D-073) and every financial section is withheld for all three.

## 2. Route permissions (every route; `services/access.py`)

Levels: `public` (no session: `GET /giris` only), `viewer` (any session — website, financial or owner; anonymous →
the site-password page for `/` and `/yukleme`, JSON 401 `site_auth_required` for APIs), `finance_read` (financial
viewer or owner, GET only), `owner_read`, `owner_write` (owner + CSRF token), `auth` (the login / session endpoints).
Unlisted routes → 403 for everyone. A state-changing method can never be a read level.

| Route | Anonymous | Website viewer | Financial viewer | Owner |
|---|:-:|:-:|:-:|:-:|
| `GET /`, `GET /yukleme` (fixed page) | site-password page (401) | ✔ | ✔ | ✔ |
| `GET /giris` (owner-login page, no data) | ✔ | ✔ | ✔ | ✔ |
| `GET /api/health` (exception text only for the owner) | ✗ 401 | ✔ | ✔ | ✔ |
| `GET /api/agent/categories`, `GET /api/macro` | ✗ 401 | ✔ | ✔ | ✔ |
| `GET /api/agent/actions`, `/api/agent/customer/{code}`, `/api/agent/product/{code}` | ✗ 401 | ✔ | ✔ | ✔ |
| `GET /api/customer360/status` · `?retry=true` | ✗ 401 · ✗ 401 | ✔ · ✗ 403 | ✔ · ✗ 403 | ✔ · ✔ |
| `GET /api/customer360/search`, `/api/customer360/{code}`, `/api/sales-queue` | ✗ 401 | ✔ | ✔ | ✔ |
| `GET /api/opportunities/summary`, `/territories` | ✗ 401 | ✔ | ✔ | ✔ |
| `GET /api/fairs/history`, `/history/{id}`, `/candidates`, `/campaigns`, `/campaigns/{id}`, `/campaigns/{id}/customers` | ✗ 401 | ✔ | ✔ | ✔ |
| `GET /api/crm/summary`, `/followups`, `/recent`, `/customers/{code}` | ✗ 401 | ✔ | ✔ | ✔ |
| `GET /api/vkm/summary` (Veri Kontrol Merkezi counts and sync state only, allow-list `VkmPublicSummary`) | ✗ 401 | ✔ | ✔ | ✔ |
| **`GET /api/finance/overview`** (Kredi / Gider-Gelir / Bütçe projection) | ✗ 401 | ✗ 401 | ✔ | ✔ |
| `GET /api/vkm/issues`, `GET /api/vkm/issues/{issue_id}` (issue list, locators, history, notes) | ✗ 401 | ✗ 403 | ✗ 403 | ✔ |
| `GET /api/owner/overview`, `GET /api/owner/status` (full Yönetim, system) | ✗ 401 | ✗ 403 | ✗ 403 | ✔ |
| `GET /api/accuracy/status`, `GET /api/context`, `GET /api/ai/calls`, `GET /api/uploads…` | ✗ 401 | ✗ 403 | ✗ 403 | ✔ |
| every POST / PUT below (incl. every upload, validation and publication) | ✗ 401 | ✗ 403 | ✗ 403 | ✔ (+ CSRF) |
| `GET /api/auth/session`, `POST /api/auth/login`, `/finance-login`, `/viewer-login`, `/logout` | ✔ | ✔ | ✔ | ✔ |
| `/docs`, `/redoc`, `/openapi.json` | 404 | 404 | 404 | 404 |

Owner-write routes: `POST /api/context/refresh` (report refresh; future upload), `POST /api/owner/reload`,
`POST /api/agent/actions/enrich`, `POST /api/agent/customer/{code}/analyze`, `POST /api/agent/product/{code}/analyze`,
`POST /api/agent/draft-message` (paid AI), `POST /api/scenario`, `POST /api/actions/{id}/complete|defer|dismiss|reopen`,
`POST /api/fairs/campaigns`, `PUT /api/fairs/campaigns/{id}`, `POST /api/fairs/campaigns/{id}/archive`,
`POST|PUT /api/fairs/campaigns/{id}/customers`, `POST /api/crm/customers/{code}/interactions`,
`PUT /api/crm/customers/{code}/followup`, `POST /api/auth/logout-all`, `POST /api/auth/finance-sessions/revoke`,
`POST /api/auth/viewer-sessions/revoke` (D-074),
`POST /api/vkm/issues/{issue_id}/review`, `POST /api/vkm/issues/{issue_id}/notes`, `POST /api/vkm/sync` (Veri Kontrol Merkezi decisions and re-observation,
M8-VKM-1, D-062). Future upload (CP3) and publication (CP4) must be registered as owner-write.

### 2.1 What a financial viewer receives (`GET /api/finance/overview`)
Built from the same gate-redacted sections as the owner view, then projected (`OV.finance_section`) and validated
against the allow-list schema `FinanceOverview`:
- top level: `today`, `audience: "finance"`, `audience_note`, `read_only: true`, `kredi`, `gider_gelir`, `butce`;
- per section: `key`, `label`, `configured`, `state`, `state_label`, fixed failure text `message`, the section's
  fixed notes, `data` (financial content, withheld today) and the `withheld` notice.
- **Removed:** source file names, error codes, failed step, issue codes, load time, reload flag; and the whole
  operations summary, system status, known issues and reload state.

### 2.2 GET side effects (audited)
- `?retry=true` on Customer 360 status restarts a build → owner-only.
- `/api/owner/*` and `/api/finance/overview` may lazily load the configured owner workbooks on first open (read-only,
  the same load; never a reload — reload is owner-write).
- `/api/agent/actions` and `/api/sales-queue` insert the published deterministic queue rows (insert-only; never a
  status, note or decision); a test proves a website-viewer GET sweep changes no human decision.

## 3. Security protections

- **Passwords:** never stored or committed; only scrypt hashes (N=2¹⁵, r=8, p=1, 16-byte salt) in the private `.env`:
  `HALSA_OWNER_PASSWORD_HASH`, `HALSA_FINANCE_PASSWORD_HASH`, `HALSA_VIEWER_PASSWORD_HASH` (D-074). ≥12 characters.
  Malformed / weak → that login disabled; with no website hash, anonymous visitors are still refused (never public).
  The provisioning command refuses a password already used by another role.
- **Sessions:** random 256-bit token in the HttpOnly, SameSite=Strict cookie `halsa_owner` (Secure over HTTPS); SQLite
  keeps only its SHA-256 **and its role**. 12 h absolute / 120 min idle (bounded env). Per-role caps (owner 5, finance
  10, website viewer 20). Rotation on login (never a downgrade). Revocation: logout; owner "log out everywhere" (all
  roles); owner "end financial sessions" / "end website sessions" (`POST /api/auth/viewer-sessions/revoke`);
  `python -m services.owner_auth revoke-sessions [--owner|--finance|--viewer]`; changing a role's password revokes
  **only that role's** sessions. An open page whose session ended reloads to the site-password page on its next call.
- **Role separation:** the role comes from the login endpoint, never from the request; the middleware checks the
  session's role for every owner route (403 `owner_required` for a financial session) and for the financial route.
- **CSRF:** owner writes need the session's HMAC-derived CSRF token + same-origin `Origin`; `SameSite=Strict`.
  `X-StockAgent` is a legacy guard, not authentication.
- **Throttling:** per client (5) and global (20) failures per 15 minutes, **per role**; HTTP 429 + `Retry-After`;
  pseudonymous client keys; login attempts serialized.
- **Transport:** passwords only over HTTPS, or plain HTTP on the loopback interface (local use) — otherwise 403.
- **Headers:** frame-deny + CSP `frame-ancestors 'none'`, nosniff, same-origin referrer/COOP/CORP, HSTS over HTTPS,
  `Cache-Control: no-store` on every response to a session and on the site-password page (a shared computer's cache
  never keeps a page for the next visitor); the site-password page is `noindex`.
- **No leakage:** login bodies never echoed; logs never contain passwords, tokens, hashes, client addresses or counts.

## 4. Configure locally

```bash
source .venv/bin/activate
python -m services.owner_auth hash-password             # owner password  → HALSA_OWNER_PASSWORD_HASH=…
python -m services.owner_auth hash-password --finance   # financial password (must differ) → HALSA_FINANCE_PASSWORD_HASH=…
python -m services.owner_auth hash-password --viewer    # D-074 website password (must differ) → HALSA_VIEWER_PASSWORD_HASH=…
# paste the lines into your PRIVATE .env (never .env.example, chat or GitHub); restart the server
python -m services.owner_auth status                    # all three roles configured? open sessions per role
```

- **Father / website viewer:** open `http://127.0.0.1:8000/` → "Site şifresi" → **Giriş yap**; the dashboard opens
  at the same address (a bookmark such as `/#yonetim` is kept).
- **Father / financial viewer:** in the dashboard click **Yönetim** (or open `/#yonetim`) → "Finans
  görüntüleme şifresi" → **Aç**. The header then shows "🔓 Finans görüntüleme açık (salt okunur)" and **Çıkış yap**.
  The password lasts for the session (12 h, or 120 min without activity).
- **Owner:** `http://127.0.0.1:8000/giris`.
- **Lost phone / leaked financial password:** run `hash-password --finance` again (all financial sessions end), or
  `revoke-sessions --finance`, or (logged in as owner) `POST /api/auth/finance-sessions/revoke`. The website password
  likewise: `hash-password --viewer`, `revoke-sessions --viewer`, `POST /api/auth/viewer-sessions/revoke`.
- Over the local network (`http://192.168.x.x`) **every password login is refused** (unencrypted), so nothing is
  viewable there — HTTPS is required for any use beyond the owner's own computer.

## 5. Remaining risks

1. **Everyone with the website password reads the normal dashboard**, including customer codes / names, CRM outcomes /
   notes / owner labels, follow-ups, fair notes and the **account-level Cari balances (D-073)**. A shared password is
   shared: it can be forwarded; there is no per-person audit trail (no accounts, by decision). Rotate it when someone
   should no longer see the data.
2. **Every metric later promoted to VERIFIED becomes visible to every website-password holder.** Financial sections
   promoted later become visible to every financial-password holder. Each promotion PR must say so.
3. **Open exposure question (owner):** whether account-level Cari balances should stay at the website-password level
   or move behind the financial password before hosting (§6). D-074 places them at the website level, as instructed;
   it is not a final financial-disclosure approval for external hosting.
4. **Lockout:** someone who can reach the site can throttle a role's login for up to 15 minutes (per role; global 20
   failures) — for the website password this blocks new viewer logins meanwhile (open sessions keep working). Single
   factor, no MFA; the site-password page is an online guessing target (scrypt + throttling).
5. **Reverse proxy** must forward the real scheme/client (`--proxy-headers --forwarded-allow-ips=<proxy>`), or logins
   are refused (fail closed) and throttling becomes global-only.
6. No rate limits / bot protection beyond login throttling; CSP limited to framing (inline-script dashboard).
7. A hosting health check cannot use `/api/health` any more (it needs a session); a hosting checkpoint must add or
   choose a data-free liveness probe.
8. Not built yet: backups (CP5), hosting / TLS (CP8).

## 6. Disclosure inventory — who can read what (D-074)

Anonymous visitors: the fixed site-password page and the owner-login page only (no data, no API). Every row below
needs at least the website password.

| Information | Website viewer | Financial viewer | Owner |
|---|:-:|:-:|:-:|
| Dashboard shell, tab names, data date, source **file names** in the header, withheld notices | ✔ | ✔ | ✔ |
| D-070 Stock retirement notice and signed-ERP-balance caveat (fixed policy text; no new source values) | ✔ | ✔ | ✔ |
| D-071 Cari notice: invoice age is not overdue, sign meaning, 320 = supplier (fixed policy text; no new source values). Cari balance fields (signed balance, account class, direction, oldest invoice month, `overdue: null`) belong to the analytical row below | ✔ | ✔ | ✔ |
| D-072 Cari notes: 120 = Müşteri / 320 = Satıcı labels, USD-column note (fixed text), and the Cari report date with its provenance (the date and "dosya adındaki tarih" / "yükleme tarihi"; the original file name itself is shown only in the owner's upload preview) — part of the Cari analytical row, withheld until VERIFIED | notice today | same | same |
| **D-073: the factual Cari list (`receivables.collections`, VERIFIED-certified; shown only while its evidence is fresh — 60 days — and bound)** — per Cari account: code, recorded name, representative code, class (Müşteri (120) / Satıcı (320) / Sınıflandırılmadı), exact signed TL balance, sign wording, oldest invoice month; the report date with its provenance; fixed notes (no overdue, USD apart). No totals, ranking or overdue. **D-074: behind the website password — everyone who has the website password can read these account-level balances.** Not public by link. Whether this level is acceptable for a hosted site (or the list should need the financial password) is an **open owner question**, not decided by D-074 | ✔ | ✔ | ✔ |
| Analytical tables, action queue, drawer facts, Günlük Satış, Customer 360 profile, Fırsatlar, fair history | notice today; ✔ once VERIFIED | same | same |
| Customer directory (code + recorded name) — **approved** | ✔ | ✔ | ✔ |
| CRM follow-ups, contacts, **notes, owner labels, legacy memory text** — **approved** | ✔ | ✔ | ✔ |
| Fair campaigns and members incl. **notes** — **approved** | ✔ | ✔ | ✔ |
| Veri Kontrol Merkezi summary: number of open issues by severity / review status / issue type (fixed Turkish type labels), date of the last observation, whether these counts describe the served upload (`sync`: in_sync / out_of_sync / no_generation, with fixed Turkish text), the fixed "review is not approval" notice — **new in VKM-1 (D-062, D-063)** | ✔ | ✔ | ✔ |
| Veri Kontrol Merkezi details: issue IDs, titles, source and column / field locators, affected-record **counts**, affected analyses, first / last seen, presence, per-source observation state, short fingerprints and generation IDs, review history, **owner notes** | | | ✔ |
| Health: AI provider / model, data folder name, data date | ✔ | ✔ | ✔ |
| Kredi / Gider-Gelir / Bütçe sections (withheld today) | | ✔ | ✔ |
| Owner workbook file names, error details, operations summary, system state, known issues, reload | | | ✔ |
| Source fingerprints / declared dates, context export, AI telemetry | | | ✔ |
| Upload Center page `/yukleme` (fixed text and controls only; no data) — **new in CP3 (D-065)** | ✔ | ✔ | ✔ |
| Upload Center content: uploaded file **labels** (the owner's original file names), sizes, fingerprint prefixes, declared report details, recognized sheet names and required-column status, validation errors / warnings (fixed Turkish text, schema labels, counts), VKM preview, gate decisions per section, published selection, upload history — **new in CP3 (D-065)** | | | ✔ |
| Any change (refresh / upload / publication, CRM, fairs, actions, AI, data review, settings) | | | ✔ |
| Raw Excel, SQLite, manifests, `.env`, logs | never | never | never |

**Answered by the owner (D-061, D-074):** viewer visibility of customer names, CRM / follow-up / fair notes —
approved (now for website-password holders, not the public); Yönetim financial sections — financial password,
read-only; a website password in front of everything (D-074).
**Open (owner):** the access level of the account-level Cari list on a hosted site (website or financial password).
**Still required before public hosting (record as a dated decision):** whether to add a network gate (VPN / IP
allow-list / access proxy) in front of public pages; the hosting region under KVKK; final CP8 checklist.

## 7. CP1B (D-074) acceptance

**Synthetic (CI):** `tests/test_m8_cp1b_site_password.py` (real sessions): only `/giris` is public; every data API
answers 401 `site_auth_required` without a session (also with the legacy `X-StockAgent` header); `/`, `/?…` and
`/yukleme` answer the site-password page (401, `no-store`, no data, no generation header); a page once served to a
viewer is not served after logout or to another browser; a missing or malformed website hash refuses
(`site_not_configured`) instead of going public, while the other passwords still open the site; wrong-password
throttling, HTTPS-or-loopback and same-origin rules apply to the website login; idle / absolute expiry, owner
revocation of website sessions and a changed website password end only website sessions; the website role gets 401 on
the financial projection and 403 on every owner read and write; logins never downgrade and rotate the session; the
provisioning command refuses a password used by another role. Updated: `test_m8_cp1_access.py`,
`test_m8_cp1_finance.py`, VKM, CP3 and D-073 tests; browser tests (`test_m8_cp1_browser.py`: site-password page,
wrong password, bookmark `#yonetim` kept, refresh keeps the session, logout and revocation return to the password
page; `test_m8_cp3_browser.py`: anonymous `/yukleme` shows only the password page; the website and financial viewers
cannot use the Upload Center; the owner uploads, validates and publishes).

**Owner-local (Mac, 2026-11-03, real exports, read-only copies; throwaway passwords deleted afterwards):** originals =
copies (Stok `63602ae3e8bf`, Satış `be1d895e426b`, Cari `672fac2f5bf6`, unchanged). The owner uploaded, validated
(53 s) and published (53 s) through the Upload Center → core `01d305886628f6c6`, 1 verified (`receivables.collections`)
/ 4 not_verified / 28 invalid; policy `m8-cari-d073`, flag True, Cari max age 60.

| Role | `GET /` | Cari list | Financial projection | Owner view | Upload |
|---|---|---|---|---|---|
| Anonymous | 401 site-password page (3,171 bytes, no business data); 11 / 11 data APIs and pages 401; `/giris` 200 | — | 401 | 401 | 401 |
| Website viewer | 200, displayed = `receivables.collections`, page generation = status generation | 824 rows (796 × 120, 28 × 320) | 401 `finance_auth_required` | 403 | 403 |
| Financial viewer | 200, same | 824 rows | 200 | 403 | 403 |
| Owner | 200, same | 824 rows | 200 | 200 | 201 |

Viewer logout → `GET /` 401 (password page). Owner "end website sessions" revoked 2 sessions → the website viewer's
next API call 401; the financial session was not refused (its CRM call answered 503 while the Customer 360 build was
still loading — not an access refusal).
