# M8-CP1 — Simplified web access and owner administration (D-060)

**Status:** implemented and tested locally (synthetic data). **Not deployed.** Public hosting needs the owner's
explicit, written sign-off on §6 first. Accuracy gate unchanged: no metric is VERIFIED and
`GENERATION_BOUND_API_EVIDENCE = False`.

Halsa Analysis is a **browser-based web application** (existing FastAPI + HTML/JS + SQLite). Nobody installs
anything. The owner's father opens a link; the owner logs in only to change things.

## 1. Access model

| Who | How | Can |
|---|---|---|
| **Viewer** (anyone who can reach the server; the father) | opens the link, no login | open the dashboard, switch tabs, read every permitted read-only view and the customer drawer, search Customer 360, read CRM / follow-ups and fair campaigns |
| **Owner** | `/giris` → one password | everything a viewer can, plus every data-changing and administrative operation, and the owner-only views |

There are no user accounts, usernames, e-mail, OAuth, password recovery or roles (owner decision 2026-10-22). The
father does not need, and does not get, a login.

**Logging in never reveals an unverified number.** The accuracy gate (D-049) applies to the owner exactly as to a
viewer. Today every analytical metric is still withheld for both.

## 2. Route classification (every route; `services/access.py`)

Unlisted routes are refused for everyone (403 `access_unclassified`). A state-changing method can never be viewer
level, even by mistake (it is treated as owner-write). Each GET was checked for side effects (§2.5).

### 2.1 Viewer-safe read-only (no login)
| Route | Notes |
|---|---|
| `GET /` | dashboard; only the published, gate-authorized bytes (unchanged D-053/D-054 checks) |
| `GET /giris` | fixed login page, no data |
| `GET /api/health` | the context-error text is replaced by a generic sentence for viewers (it can name local files) |
| `GET /api/agent/categories`, `GET /api/macro` | reference / status |
| `GET /api/agent/actions` | action queue (metric-gated; withheld today) — see §2.5 |
| `GET /api/agent/customer/{code}`, `GET /api/agent/product/{code}` | drawer facts (metric-gated; withheld today) |
| `GET /api/customer360/status` | **`?retry=true` is owner-only** (restarts the build) |
| `GET /api/customer360/search`, `GET /api/customer360/{code}` | directory: code + recorded name; profile metric-gated |
| `GET /api/sales-queue` | Günlük Satış (metric-gated; withheld today) — see §2.5 |
| `GET /api/opportunities/summary`, `GET /api/opportunities/territories` | metric-gated |
| `GET /api/fairs/history`, `/history/{id}`, `/candidates` | metric-gated |
| `GET /api/fairs/campaigns`, `/campaigns/{id}`, `/campaigns/{id}/customers` | operational records, analytic fields redacted |
| `GET /api/crm/summary`, `/followups`, `/recent`, `/customers/{code}` | operational records, analytic fields redacted |

### 2.2 Owner-only read
| Route | Why |
|---|---|
| `GET /api/owner/overview`, `GET /api/owner/status` | Yönetim: company loans, expenses / income, budget, source file names, system state; the first call also loads the owner workbooks |
| `GET /api/accuracy/status` | source file names, fingerprints, declared dates (diagnostics) |
| `GET /api/context` | bulk export of the entire analysis context (no screen uses it) |
| `GET /api/ai/calls` | AI usage / cost telemetry |

### 2.3 Owner-only write / administration (session + CSRF token)
`POST /api/context/refresh` (re-reads the selected reports and replaces the dashboard — the report-replacement
operation that exists today), `POST /api/owner/reload`, `POST /api/agent/actions/enrich`,
`POST /api/agent/customer/{code}/analyze`, `POST /api/agent/product/{code}/analyze`, `POST /api/agent/draft-message`
(all paid AI), `POST /api/scenario`, `POST /api/actions/{id}/complete|defer|dismiss|reopen`,
`POST /api/fairs/campaigns`, `PUT /api/fairs/campaigns/{id}`, `POST /api/fairs/campaigns/{id}/archive`,
`POST|PUT /api/fairs/campaigns/{id}/customers`, `POST /api/crm/customers/{code}/interactions`,
`PUT /api/crm/customers/{code}/followup`, `POST /api/auth/logout-all`.

Future upload (CP3), publication (CP4) and Veri Kontrol Merkezi decisions (VKM-1) must be registered here as
owner-write; the register test fails on any unclassified route.

### 2.4 Session endpoints and disabled interfaces
- `GET /api/auth/session` (viewer → `authenticated: false`), `POST /api/auth/login`, `POST /api/auth/logout`.
- **Disabled:** `/docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect` (404 for everyone). No static-file mount,
  no directory listing, no raw workbook, database, manifest or `dashboard.prev.html` route exists.

### 2.5 GET side effects found and how they are handled
- `GET /api/customer360/status?retry=true` restarts a failed build → **owner-only** parameter.
- `GET /api/owner/*` lazily loads the owner workbooks → owner-only routes.
- `GET /api/agent/actions` and `GET /api/sales-queue` insert the published deterministic queue into the action table
  (insert-only; status `open`, history event `generated`). They never change a status, note, follow-up or campaign,
  and the client cannot choose what is inserted. Kept, because owner actions refer to those rows; a test proves a
  full anonymous GET sweep changes no human decision and a second sweep changes nothing at all.
- `GET /` and the context reads may finish the deterministic build of the already-selected sources (same as the
  startup warm-up). No source is selected, uploaded or replaced by a GET.

## 3. Security protections

- **Password:** never stored or committed. Only an scrypt hash (Python `hashlib.scrypt`, N=2¹⁵, r=8, p=1, 16-byte
  salt) in the private `.env` as `HALSA_OWNER_PASSWORD_HASH`. Minimum 12 characters; weaker scrypt parameters and
  malformed values are refused (administration then stays disabled). Constant-time comparison.
- **Session:** random 256-bit token in cookie `halsa_owner` — `HttpOnly`, `SameSite=Strict`, `Path=/`, `Secure` over
  HTTPS (or `HALSA_COOKIE_SECURE=1`). SQLite stores only its SHA-256. Absolute limit 12 h (1–72), idle limit 120 min
  (5–720). At most 5 sessions. Revocable by logout, "log out everywhere", `python -m services.owner_auth
  revoke-sessions` and by changing the password hash. The session rotates on every login.
- **CSRF:** every owner write needs `X-CSRF-Token` = HMAC of the session token (only the same-origin page can read
  it from `/api/auth/session`), and a browser-sent `Origin` must be this server. `SameSite=Strict` as a second layer.
  `X-StockAgent` is still required on mutations as before, but it is **not** authentication.
- **Login throttling:** 5 failures per client and 20 failures overall per 15 minutes → HTTP 429 with `Retry-After`
  (even for the right password). Stored in SQLite (survives restart); client addresses only as pseudonymous hashes.
  Login attempts are serialized.
- **Transport:** a password is accepted only over HTTPS, or over plain HTTP when both the browser and the typed
  address are on the same computer (`http://127.0.0.1:8000`). Otherwise 403 `https_required`.
- **Fail closed:** no / malformed hash → login 503, every owner route 401; unclassified route → 403; state store
  error during the session check → treated as anonymous.
- **Headers:** `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'` (no click-jacking of owner buttons), `nosniff`,
  `Referrer-Policy: same-origin`, COOP/CORP same-origin, HSTS over HTTPS, `Cache-Control: no-store` on owner and
  session responses.
- **No leakage:** login validation errors never echo the body; logs never contain the password, token, hash, client
  address or attempt counts; the health endpoint hides exception text from viewers.

## 4. Configure the owner login locally

```bash
source .venv/bin/activate
python -m services.owner_auth hash-password      # asks twice; prints ONE line HALSA_OWNER_PASSWORD_HASH=scrypt:…
# paste that line into your PRIVATE .env (never into .env.example, chat or GitHub)
uvicorn app:app --host 127.0.0.1 --port 8000      # restart after changing .env
python -m services.owner_auth status              # "Yönetici girişi: yapılandırıldı", open sessions
```

Open `http://127.0.0.1:8000/` — viewer mode, no login. Click **Yönetici girişi** (or open `/giris`), enter the
password; the header shows "Yönetici girişi açık" and a **Çıkış yap** button.

- Lost password: run `hash-password` again, replace the line, restart. All old sessions stop working.
- Lost / shared device: `python -m services.owner_auth revoke-sessions`, or "log out everywhere"
  (`POST /api/auth/logout-all` while logged in).
- Over the local network (e.g. a phone on `http://192.168.x.x:8000`) viewing works, **login is refused** because the
  password would travel unencrypted. Use HTTPS (hosting checkpoint) for remote administration.

## 5. Remaining risks

1. **Public-by-link means public.** A URL is not a secret: it leaks through browser history, sharing, screenshots,
   referrers and scanners. Anyone who has it sees everything in §6.
2. **Today's viewer-visible personal data** (§6 "visible now"): customer codes and names, CRM outcomes, next actions,
   owner labels, free-text notes and legacy memory text, fair-member notes and stages. This is customer / contact
   data under KVKK; a privacy and cross-border assessment is required before hosting.
3. **Future metric promotion widens disclosure automatically.** Whenever a metric becomes VERIFIED it becomes
   visible to every viewer, not just the owner. Each promotion PR must state that.
4. **Lockout:** an attacker who can reach the login can trigger the global throttle and lock the owner out for up to
   15 minutes (viewing unaffected). Accepted trade-off for a single password.
5. **Single factor:** one password, no second factor. A password manager and a long passphrase are recommended.
6. **Reverse proxy:** behind a proxy, uvicorn must trust the forwarded scheme / client (`--proxy-headers
   --forwarded-allow-ips=<proxy>`), otherwise login is refused (fail closed) and throttling becomes global-only.
7. **No viewer rate limits / bot protection** on read routes; the deterministic build can be triggered by the first
   viewer after a restart (same work as the warm-up).
8. **CSP** forbids framing only; the existing inline-script dashboard prevents a strict script CSP for now.
9. **Owner-only views are not hidden in the UI:** a viewer sees the Yönetim tab and an explanatory message; owner
   buttons are visible and explain that a login is needed when pressed.
10. Not yet built: upload UX (CP3), one-generation publication (CP4), backups (CP5), hosting/TLS (CP8).

## 6. What would become internet-accessible (owner sign-off required before any public hosting)

| Information | Viewer can see now | After metric verification | Owner only |
|---|---|---|---|
| Dashboard shell: tab names, data date, source **file names** in the header, withheld notices | ✔ | ✔ | |
| Analytical tables (stock, reorder, overstock, win-back, momentum, order size, margin, receivables, risk) | notice only | ✔ every verified one | |
| AI Aksiyon Merkezi queue, customer / product drawer facts | notice only | ✔ | |
| Günlük Satış, Customer 360 profile, Fırsatlar, fair history and segments | notice only | ✔ | |
| Customer directory search: **customer code + recorded name** | ✔ | ✔ | |
| CRM: follow-up list (code, name, due date, purpose, next action), recent contacts (outcome), customer CRM page incl. **free-text notes, owner labels, legacy memory text** | ✔ | ✔ | |
| Fair campaigns: names, dates, members (code, name, stage, next-action date, **note**) | ✔ | ✔ | |
| Health: AI provider / model name, data folder name, data date | ✔ | ✔ | |
| Yönetim: Kredi, Gider/Gelir, Bütçe, operations summary, source file names, system state, known issues | | | ✔ |
| Source fingerprints / declared dates / verification states (`/api/accuracy/status`) | | | ✔ |
| Whole analysis context export (`/api/context`), AI call telemetry | | | ✔ |
| Any change: refresh / reports, owner reload, actions, CRM, fairs, AI drafts and analyses | | | ✔ |
| Raw Excel files, SQLite database, manifests, `.env`, logs | never served | never | never served |

**Sign-off questions for the owner (record the answers as a dated decision):**
1. Is it acceptable that anyone with the link can read the customer names, CRM notes and fair notes listed above?
   If not: make those routes owner-only, or keep the link private behind network-level protection.
2. Should Yönetim stay owner-only (current) or become viewer-visible for the father once its metrics verify?
3. Is public-by-link acceptable at all for this data, or should hosting add a network gate (VPN / IP allow-list /
   access proxy) in front of the viewer pages?
4. Which hosting region is acceptable for this data under KVKK?
