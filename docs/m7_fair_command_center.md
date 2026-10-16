# M7-4 — Fuar Merkezi (Fair Command Center)

Decision: D-045. Status: done (2026-10-16). Builds on D-003, D-009, D-015, D-023, D-024, D-025, D-036, D-042, D-043,
D-044.

The feature has two deliberately separate halves:

1. **Trusted historical FUAR intelligence.** Facts come only from ERP order attribution (`Srmmerk` type FUAR).
2. **A manually curated upcoming-fair campaign.** It is local operational state that the user maintains.

The system does not invent historical invitations, attendance, a post-fair conversion window, fair ROI, an
"ETKİNLİK/SAHA = fair" rule, or attendee identities.

## 1. Historical FUAR model (`services/fairs.py`)

**Source.**
- The sales frame `build_context` has already read (`load_sales` output). There is no Excel read and no re-run of the
  M6-2 importer.
- The unique `Srmmerk` labels are parsed **once** with the M6-2 grammar (`sales_activity.parse_label`, reused, not
  re-implemented).
- Aggregation is vectorized at order-document grain. On real data it takes about 0.5 s.
- The result is attached privately to the context (`AgentContext.fairs`) and is never serialized.

**FUAR only.**
- Only orders whose label type is `SourceType.FUAR` are included. ETKİNLİK, SAHA and GENEL are excluded.
- There is no `is_fair` grouping; the fair taxonomy stays an owner decision.
- ETKİNLİK and SAHA remain visible in the existing Customer 360 event history.

**Edition identity.** The M6-2 `event_id`: edition year + exact stem + type + optional sequence. Different stems
(including partner-named events) never merge, and a sequence creates a separate edition.

**Order grain** (`sip_evrakno_seri` + `sip_evrakno_sira`):
- A multi-line order counts once; amounts and quantities sum its lines.
- An order whose lines carry different labels is excluded and counted (`label_conflict_orders`). M6-2 rejects such a
  file outright; the per-type order counts match M6-2 on files it accepts (tested).

**Per-edition facts:**
- first / last attributed order date;
- distinct orders, lines, canonical customers, products;
- quantity;
- the source-recorded `Nettutar` sum;
- missing-amount lines;
- unusable-date orders;
- customer-conflict orders;
- top 10 SKUs / anagrup / kategori by `Nettutar`, then quantity.

**Data-quality policy:**

| Case | Treatment |
|---|---|
| Lines disagree on the date, or the date is missing | Order stays in totals, is left out of date ranges and is counted. No date is invented. |
| Lines disagree on the canonical D-003 customer | Order stays in event totals, enters no customer list and is counted. |
| `Nettutar` missing | Counted. Never treated as 0. `load_sales` now also records `_amt_missing`; M5 still sums 0, unchanged. |

**Amount semantics:** the label is always **"Net Sipariş Tutarı (Nettutar)"**, with the note that it is the
source-recorded ordered net amount, not delivered or invoiced revenue (revenue basis open). It is never labelled
"Ciro" or "Fuar Geliri", and no margin is used.

**No conversion window and no ROI.** Later GENEL orders are never attributed to a fair. ROI is not calculated because
fair costs are not attributed to editions; Gider/Gelir and Bütçe are not used.

**Historical attendee limitation.** A customer with a FUAR-tagged order is a "FUAR-attributed customer" ("Bu fuara
atfedilmiş siparişi var"), never an attendee, visitor or invitee. Historical invitation and attendance lists are
missing, so the historical funnel is empty.

**Customer list** of an edition:
- canonical Customer 360 identity, with orders, `Nettutar`, quantity and first / last date;
- sorted by FUAR-tagged `Nettutar` ↓, then orders ↓, then code;
- labelled "Fuar etiketli sipariş tutarına göre". This is descriptive sorting, not a priority.

The unverified fair summary workbooks (`2026 fuarlar özet.xlsx`, Antalya analysis) are not used.

## 2. Campaigns (`services/fair_store.py`)

A reusable, multi-edition campaign system. Nothing is hard-coded to a January fair, and campaigns are created only by
the user.

**SQLite tables** (created by `store.init_db` with `CREATE TABLE IF NOT EXISTS`; existing action state, memory, AI
cache and older databases are untouched — migration tested):

| Table | Columns |
|---|---|
| `fair_campaigns` | id, name, edition_year, start_date, end_date, event_stem, event_sequence, status (`planned` / `active` / `archived`), timestamps |
| `fair_customers` | campaign_id, canonical customer_code, stage, next_action_date, note (≤ 500), timestamps |
| `fair_history` | campaign_id, customer_code, ts, event (`added` / `stage_changed` / `next_action_changed` / `note_updated` / `removed` / `restored` / `campaign_*`), from/to stage, detail — never the note text or customer records |

**Validation:**
- name 1–120 characters and not blank;
- edition year 2000–2099;
- valid dates, end ≥ start;
- optional stem ≤ 120 characters;
- sequence ≥ 1;
- stage from an allowlist.

All SQL is parameterized. Only the archive action ends a campaign; there is no permanent delete. Archived campaigns
are read-only.

**Optional ERP link.** A campaign may declare edition year + exact stem + optional sequence. Trusted FUAR orders of
that exact `event_id` are then shown as evidence. There is no fuzzy matching and no match by campaign name.

**Funnel** (manual, stable English keys):

| Key | Label |
|---|---|
| `invite_candidate` | Davet Edilecek |
| `called` | Arandı |
| `accepted` | Kabul |
| `hotel_confirmed` | Otel Teyit |
| `meeting_planned` | Görüşme Planlandı |
| `attended` | Katıldı |
| `offer` | Teklif |
| `order` | Sipariş |
| `follow_up` | Takip |
| `declined` (terminal) | Reddetti |
| `removed` (terminal) | Listeden çıkarıldı |

- Stages are never inferred from sales data or the CRM.
- **ERP evidence vs funnel:** when the linked edition has FUAR orders for a member, the UI shows "ERP'de bu fuara
  atfedilmiş sipariş bulundu." with an explicit **"Aşamayı Sipariş olarak işaretle"** button. Nothing is synchronized
  silently.
  - A disagreement is shown either way: `erp_order_stage_differs` when ERP has an order but the stage is not
    "Sipariş", and `stage_order_without_erp` when the stage is "Sipariş" but ERP has no order.
- The next-action date is explicit only; there is no automatic scheduling.

**Adding customers:**
- by exact canonical Customer 360 code only (search, add, or explicit selection from candidates or history);
- bulk add takes at most 200 per request and reports added / already present / blocked KAPALI / invalid.

**KAPALI:**
- Adding a trusted master KAPALI customer is blocked.
- Moving a member into an active stage while trusted KAPALI is blocked; terminal stages are allowed.
- A duplicate, unmatched or unavailable master status is never turned into KAPALI; it shows a warning instead.
- Historical FUAR orders of KAPALI customers stay visible.

There is no salesperson assignment, no rep-based counts and no generic CRM (that is M7-5).

## 3. Candidate segments (read-only, `services/fair_center.py`)

| Segment | Rule |
|---|---|
| Geçmiş fuar müşterisi | at least one trusted FUAR-attributed order |
| Son 365 günde aktif | Customer 360 last order within 365 days of the data date |
| Geri Kazanım / Reel Satış Düşüşü / Çapraz Satış | a current deterministic reason (uncut candidates, via a private compact reason index; cross-sell from the M7-3 model) |
| Tahsilat hassasiyeti | a collections reason on an exclusively mapped invoice account; shown with a "Tahsilat önce" warning, not blocked |

- Trusted KAPALI customers are excluded from results and counted.
- **Segments never enroll anyone.** The UI says how many customers are in a segment, and the user selects explicitly.
- **Sorting:** optionally historical FUAR first; then the existing Günlük Satış reason score (maximum current reason
  score); then last order date ↓; then code. There is no "fair potential" score.
- There is no stale-Hedef potential, growth, prospect, territory or CRM-activity segment.

## 4. Günlük Satış fair task

**Category and score:**
- category `fair_followup` ("Fuar Takibi"), on the `sales_queue` surface only (never in the M5 AI Action Center);
- produced at **request time** from SQLite;
- one fixed score, `FAIR_TASK_SCORE = 55` (medium): an operational placeholder that never varies by revenue, Hedef,
  stage or days overdue; the owner may tune it later;
- confidence `high` means the task exists, not that the customer will attend or buy; the reason text says so.

**Eligibility:** the campaign is planned or active, the stage is non-terminal, and the next-action date is today or
earlier. A member without a date is never queued.

**Identity:** the facts are campaign id, stage label and next-action date. The same campaign / customer / stage /
date gives the same id. A new date or stage gives a new task, and a completed task is never reopened.

**Sequence:** collections → winback → due fair task → the rest by score. Sequence only; the card score is still the
maximum reason score. The filter **"Fuar Takibi"** shows every due task.

**Action state vs funnel state:**
- Completing, deferring or dismissing the queue task uses the existing action table and never changes the funnel
  stage.
- Changing the stage never marks the task complete.
- The two histories stay separate (`action_history` vs `fair_history`).

**Drafts:** the default purpose is `fair_invite`, generated only on an explicit "Taslak oluştur" click. KAPALI is
blocked by Python, and the request carries only the existing draft contract (customer code, channel, purpose). No
event history, stage, note or event name reaches AI.

## 5. Customer 360

The "Fuar / etkinlik geçmişi" card gains a read-only campaign list:
- campaign, status, stage, next-action date;
- a note indicator only;
- linked ERP evidence.

Editing stays in Fuar Merkezi. There is no AI change and no extra Excel read.

## 6. API

| Route | Purpose |
|---|---|
| `GET /api/fairs/history?year=&q=&limit=` (≤ 200) | editions with counts |
| `GET /api/fairs/history/{event_id}?campaign_id=&customer_limit=` | detail with canonical customer identity |
| `GET /api/fairs/campaigns` | stages + campaigns with funnel counts, due tasks and linked evidence |
| `GET /api/fairs/campaigns/{id}` | one campaign |
| `GET /api/fairs/campaigns/{id}/customers?stage=&due=&segment=&q=` | members with identity, segments and ERP evidence / mismatch |
| `GET /api/fairs/candidates?segment=&q=&campaign_id=&fuar_first=&limit=` | read-only candidate discovery |
| `POST /api/fairs/campaigns` | create |
| `PUT /api/fairs/campaigns/{id}` | update metadata |
| `POST /api/fairs/campaigns/{id}/archive` | archive |
| `POST /api/fairs/campaigns/{id}/customers` | add (≤ 200) |
| `PUT /api/fairs/campaigns/{id}/customers` | stage / date / note |

- All routes are typed (pydantic, `extra="forbid"` on inputs).
- Every mutation requires `X-StockAgent: 1` (global middleware; no CORS).
- Unknown campaign → 404; archived → 409; KAPALI → 409; validation → 422.
- Routes that need Customer 360 return 503 `building` until it is ready and consistent.
- No AI, no Excel read per request, and no paths in errors.

## 7. UI — "Fuar Merkezi" tab

A marked block (`_fair_panel`, `<!--FAIR-TAB-START-->`) placed after Fırsatlar. The rest of the page is byte-identical
without it.

**Sections:**
1. **Campaign:** selector, "Yeni fuar oluştur" form, edit, planned / active, two-step archive, linked ERP line, and an
   empty state.
2. **Funnel:** stage chips with counts (clickable filter) and the due count.
3. **Campaign customers:** Customer 360 search + add; filters for stage, segment, due and text; cards with stage, date,
   note, "Tahsilat önce", ERP evidence + explicit mark button, mismatch notice, and a Müşteri 360 link.
4. **Candidate discovery:** segment filter, FUAR-first toggle, counts, and explicit selection into the campaign.
5. **Historical fairs:** year / text filters, editions table, and detail with date range, data-quality counts, top
   products / groups / categories, and customers with checkboxes and Müşteri 360.

**Behaviour:**
- lazy: nothing is fetched until the tab opens;
- under `file://` it shows "Fuar Merkezi için panoyu http://127.0.0.1:8000/ üzerinden açın." and makes no request;
- `textContent` only, and no browser dialogs;
- at 390 px tables scroll inside their cards.

## 8. Privacy and AI separation

- No names, codes, notes or event names are logged. Diagnostics are counts only.
- Event stems (possibly partner names) appear only in the local UI/API.
- There are zero automatic AI calls: opening, creating, searching, adding, stage changes, history and fair tasks.

## 9. Real-data check (5 Oct 2026 exports; Mac VM; aggregates only)

**Historical FUAR:**

| Item | Result |
|---|---|
| FUAR editions | 88 (edition years 2020–2026; 34 distinct stems; 1 edition with a sequence) |
| FUAR order documents / lines / customers | 2,924 / 40,899 / 1,584 |
| Other types (excluded) | GENEL 35,164 · ETKİNLİK 57 · SAHA 70 orders; 0 label-conflict orders; 0 unparsed labels |
| `Nettutar` coverage | 40,899 of 40,899 FUAR lines have an amount |
| Data quality | 0 unusable-date orders · 4 customer-conflict orders (in 4 editions; kept out of customer lists) |
| Product / category coverage | 1,579 products; anagrup and kategori present on all FUAR lines |

**Candidate segments (counts only; nothing added):**

| Segment | Customers |
|---|---|
| Historical FUAR | 1,584 (29 KAPALI excluded in results) |
| Active (365 days) | 1,937 |
| Winback | 569 |
| Real decline | 217 |
| Cross-sell | 1,516 |
| Collection-sensitive | 5 |

Overlaps with historical FUAR: active 563, winback 329, real decline 121, cross-sell 477, collections 4. 1,021
historical FUAR customers have not ordered in the last 365 days.

**Isolated campaign smoke** (temporary SQLite only; anonymous customer chosen by rule; deleted afterwards):
- create → add (1 added, 1 KAPALI blocked) → stage + today's date;
- the Günlük Satış fair reason appears with score 55;
- completing it hides it and leaves the stage unchanged;
- 0 AI calls; the real state DB was not touched.

**Performance:**

| Item | Result |
|---|---|
| FUAR model | ≈ 0.5 s (≈ 2.5 s under tracemalloc), inside a 29 s context build; ≈ 52 MB traced peak; index ≈ 370 KB |
| Request latency (median) | history 1.4 ms · detail 2 ms · candidates 1.6–9 ms · queue 6 ms · campaign customers 1.3 ms · Customer 360 detail 5 ms |

**Regression and safety:**
- M5 top 15, existing M7-2 reasons and M7-3 cross-sell results (1,937 / 210 / 1,516 / 3,795) are unchanged.
- Source workbooks are hash- and mtime-identical; scratch deleted.

## 10. Open items

- **Fair taxonomy (ETKİNLİK / SAHA), conversion window, fair cost attribution and ROI:** owner decisions.
- **Historical invitation / attendance lists:** missing.
- **Salesperson of record:** open, so there are no assignments.
- **`FAIR_TASK_SCORE` (55):** a placeholder.
- **Cross-sell threshold review:** still open (M7-3), not changed here.
- **Not started:** M7-5 generic CRM / follow-up.
