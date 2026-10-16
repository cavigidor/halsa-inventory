# M7-2 — Günlük Satış: deterministic daily sales queue

Decision: D-043. Status: done (2026-10-16). Builds on M7-1 Customer 360 (D-042) and the M5 action engine (D-022).

## 1. Question and scope

"Bugün kimi aramalıyım, neden ve önce hangi konuyu konuşmalıyım?"

M7-2 answers it with one card per customer, built only from deterministic action categories that already exist and
are already tested:

| Category | Kind | Source of the reason |
|---|---|---|
| `collections` (Tahsilat) | collection | M5 aging logic (invoice account, account level) |
| `winback` (Geri Kazanım) | sales | M5 winback producer |
| `real_decline` (Reel Satış Düşüşü) | sales | M5 real-decline producer |

Product (overstock) actions stay in the M5 "✨ AI Aksiyon Merkezi" only.

M7-2 adds **no new rule, threshold or score**:
- no cross-sell, Hedef potential, penetration or territory;
- no fair invitations, CRM follow-up dates or new-buyer discovery;
- no positive-growth calls and no salesperson filter.

Detailed Collections (M6-4) stays blocked. The collections reason is the existing account-level aging reason, never
document-level.

## 2. Candidate seam (no change to M5)

`services/actions.py`:
- `produce_candidates(inputs)` runs every registered source, validates each action and sorts it stably by score,
  **without any cut**.
- `produce_all(inputs, limit)` is now `produce_candidates(inputs)[:limit or MAX_DAILY_ACTIONS]`. It is the same
  producers, the same validation, the same stable sort and the same cut as before.

`services/context.build_context`:
- Takes the uncut list once.
- Sets `ctx["actions"] = candidates[:MAX_DAILY_ACTIONS]`, so the M5 top 15 is byte-identical. Ids, scores, facts,
  order and therefore the AI cache keys are unchanged.
- Keeps the **customer** candidates privately (below).

Because the queue reads the uncut list, product actions can never take Daily Sales slots. In the synthetic test
dataset the M5 top 15 is entirely overstock, yet the queue still holds every customer reason.

Proof:
- `test_03_m5_top15_unchanged` compares `ctx["actions"]` against a verbatim copy of the old `produce_all` on the
  same inputs, for both synthetic datasets.
- On real data the same comparison printed `True`.

## 3. Private / public boundary

`services/context.AgentContext` is a `dict` subclass. It carries two **attributes**, which are not dict items:

| Attribute | Content | Bound |
|---|---|---|
| `sales_queue_candidates` | tuple of customer actions from the uncut list, in Python score order | `MAX_SALES_QUEUE_CANDIDATES` (200) |
| `sales_queue_rows` | the already-computed collections / winback / shrinking rows of those candidates only | same codes |

Because JSON and pydantic serialize dict items only, the attributes never reach:
- `/api/context`;
- `agent_context.json`;
- `/api/agent/actions`;
- evidence packets;
- logs.

Tests 05 and 06 search all of these for the private action ids. `sales_queue` appears in no AI or evidence module.

The attributes live on the context object itself. A controlled refresh installs a new `AgentContext` (`replace_context`),
so the candidates swap **atomically** with the context; there is no separate cache to go stale.

Entity fallback (`services/entity.py`):
- The customer index is built from the public lists exactly as before.
- Customers that exist only in `sales_queue_rows` are added with `setdefault`.
- Every customer M5 already knew keeps exactly its M5 facts (tested). The existing draft endpoint can therefore
  resolve queue customers that fall outside the top-N lists.

## 4. Queue service (`services/sales_queue.py`)

`build_queue(ctx, snap, statuses=None, limit=None, flt="all", upsert=True)`. Inputs are all in memory: the private
candidates, the Customer 360 snapshot of the same sources and the SQLite statuses. There is no Excel read and no AI.

1. **Filter.** Keep the queue categories only; drop reasons hidden by state (`store.is_hidden`: completed, ignored,
   or deferred until a future date).
2. **Resolve the customer.**
   - Collections: `snap.account_customers[account]`.
     - Exactly one canonical customer → attach with `scope: invoice_account` and `account_code`.
     - Several customers (a shared account) → never assigned or divided; counted as
       `shared_collection_accounts_not_assigned`.
     - No customer → counted as `unlinked_collection_accounts`.
     - Excluded reasons stay in M5 unchanged.
   - Sales reasons: the action's customer must exist in `snap.customers` (D-003, exact). Otherwise the reason is
     excluded and counted as `unresolved_sales_customers`.
3. **KAPALI.**
   - If the unique master (`liste`) says KAPALI (`master_closed is True`), sales reasons are removed
     (`closed_sales_reasons_removed`) and collections are kept.
   - A duplicate master code gives `closed = None`. Nothing is invented, the reasons stay, and the card shows a
     `master_duplicate` warning.
   - The cached sales-file KAPALI column (DQ-5) is not used.
4. **Group.** One card per canonical customer.
   - **Reason order:** score ↓, then category registration order, then action id.
   - **`queue_score`** = the maximum visible reason score. Secondary reasons add nothing.
   - **`collection_first`** is true when a collections reason is visible. The first collections reason becomes the
     **primary** reason, even when a sales reason scores higher. This sets the order of topics only; it adds no
     score.
   - **Primary otherwise:** the highest-scored reason.
   - **`priority`** = `config.band(queue_score)`.
5. **Order cards** by `queue_score` ↓, then collection-first first, then customer code ↑.
6. **Filter** (server-side): `all`, `collection_first`, `collections`, `winback`, `real_decline`. `filter_counts`
   always covers the whole queue.
7. **Limit.** The default is `MAX_DAILY_SALES_QUEUE` (20); requests are clamped to 1…`MAX_SALES_QUEUE_LIMIT` (50).
   `truncated` and `visible_customers` are reported.
8. **Upsert** the returned reasons into the shared SQLite `actions` table. This uses the same ids, and existing rows
   are untouched. The existing `/api/actions/{id}/complete|defer|dismiss|reopen` endpoints then work for reasons
   outside the M5 top 15.

Card identity (`display_name`, `last_order_date`, KAPALI, master status) comes from Customer 360. The legacy M5
"Temsilci:" fact (the sales file's cached lookup) is stripped from the reasons. M7 shows no salesperson.

The draft block on each card holds:
- `default_purpose`:
  - collection-first → `collection`;
  - winback → `winback`;
  - decline → `sales_followup`;
  - KAPALI → `collection`.
- `allowed_purposes`: `["collection"]` for a trusted KAPALI customer, otherwise all five.
- `collection_target`: the collections invoice account.
- `sales_target`: the customer code.

## 5. API

`GET /api/sales-queue?limit=&filter=` returns `SalesQueueResponse` (typed, pydantic):
- `as_of_data_date`, `generated_at`, `filter`, `limit`, `visible_customers`, `returned`, `truncated`;
- `filter_counts`, `filters` (Turkish labels), `diagnostics` (counts only);
- `items`;
- `customer360_state: "ready"`, `sources_consistent: true`, `ai_used: false`.

| Situation | Response |
|---|---|
| Customer 360 is building | 503 `{"state":"building","message":"Müşteri verisi hazırlanıyor…"}`. The UI polls and never starts a second build. |
| Customer 360 error | 503 with its error code (for example `source_date_mismatch`) |
| Live context signature ≠ snapshot signature | 503 `building`. Candidates of A are never combined with customers of B. |
| Context without private candidates (for example one loaded from JSON) | 503 `queue_candidates_unavailable` |

No paths, names or codes appear in errors or logs. The route never calls the AI provider.

## 6. UI — "Günlük Satış" tab

A marked block (`<!--SQ-TAB-START-->` … `<!--SQ-TAB-END-->`, from `_sq_panel`) placed before "Müşteri 360". The rest
of the page is byte-identical without it (tested). The heading is **"Bugün kimi aramalıyım?"**.

**Loading:**
- Lazy: nothing is fetched until the tab opens.
- Under `file://` it shows "Günlük Satış için panoyu http://127.0.0.1:8000/ üzerinden açın." and makes no request.
- While Customer 360 builds it shows "Müşteri verisi hazırlanıyor…" and polls.

**Filter chips:** Tümü / Tahsilat önce / Tahsilat / Geri Kazanım / Reel Satış Düşüşü, with counts.

**Cards**, in Python order (never re-sorted), each showing:
- priority, score, KAPALI badge, master badges;
- name, code, last order date;
- the collection-first warning;
- the primary reason;
- reason chips.

**Per card:**
- **Gerekçeler (n):** the reason list (category, scope, account, score, confidence, status, facts) with
  ✓ Yapıldı / ⏸ Ertele (date picker, default +7 days) / ✕ Gizle.
  - Each button POSTs to the shared M5 endpoints, then the queue reloads, so the server recomputes reasons, score
    and order.
  - A toast offers **Geri al** (reopen).
  - On failure the card shows "Durum kaydedilemedi." and nothing changes.
- **Müşteri 360:** switches to the Müşteri 360 tab and loads that exact customer through the additive
  `window.StockAgentC360.open(code)` hook.
- **Mesaj taslağı:** opens a panel with purpose (only the allowed purposes) and channel. Nothing is sent until
  **Taslak oluştur** is clicked; that is the only AI-capable request, the existing copy-only
  `POST /api/agent/draft-message`. A collection purpose targets the collections invoice account.

**Safety and layout:**
- All text is set with `textContent`; there is no `innerHTML`.
- Phone layout at 390 px: names wrap, scores never split, buttons are at least 36 px, no page-level horizontal
  overflow.

## 7. Real-data check (5 Oct 2026 exports; Mac VM; aggregates only)

| Item | Result |
|---|---|
| M5 top 15 identical to the old implementation | yes (15 actions) |
| Uncut customer actions | 793 (real decline 217 · winback 569 · collections 7) |
| Private pool (cap 200) | 200 (real decline 167 · winback 26 · collections 7); 185 outside the M5 top 15 |
| Candidate reasons before state filtering | 200; 196 visible after mapping |
| Grouped customers | 195 (filter counts: collection-first 5 · collections 5 · winback 24 · real decline 167) |
| Shared collection accounts excluded | 2 |
| Unlinked collection accounts excluded | 0 |
| Unresolved sales customers | 0 |
| KAPALI sales reasons removed | 2 |
| Final queue page | 20 of 195 (truncated); 2 collection-first; 1 multi-reason; 8 critical · 12 high |
| Queue build | ≈ 12.8 ms median in process (under tracemalloc) |
| HTTP (in-process TestClient) | 4.4 ms median; 35.8 ms on the first call (upserts 20 new rows); 11 ms for `limit=50` |
| Incremental memory | private payload ≈ 117 KB pickled; queue build peak ≈ 339 KB traced; no RSS growth (peak RSS 883 MB is the context plus the Customer 360 build, as in M7-1) |
| AI calls / paths in body / private ids in public JSON | 0 / no / no |
| Source workbooks | hash- and mtime-identical; scratch deleted |

## 8. Known limits and open items

- **No backfill past the pool.** The pool keeps the 200 best-scored customer actions. Reasons hidden by state are
  not backfilled from below the pool. With 195 customers already in the pool this is far above the 20-card page, but
  it is a bound, not a full ranking.
- **Real decline dominates** the pool on current data (167 of 200). That is the existing M5 scoring, not a new rule.
  Any re-weighting is an owner decision.
- **Two shared collection accounts** stay in M5 only. Splitting them needs an owner rule (open; not decided here).
- **Unchanged and still open:** DQ-1 to DQ-5, salesperson of record, revenue basis, collection thresholds and file
  of record.
- M7-3 (cross-sell, potential, territory) is not started.
