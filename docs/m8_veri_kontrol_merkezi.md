# Veri Kontrol Merkezi — permanent data-review section (design, M8-VKM)

Status: designed in PR #29 (2026-10-21; D-058); **implemented in M8-VKM-1 (2026-10-24; D-062)** — see §8 for what
was built and where it deviates from or refines this design. The accuracy gate, the generation manifest and every rule
in `docs/accuracy/release_gate.md` stay in force unchanged.

## 1. Purpose

A permanent Turkish section ("Veri Kontrol Merkezi") where the owner (and, later, authorized staff) sees every
deterministic data-quality exception found in the uploaded ERP exports, records a human review decision with a note,
and follows the same issue across monthly uploads.

Three rules are absolute:

1. **Review is not certification.** Marking an issue "incelendi" (reviewed) or "kabul edildi" (acknowledged) records a
   human decision. It never changes a metric's gate state, never sets any evidence field (`totals_consistent`,
   `mapping_approved`, `period_complete`, …), never adds an approval reference and never flips
   `GENERATION_BOUND_API_EVIDENCE`. Promotion of a metric remains a reviewed code change (release gate §4).
2. **Source data is never changed.** The uploaded workbook bytes, their SHA-256 fingerprints and the frozen
   generation stay exactly as uploaded. No issue action repairs, deduplicates, deletes, re-maps or re-dates a row.
   **A suspected duplicate is never deleted automatically** — not on detection, not on review, not on carryover.
3. **No raw values leave the private store.** Issues reference sources by fingerprint, row number and column letter;
   the UI shows only what the authorized viewer may already see. No customer names, codes, amounts or contract
   identifiers in logs, issue titles, IDs or anything committed to GitHub.

## 2. Issue types (deterministic only)

Every issue is produced by a deterministic check that already exists or is added with its own tests; no AI, no
inference, no heuristic "probably". Initial catalogue (Turkish label · code · default severity):

| Code | Türkçe başlık | Source of the check | Severity |
|---|---|---|---|
| `confirmed_error` | Kesin hata (ör. #REF!, #DIV/0! dışı Excel hatası, çelişen toplam) | `inspect_core` field evidence, footer `mismatched` | Kritik |
| `suspected_duplicate` | Olası yinelenen kayıt (ör. aynı ürün kodu iki satırda; 2022'deki aynı satış satırları) | stock row analysis, sales duplicate check | Yüksek |
| `unresolved_mapping` | Çözülmemiş kaynak eşlemesi (ör. 120/320 hesap ayrımı, DQ-5/DQ-6) | join checks, open DQ items | Yüksek |
| `unknown_report_date` | Rapor tarihi bilinmiyor / beyan edilmedi | generation declarations (no mtime inference) | Yüksek |
| `invalid_formula` | Geçersiz / önbelleksiz formül, paylaşılan formül çözülemedi | `scan_sheet`, footer `missing_cache`, `\x00unresolved-shared` | Yüksek |
| `undefined_stock_coverage` | Tanımsız stok kapsamı (#DIV/0!) — sıfır / sonsuz sayılmaz | stock coverage counts (DQ-4) | Orta |
| `unmatched_account` | Eşleşmeyen müşteri / ürün hesabı | `join_checks` (`sales_product_in_stock`, …) | Orta |
| `partial_reconciliation` | Kısmi mutabakat (hata yayılımı, desteklenmeyen SUBTOTAL) | footer `error_propagated` / `unsupported` | Orta |
| `layout_refused` | Rapor düzeni onaylı şablonla uyuşmuyor | `check_stock_template` (D-056) | Kritik |
| `missing_source` | Beklenen dosya yok (ör. Kredi dosyası yüklenmedi) | source selection | Yüksek |
| `other_deterministic` | Diğer deterministik veri kalitesi istisnası | any check with its own test | (per check) |

Severity is a display order, not a gate state. The gate state of each metric continues to come only from the
evidence (VERIFIED / NOT_VERIFIED / INVALID).

## 3. Data model

```
dq_issue                                  -- one row per stable issue (current state)
  issue_id        TEXT PRIMARY KEY        -- stable ID, see §3.1
  issue_type      TEXT NOT NULL           -- code from §2
  severity        TEXT NOT NULL           -- kritik | yuksek | orta | dusuk
  source_key      TEXT NOT NULL           -- stock | sales | aging | liste | kredi | …
  locator         TEXT NOT NULL           -- privacy-safe: column letter / header slot / row-grain key hash
  affected_metrics TEXT NOT NULL          -- JSON list of register keys whose dependencies include the locator
  first_seen_generation TEXT NOT NULL     -- generation_id
  last_seen_generation  TEXT NOT NULL
  first_seen_source_sha TEXT NOT NULL     -- SHA-256 of the source bytes
  last_seen_source_sha  TEXT NOT NULL
  period_start    TEXT, period_end TEXT   -- the declared period of the last source it was seen in (may be NULL)
  detail_counts   TEXT NOT NULL           -- JSON of counts only (e.g. {"rows": 2}); never values
  status          TEXT NOT NULL           -- acik | incelendi | kabul_edildi | cozuldu_kaynakta | yeniden_acildi
  reviewer        TEXT                    -- authenticated account id (after CP1), never free text
  created_at, updated_at TEXT NOT NULL    -- UTC ISO timestamps

dq_issue_event                            -- APPEND-ONLY history (never updated or deleted)
  event_id        INTEGER PRIMARY KEY
  issue_id        TEXT NOT NULL REFERENCES dq_issue(issue_id)
  generation_id   TEXT NOT NULL
  source_sha256   TEXT NOT NULL
  event           TEXT NOT NULL           -- detected | still_present | not_present | reviewed | acknowledged |
                                          -- note_added | reopened | source_missing
  actor           TEXT                    -- account id or "system"
  note            TEXT                    -- ≤ 500 chars, owner-entered; never shown in logs
  at              TEXT NOT NULL
```

### 3.1 Stable issue ID

`issue_id = "VKM-" + sha256(issue_type | source_key | locator)[:16]`. It deliberately does **not** include the
source SHA or the generation, so the same problem in next month's upload keeps its ID. The locator never contains a
raw value: a column letter, a header-slot number, or a salted hash of a row-grain key (the salt lives only in the
private store), so the ID is safe to show and to reference in a ticket.

### 3.2 Cross-month carryover

On every accepted generation the detector produces the current issue set and reconciles it with the store:

- new ID → `detected`, status `acik`;
- existing ID still present → `still_present`; a reviewed / acknowledged status is **kept** (the decision carries
  over) but the issue stays listed as present, and if its `detail_counts` changed materially (e.g. more rows) the
  status returns to `yeniden_acildi`;
- existing ID absent from a source that WAS uploaded → `not_present`, status `cozuldu_kaynakta` (resolved in the
  source, not by review). It reopens automatically if it returns;
- the whole source missing (e.g. **no Kredi file uploaded this month**) → `source_missing` for that source's issues
  plus one `missing_source` issue; the previous issues are **not** marked resolved — absence of a file is never
  evidence that its problems disappeared.

## 4. Persistence strategy

- Same private SQLite file as the rest of the app state (`AGENT_DB`, default `data/agent_state.db`, gitignored),
  created non-destructively like `crm_store.init_tables`. All SQL parameterized.
- `dq_issue_event` is append-only (enforced in code and by a test; no UPDATE/DELETE statements on it).
- Detection runs only on an **accepted** generation (after the manifest is built and the gate evaluated), inside the
  same publication flow, so the issue list always describes the generation the dashboard serves. A failed refresh
  writes nothing.
- Encrypted backup together with the rest of the state once hosting is provisioned (M8 plan).

## 5. Security requirements

- **Authorization (revised 2026-10-22, D-060 — CP1 simplified access):**
  - *Read-only issue visibility* MAY be `viewer` level (no login) for the privacy-safe issue list: type, severity,
    affected metric names, counts, status and dates — the same kind of information the withheld notices already show.
    Anything that could carry a private value or the owner's own text — **review notes**, row-level locators that a
    viewer could map back to a customer, source file names, fingerprints — is `owner_read`.
  - *Every decision* — review, acknowledge, note, status change, reopen — is `owner_write` in `services/access.py`:
    owner session + CSRF token + same-origin check. The reviewer is the authenticated owner session, never a
    request field.
  - The original rule "until CP1 exists the section is not exposed at all" is satisfied (CP1 exists). VKM-1 must
    register every new route in `services/access.py` and `services/accuracy.ROUTES` (both tests fail otherwise) and
    list any new viewer-visible field in the `docs/m8_cp1_access.md` §6 disclosure inventory.
  - Staff roles do not exist (D-060); there is one owner.
- CSRF protection and rate limits on writes (CP1 machinery); reviewer is taken from the session, never from the
  request body.
- No raw ERP values in issue IDs, titles, logs or error messages; notes are owner text, length-limited, HTML-escaped,
  never logged and never sent to an AI provider.
- Read-only towards sources: the detector receives the frozen bytes and evidence; it has no code path that writes to
  `data/` inputs.
- Every response carries the generation it describes; a review submitted against an older generation is rejected
  (stale-generation check, like request pinning).

## 6. Acceptance tests (for M8-VKM-1; synthetic data only)

1. Each issue type in §2 is produced by its deterministic check on a synthetic workbook and by nothing else.
2. Issue IDs are stable across two uploads of the same problem with different source bytes, and contain no raw value.
3. Reviewing / acknowledging an issue leaves every gate decision, evidence field and manifest ID byte-identical
   (property test over all register metrics); no metric becomes VERIFIED.
4. A suspected duplicate stays in the source, in the counts and in the issue list after review, acknowledgement and
   carryover; no code path deletes or merges it.
5. Source bytes and their SHA-256 are unchanged after detection, review and carryover.
6. Carryover: present → still_present (decision kept); grown → yeniden_acildi; absent from an uploaded source →
   cozuldu_kaynakta; reappears → reopened.
7. **Missing Kredi file:** a month without the Kredi upload produces `missing_source`, marks earlier Kredi issues
   `source_missing`, and resolves none of them.
8. **Authorization (D-060):** anonymous review / acknowledge / note / status requests are refused (401) and change
   nothing; owner-only issue detail (notes, locators) is refused to viewers; reviewer identity comes from the session;
   CSRF-less or cross-origin writes are refused; a review against a stale generation is refused; any viewer-level
   issue list contains no canary customer name, code, amount or note.
9. `dq_issue_event` is append-only (no UPDATE/DELETE), and history survives restart.
10. Privacy: logs, IDs and API errors contain no synthetic customer names, codes or amounts (canary strings).
11. Turkish UI strings for every type and status; the section states plainly that review is not approval
    ("İnceleme bir onay değildir; metrikler yalnızca doğrulanmış kanıtla gösterilir.").

## 7. Out of scope for M8-VKM-1

The full UI build in this PR; automated fixing; deletion or merging of any record; AI explanations of issues;
notifications; any change to Kredi `DURUM`, revenue definitions, DQ-1–DQ-6, the 120/320 rules or D-003.

## 8. Implementation notes (M8-VKM-1, D-062)

- **Code:** `services/vkm.py` (pure detection, check catalogue with fixed Turkish title / explanation / action),
  `services/vkm_store.py` (SQLite, carry-over, review), routes in `app.py` (`/api/vkm/*`), tab "Veri Kontrol" in
  `dashboard_builder.py`, hook `GenerationStore.add_observer` in `services/generation.py`.
- **Check → type:** every check in `vkm.CHECKS` names its §2 type and default severity; one recorded fact yields
  exactly one issue. Several checks may share a type (e.g. `footer_mismatch` and `field_excel_errors` are both
  `confirmed_error`). Joins are attributed to the source the unmatched record lives in (sales, aging, liste).
- **Schema refinements:** `dq_issue` also stores `check_code`, `field_name`, `column_letter`, `join_id`,
  `presence` (`present` / `absent` / `source_missing`), `detail_labels` (schema labels only) and first / last seen
  timestamps; `dq_issue_event` stores `status_after`, `detail_counts` and a `reason` code. Two further tables:
  `dq_observation` (append-only log of every observed upload per source) and `dq_source_state` (latest observation per
  source). Append-only and no-delete rules are SQLite triggers, so they hold for any code path.
- **Locator:** a schema label, a physical column letter (only where the owner-approved stock template fixes it, D-056)
  or a named check. No salted row-key hashes were needed: no issue is row-level, issues carry counts.
- **Reviewer:** the authenticated session's role (`owner`); there is one owner (D-060). Financial and public viewers
  cannot make any decision.
- **Stale check:** the token `vkm_generation` is a digest of the per-source observation state; any newly observed
  upload changes it, and a review or note presenting an older token is refused (409 `stale_generation`).
- **Owner review actions:** `incelendi`, `kabul_edildi`, back to `acik`, and notes. `cozuldu_kaynakta` and
  `yeniden_acildi` are set only by observation, never by a person.
- **Expected sources:** stock, sales, aging and Kredi always; Gider/Gelir and Bütçe once configured or observed before
  (they stay optional, D-047). The `liste` master is expected whenever the sales file is present.
- **Not detected (no deterministic signal in the current pipeline):** DQ-1 (cost columns are not inspected), DQ-2 (a
  list rule), the 120/320 supplier-account split. They stay in `docs/accuracy/open_verification_items.md`.
