# M8-CP1 — Web access: public viewer, financial viewer, owner (D-060, revised by D-061)

**Status:** implemented and tested locally (synthetic data), PR #30. **Not deployed.** Accuracy gate unchanged: no
metric is VERIFIED and `GENERATION_BOUND_API_EVIDENCE = False`.

Halsa Analysis is ONE **browser-based web application** (existing FastAPI + HTML/JS + SQLite). Nobody installs
anything.

## 1. Access model (owner-approved 2026-10-22, D-061)

| Level | How | Can |
|---|---|---|
| **1. Public viewer** | opens the link, no password | the normal dashboard: every tab, the action queue, Günlük Satış, Customer 360 search, CRM / follow-ups, fair campaigns — **including customer names, CRM notes, follow-ups and fair notes (explicitly approved)** |
| **2. Financial viewer** (the owner's father and other trusted financial viewers) | one shared **financial viewing password**, entered once per session in the Yönetim tab | everything a public viewer sees + the **financial sections of Yönetim** (Kredi, Gider/Gelir, Bütçe) — **read-only**, nothing else |
| **3. Owner administrator** | separate **owner password** at `/giris` | everything, incl. full Yönetim (operations, system state, known issues, reload), and every data-changing / administrative operation |

No usernames, no accounts. The financial password can never create an owner session; the owner session opens the
financial view without the financial password.

**No login reveals an unverified number.** The accuracy gate applies identically at every level; today every
analytical metric and every financial section is withheld for all three.

## 2. Route permissions (every route; `services/access.py`)

Levels: `viewer` (anyone), `finance_read` (financial viewer or owner, GET only), `owner_read`, `owner_write`
(owner + CSRF token), `auth` (the login/session endpoints). Unlisted routes → 403 for everyone. A state-changing
method can never be a read level.

| Route | Public | Financial viewer | Owner |
|---|:-:|:-:|:-:|
| `GET /`, `GET /giris` | ✔ | ✔ | ✔ |
| `GET /api/health` (exception text only for the owner) | ✔ | ✔ | ✔ |
| `GET /api/agent/categories`, `GET /api/macro` | ✔ | ✔ | ✔ |
| `GET /api/agent/actions`, `/api/agent/customer/{code}`, `/api/agent/product/{code}` | ✔ | ✔ | ✔ |
| `GET /api/customer360/status` · `?retry=true` | ✔ · ✗ | ✔ · ✗ (403) | ✔ · ✔ |
| `GET /api/customer360/search`, `/api/customer360/{code}`, `/api/sales-queue` | ✔ | ✔ | ✔ |
| `GET /api/opportunities/summary`, `/territories` | ✔ | ✔ | ✔ |
| `GET /api/fairs/history`, `/history/{id}`, `/candidates`, `/campaigns`, `/campaigns/{id}`, `/campaigns/{id}/customers` | ✔ | ✔ | ✔ |
| `GET /api/crm/summary`, `/followups`, `/recent`, `/customers/{code}` | ✔ | ✔ | ✔ |
| **`GET /api/finance/overview`** (Kredi / Gider-Gelir / Bütçe projection) | ✗ 401 | ✔ | ✔ |
| `GET /api/vkm/summary` (Veri Kontrol Merkezi counts and sync state only, allow-list `VkmPublicSummary`) | ✔ | ✔ | ✔ |
| `GET /api/vkm/issues`, `GET /api/vkm/issues/{issue_id}` (issue list, locators, history, notes) | ✗ 401 | ✗ 403 | ✔ |
| `GET /api/owner/overview`, `GET /api/owner/status` (full Yönetim, system) | ✗ 401 | ✗ 403 | ✔ |
| `GET /api/accuracy/status`, `GET /api/context`, `GET /api/ai/calls` | ✗ 401 | ✗ 403 | ✔ |
| every POST / PUT below | ✗ 401 | ✗ 403 | ✔ (+ CSRF) |
| `GET /api/auth/session`, `POST /api/auth/login`, `POST /api/auth/finance-login`, `POST /api/auth/logout` | ✔ | ✔ | ✔ |
| `/docs`, `/redoc`, `/openapi.json` | 404 | 404 | 404 |

Owner-write routes: `POST /api/context/refresh` (report refresh; future upload), `POST /api/owner/reload`,
`POST /api/agent/actions/enrich`, `POST /api/agent/customer/{code}/analyze`, `POST /api/agent/product/{code}/analyze`,
`POST /api/agent/draft-message` (paid AI), `POST /api/scenario`, `POST /api/actions/{id}/complete|defer|dismiss|reopen`,
`POST /api/fairs/campaigns`, `PUT /api/fairs/campaigns/{id}`, `POST /api/fairs/campaigns/{id}/archive`,
`POST|PUT /api/fairs/campaigns/{id}/customers`, `POST /api/crm/customers/{code}/interactions`,
`PUT /api/crm/customers/{code}/followup`, `POST /api/auth/logout-all`, `POST /api/auth/finance-sessions/revoke`,
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
  status, note or decision); a test proves an anonymous GET sweep changes no human decision.

## 3. Security protections

- **Passwords:** never stored or committed; only scrypt hashes (N=2¹⁵, r=8, p=1, 16-byte salt) in the private `.env`:
  `HALSA_OWNER_PASSWORD_HASH`, `HALSA_FINANCE_PASSWORD_HASH`. ≥12 characters. Malformed / weak → that login disabled.
  The provisioning command refuses one password for both roles.
- **Sessions:** random 256-bit token in the HttpOnly, SameSite=Strict cookie `halsa_owner` (Secure over HTTPS); SQLite
  keeps only its SHA-256 **and its role**. 12 h absolute / 120 min idle (bounded env). Per-role caps (owner 5, finance
  10). Rotation on login. Revocation: logout; owner "log out everywhere" (all roles); owner "end financial sessions";
  `python -m services.owner_auth revoke-sessions [--owner|--finance]`; changing a role's password revokes **only that
  role's** sessions.
- **Role separation:** the role comes from the login endpoint, never from the request; the middleware checks the
  session's role for every owner route (403 `owner_required` for a financial session) and for the financial route.
- **CSRF:** owner writes need the session's HMAC-derived CSRF token + same-origin `Origin`; `SameSite=Strict`.
  `X-StockAgent` is a legacy guard, not authentication.
- **Throttling:** per client (5) and global (20) failures per 15 minutes, **per role**; HTTP 429 + `Retry-After`;
  pseudonymous client keys; login attempts serialized.
- **Transport:** passwords only over HTTPS, or plain HTTP on the loopback interface (local use) — otherwise 403.
- **Headers:** frame-deny + CSP `frame-ancestors 'none'`, nosniff, same-origin referrer/COOP/CORP, HSTS over HTTPS,
  `Cache-Control: no-store` on every financial, owner and session response.
- **No leakage:** login bodies never echoed; logs never contain passwords, tokens, hashes, client addresses or counts.

## 4. Configure locally

```bash
source .venv/bin/activate
python -m services.owner_auth hash-password             # owner password  → HALSA_OWNER_PASSWORD_HASH=…
python -m services.owner_auth hash-password --finance   # financial password (must differ) → HALSA_FINANCE_PASSWORD_HASH=…
# paste both lines into your PRIVATE .env (never .env.example, chat or GitHub); restart the server
python -m services.owner_auth status                    # both roles configured? open sessions per role
```

- **Father / financial viewer:** open `http://127.0.0.1:8000/#yonetim` (or click **Yönetim**) → "Finans
  görüntüleme şifresi" → **Aç**. The header then shows "🔓 Finans görüntüleme açık (salt okunur)" and **Çıkış yap**.
  The password lasts for the session (12 h, or 120 min without activity).
- **Owner:** `http://127.0.0.1:8000/giris`.
- **Lost phone / leaked financial password:** run `hash-password --finance` again (all financial sessions end), or
  `revoke-sessions --finance`, or (logged in as owner) `POST /api/auth/finance-sessions/revoke`.
- Over the local network (`http://192.168.x.x`) viewing works but **both password logins are refused** (unencrypted).

## 5. Remaining risks

1. **Public-by-link means public** (approved for the normal dashboard): customer codes / names, CRM outcomes / notes /
   owner labels, follow-ups, fair notes. The URL can leak via history, sharing, screenshots and scanners.
2. **Every metric later promoted to VERIFIED becomes public** — not just visible to the father. Financial sections
   promoted later become visible to every financial-password holder. Each promotion PR must say so.
3. **A shared password is shared:** anyone told the financial password reads the financial sections until it is
   rotated. No per-person audit trail (no accounts, by decision).
4. **Lockout:** someone who can reach the site can throttle a role's login for up to 15 minutes (per role; viewing
   unaffected). Single factor, no MFA.
5. **Reverse proxy** must forward the real scheme/client (`--proxy-headers --forwarded-allow-ips=<proxy>`), or logins
   are refused (fail closed) and throttling becomes global-only.
6. No viewer rate limits / bot protection; CSP limited to framing (inline-script dashboard).
7. Not built yet: upload UX (CP3), publication (CP4), backups (CP5), hosting / TLS (CP8).

## 6. What is internet-accessible by link (before any public hosting)

| Information | Public | Financial viewer | Owner |
|---|:-:|:-:|:-:|
| Dashboard shell, tab names, data date, source **file names** in the header, withheld notices | ✔ | ✔ | ✔ |
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

**Answered by the owner (D-061):** viewer visibility of customer names, CRM / follow-up / fair notes — approved;
Yönetim financial sections — financial password, read-only.
**Still required before public hosting (record as a dated decision):** whether to add a network gate (VPN / IP
allow-list / access proxy) in front of public pages; the hosting region under KVKK; final CP8 checklist.
