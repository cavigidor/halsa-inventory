# M7-3 — Cross-sell and opportunity / territory intelligence

Decision: D-044. Status: done (2026-10-16). It builds on D-015 (Python decides), D-023 (confidence), D-042 (Customer
360) and D-043 (Günlük Satış).

## 1. What M7-3 adds, and what it does not

M7-3 adds three things:
- **A deterministic cross-sell signal** at the product-GROUP level (`anagrup`).
- **The same signal surfaced in three places:**
  - as a new Günlük Satış reason;
  - as a Customer 360 "Fırsatlar" card;
  - as a read-only "Fırsatlar" tab.
- **Descriptive territory counts** from an explicitly configured Hedef file.

It also fixes the M7-2 queue reservoir, so categories can no longer starve each other.

M7-3 does **not** create:
- market share, penetration or total customer potential;
- salesperson rankings;
- price, margin, stock or revenue forecasts;
- fair invitations or CRM follow-ups;
- SKU recommendations.

The Trodat source, stale Hedef capacity, Risk and Kredi are not used anywhere in M7-3.

## 2. Cross-sell model (`services/opportunities.py`)

**Source.** The trusted sales frame that `build_context` has already read (`sdf`). There is no Excel read. It uses:
- the canonical D-003 customer (`_cust`);
- the order date;
- the native `anagrup` label.

Labels are used exactly as written: they are never trimmed or merged, and empty / whitespace / NaN means "no group".
If the column is missing, the model reports `unavailable` (`dimension_missing`). It never switches to `kategori`.

**Cohort.** Customers with at least one order in the window (as_of − `CROSS_SELL_RECENT_DAYS`, as_of]. Here as_of is
the sales **data date** (the frame's max date), never `today()`.

**Association** (for every directed group pair A → B, over the cohort's purchases inside the window):

| Term | Definition |
|---|---|
| `source_buyers` | cohort customers who bought A in the window |
| `target_buyers` | cohort customers who bought B in the window |
| `pair_buyers` | cohort customers who bought both in the window |
| `confidence` | `pair_buyers / source_buyers` |
| `target_prevalence` | `target_buyers / cohort size` |
| `lift` | `confidence / target_prevalence` |

"Confidence" means *the share of active A-buyers in whom B is also observed*. It is not the probability that a given
customer will buy, and the UI says so. There is no ML, embedding or AI.

**Eligibility** (all in `config.py`; model-quality guards, not financial targets; never tuned to reach a count):

| Setting | Value |
|---|---|
| `CROSS_SELL_RECENT_DAYS` | 365 |
| `CROSS_SELL_MIN_SOURCE_BUYERS` | 20 |
| `CROSS_SELL_MIN_TARGET_BUYERS` | 20 |
| `CROSS_SELL_MIN_PAIR_BUYERS` | 10 |
| `CROSS_SELL_MIN_CONFIDENCE` | 0.30 |
| `CROSS_SELL_MIN_LIFT` | 1.20 |
| `CROSS_SELL_MAX_TARGETS_PER_CUSTOMER` | 3 |
| `CROSS_SELL_SCORE_CAP` | 69 |

**Customer targets.** Customer C qualifies for target B when both hold:
- C bought an eligible source A **inside the window**;
- C has **never** bought B in the whole trusted sales history.

The second condition makes this a genuine assortment gap. A group that was bought once years ago is not recommended
again just because it has not been bought recently.

**One opportunity per target group.** When several sources lead to the same B, the strongest is chosen by confidence ↓,
then lift ↓, then pair buyers ↓, then source label. Up to two further sources are kept for display.

**At most three targets per customer**, chosen by confidence ↓, lift ↓, pair support ↓, target label. Everything is
ordered deterministically and is independent of row order (tested), so action ids are stable.

**Score:** `cross_sell_score = min(69, round(confidence × 100, 1))`.
- Support and lift are eligibility guards; confidence drives the order.
- The cap keeps cross-sell at `low` / `medium`. It can never be `high` or `critical`.
- The reason for the cap: a statistical assortment signal must not look more urgent than a collection or a major
  decline.
- No amount, Hedef field, capacity, margin or salesperson value enters the score (tested by data and by code
  inspection).

**Action** (generic contract, shared SQLite state; no second database):
- category `cross_sell`, entity `customer` (canonical code), confidence `medium`, approval required;
- the id is the usual category + entity + hash of facts.

The Turkish reason reads: "Bu müşteri yakın dönemde <A> grubundan alışveriş yaptı; benzer aktif müşterilerde <B> grubu
birlikte sık görülüyor ve bu müşteri <B> grubundan geçmişte alışveriş yapmadı."

The facts are:
- source group;
- target group;
- source buyers;
- pair buyers and their share;
- lift;
- "no target-group purchase in this customer's sales history".

There is no money, market size or Hedef fact.

## 3. Action surfaces (`services/actions.py`)

`Category.surfaces` declares where a category's actions appear (the default is the M5 action center only):

| Category | M5 action center | Günlük Satış |
|---|---|---|
| collections | ✓ | ✓ |
| winback | ✓ | ✓ |
| real_decline | ✓ | ✓ |
| overstock | ✓ | — |
| cross_sell | — | ✓ |

- `produce_all` / `ctx["actions"]` keep only the action-center surface **before** the 15-cut. Filtering a stably sorted
  list keeps the relative order, so the M5 top 15 (ids, scores, facts, order, AI cache keys) is identical. This is
  tested against a verbatim copy of the old algorithm on three synthetic datasets, with the cross-sell model present,
  and confirmed on real data.
- `/api/agent/categories` lists action-center categories only, in the unchanged M5 shape. It shows no cross-sell entry.

## 4. Per-category queue reservoir (fix for M7-2 starvation)

`context.sales_queue_reservoir` keeps, for **each** sales-queue category, its `MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY`
(200) best-scored customer candidates. Their union stays in the global candidate order. The M7-2 variable
`MAX_SALES_QUEUE_CANDIDATES` is still honoured as the per-category value when the new one is unset.

This fixes:
- real decline starving winback;
- a new category getting no reservoir;
- hidden reasons having nothing below them to surface.

There are **no quotas on the final page**: the 20 cards are still ranked by the M7-2 card rule. Real decline was not
re-weighted.

## 5. Günlük Satış integration

`cross_sell` is a normal sales reason:
- trusted master KAPALI removes it;
- collection-first is unchanged;
- the queue score is still the maximum visible reason score.

**Conversation sequence** (extends D-043):
1. collections;
2. winback;
3. all other reasons by score (real decline and cross-sell compete by score).

The sequence never changes a score. Cross-sell never comes before reactivation or collections.

The **Çapraz Satış** filter shows every eligible cross-sell customer in the reservoir, not only the first 20. State
(complete / defer / dismiss / reopen) works per reason as in M7-2. A cross-sell-only customer gets a code + name entity
row, so the existing explicit draft can resolve it. The default purpose is `sales_followup`, never `offer` (no SKU,
stock or profitability has been checked), and no group or target fact is sent to AI.

## 6. Customer 360 "Fırsatlar" card

`customer_detail(..., opportunities=model)` adds an `opportunities` section. The route passes the model only when the
live context and the snapshot share their source signature; otherwise the section is `unavailable`.

**Content:**
- at most three target groups;
- for each: the strongest source, confidence (with pair / source counts), lift, other sources, and the explicit "never
  bought" line;
- the label "Ürün kapsam fırsatı" / "Çapraz satış adayı".

**Notes:**
- "Bu bir ürün-grubu fırsat sinyalidir; tekliften önce uygun ürün ve stok ayrıca kontrol edilmelidir.";
- what the rate means;
- that no demand, revenue or SKU estimate is made.

A KAPALI customer still sees its opportunities, with a note that they are not queued.

The card never says penetrasyon or potansiyel, and it creates no ratio from Hedef capacity. That number stays in the
existing Hedef card, marked "Eski veri".

## 7. Territory intelligence (descriptive, unverified)

`territories(snap, model, dimension, limit)` uses only Hedef `city`, `district` and `sector_code` from the explicit
file. All three are `unverified`, shown with a "Doğrulanmadı" badge.

**Per label it counts:**
- matched customers (Hedef accounts that are Customer 360 customers);
- customers active in the last 365 days;
- customers with current-year orders;
- customers with cross-sell opportunities;
- opportunity targets.

**Coverage** reports Hedef accounts, matched customers and customers with an empty value.

**Order:** cross-sell customers ↓, then matched customers ↓, then label.

**The UI states:** "Hedef bölge/sektör alanları doğrulanmamış kaynak bilgisidir; bu tablo satış performansı
sıralaması değildir."

**What it never does:**
- no capacity, Risk, Kredi or salesperson field is read (tested by code inspection and by changing those values);
- no score or action comes from a territory.

**When Hedef is missing or broken:**
- not configured → "Bölge / sektör görünümü için Hedef dosyası yapılandırılmadı.";
- configured but failed → an unavailable state with no guessed geography;
- in both cases cross-sell is unaffected.

## 8. API (typed, bounded, never AI, no Excel read per call)

| Route | Returns |
|---|---|
| `GET /api/opportunities/summary?limit=20` (max 50 pairs) | `OpportunitySummary`: cohort, groups, eligible pairs, customers with opportunities, targets, targets by priority, thresholds, strongest pairs, queue reservoir size, notes; 503 `opportunities_unavailable` without a model |
| `GET /api/opportunities/territories?dimension=city\|district\|sector&limit=50` (max 200) | `TerritoryResponse`; 503 while Customer 360 builds or on a signature mismatch; 422 for any other dimension |
| `GET /api/customer360/{code}` | gains `opportunities` (`C360Opportunities`) |
| `GET /api/sales-queue?filter=cross_sell` | the queue's cross-sell filter |

There is no endpoint that lists every customer opportunity.

## 9. UI — "Fırsatlar" tab

A marked block (`_opp_panel`, `<!--OPP-TAB-START-->`) placed after Müşteri 360. The rest of the page is byte-identical
without it.

**Sections:**
1. **Çapraz Satış Modeli:** tiles, thresholds, notes, and the button **"Günlük Satış'ta Çapraz Satış adaylarını
   göster"**. The button uses the additive `window.StockAgentSQ.show('cross_sell')` hook.
2. **En güçlü ürün-grubu ilişkileri:** a table with no customer names.
3. **Bölge / sektör görünümü:** İl / İlçe / Sektör kodu, with the "Doğrulanmadı" badge and the disclaimer.

**Behaviour:**
- lazy: nothing is fetched until the tab opens;
- under `file://` it shows "Fırsatlar için panoyu http://127.0.0.1:8000/ üzerinden açın." and makes no request;
- while Customer 360 builds, the territory section shows "Müşteri verisi hazırlanıyor…" and polls;
- all text is set with `textContent`;
- tables scroll inside their cards at 390 px.

## 10. Privacy and AI separation

- The model and the reservoir are `AgentContext` attributes. They are never serialized to `/api/context`,
  `agent_context.json`, `/api/agent/actions` or evidence packets.
- `opportunities.py` imports nothing from AI, evidence or logging.
- Diagnostics are counts only. Group labels and cities appear only in the local UI/API payload, never in logs.
- The automatic paths make zero AI calls: context build, model, tab, territory, queue filter and Customer 360 section.

## 11. Real-data check (5 Oct 2026 exports; Mac VM; aggregates only)

**Cross-sell model:**

| Item | Result |
|---|---|
| `anagrup` line coverage | 100 % (182,121 lines); 86 distinct groups; all 6,754 customers have group data |
| Active cohort (365 days) | 1,937 customers (all with group data); 78 groups bought in the window |
| Eligible directed pairs | 210 (confidence 0.30–0.90; lift 1.23–21.1) |
| Customers with ≥ 1 opportunity | 1,516 (78 % of the cohort); 3,795 targets (1 target: 233 · 2: 287 · 3: 996) |
| Targets by priority | medium 1,634 · low 2,161 · high 0 · critical 0; 25 distinct target groups |
| Targets rejected as already bought | 6,224 |

**Queue and reservoir:**

| Item | Result |
|---|---|
| Uncut customer actions | real decline 217 · winback 569 · collections 7 · cross-sell 3,795 |
| Reservoir (200 per category) | real decline 200 · winback 200 · collections 7 · cross-sell 200 = 607 (≈ 433 KB pickled; model ≈ 345 KB) |
| Günlük Satış after the fix | 576 grouped customers (M7-2: 195); filters: collection-first 5 · winback 195 · real decline 200 · cross-sell 185 |
| First 20 cards | no cross-sell card (all critical/high reasons outrank the capped score). Excluded: 2 shared collection accounts (as in M7-2) and 5 KAPALI sales reasons |
| Cross-sell filter | 185 customers; cross-sell is the primary topic for 48 of the first 50 |

**Checks, performance and territory:**

| Item | Result |
|---|---|
| M5 top 15 / M7-2 reason ids and scores | identical / identical |
| Model build | 1.1 s inside a 31 s context build; ≈ 14.5 MB traced peak |
| Request latency (median) | queue 6.5 ms · cross-sell filter (50) 8.5 ms · summary 3.4 ms · territory 10–12 ms · Customer 360 detail 8.2 ms |
| Territory (explicit Hedef smoke, not authoritative) | 6,427 matched customers. İl: 81 rows (39 customers without a value); İlçe: 612 rows (194 without); Sektör kodu: 76 rows (338 without) |
| AI calls / Excel reads after build / sources | 0 / 0 / hash- and mtime-identical; scratch deleted |

**Model sanity:** the initial thresholds produce a **broad** population, not zero and not a modest one. 78 % of active
customers get at least one target, and about half of them get three.

The structural reasons:
- `anagrup` is coarse (86 groups);
- a few groups are bought very widely, so many pairs clear 0.30 / 1.2 with large support;
- only 25 groups ever become targets.

The thresholds were **not** tuned. The cap (69) and the per-category reservoir (200) keep cross-sell from crowding the
queue, but whether to tighten the guards is an owner decision.

## 12. Open items

- **Threshold review (owner):** the population is broad. Candidate levers are a higher confidence or lift, a minimum
  share of the source group, or excluding near-universal target groups. Nothing changes without a decision.
- **No SKU, stock or margin step:** a group signal is not an offer. Product suitability, stock, margin (DQ-1) and Trodat
  mapping stay open.
- **Hedef geography stays unverified:** the Hedef file of record is undecided, and salesperson of record stays open
  (D-024).
- **Not started:** M7-4 Fair Command Center and M7-5 CRM.
