"""Deterministic evidence packets — the ONLY thing the AI model ever receives.

Python builds a small, task-specific packet from the already-computed agent
context (services/context.py -> services/entity.py). Every number in a packet is
a *fact* with a stable id (F001, F002, ...) and a display value that Python has
already formatted. The model may reference facts as [[FACT:F001]]; it never
receives Excel files, DataFrames, SQLite files, whole tables or raw histories.

Missing values are never turned into zero: a None value produces a `missing`
entry ("veri yok") instead of a fact.
"""
import datetime as dt
import json

import config as C

SCHEMA_VERSION = "1"

# Raw ERP column names that must never appear in a packet (data-minimization check).
FORBIDDEN_KEYS = {
    "Nettutar", "Musterikod", "Musteri_ismi", "Pro_kodu", "ProjeIsmi", "sto_kod", "Tarih",
    "Net_Tutar_Maliyet_Dusulmus", "miktar", "sip_evrakno_seri", "sip_evrakno_sira",
    "Envanter değeri", "Hesap kodu", "Hesap adı",
}
FORBIDDEN_SUBSTRINGS = (".xlsx", ".xls", ".db", ".sqlite", "agent_context.json", "/Users/", "/data/")

STANDARD_WARNINGS = [
    {"code": "missing_is_not_zero",
     "text": "Pakette olmayan veya 'veri yok' olarak işaretlenen değerler bilinmiyor demektir; sıfır kabul edilmez."},
    {"code": "margin_data_gaps",
     "text": "Kâr marjı bu analizin kapsamı dışındadır (maliyet verisi güvenilir değil): marj veya kârlılık "
             "hakkında yorum, risk ya da öneri yazma; tahmin etme."},
]


# ---------------- deterministic Turkish display formatting ----------------
def _group(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def fmt_money(x):
    if x is None:
        return None
    v = float(x)
    s = _group(int(round(abs(v))))
    return f"-₺{s}" if v < 0 else f"₺{s}"


def fmt_int(x):
    if x is None:
        return None
    v = int(round(float(x)))
    return ("-" if v < 0 else "") + _group(abs(v))


def fmt_pct(x, signed=True):
    """Value already in percent units (e.g. -71.0 -> '%-71,0')."""
    if x is None:
        return None
    v = float(x)
    body = f"{abs(v):.1f}".replace(".", ",")
    sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
    return f"%{sign}{body}"


def fmt_decimal(x, digits=2):
    if x is None:
        return None
    return f"{float(x):.{digits}f}".replace(".", ",")


NUMERIC_KINDS = {"money", "count", "percent", "decimal", "year"}

FORMATTERS = {"money": fmt_money, "count": fmt_int, "percent": fmt_pct,
              "decimal": fmt_decimal, "year": lambda v: str(int(v)),
              "text": lambda v: str(v), "buyer": lambda v: str(v),
              "bool": lambda v: "Evet" if v else "Hayır"}


# ---------------- packet ----------------
class EvidencePacket:
    """Holds the facts (server side) and produces the minimized payload (model side)."""

    def __init__(self, task, as_of=None, entity=None):
        self.task = task
        self.as_of = as_of
        self.entity = entity or {}
        self.facts = []                 # list of dicts
        self.by_id = {}
        self.classifications = []
        self.flags = []
        self.warnings = list(STANDARD_WARNINGS)
        self.missing = []
        self.items = []                 # task-specific groupings (e.g. actions)
        self.allowed_text = set()       # names / codes the model may repeat verbatim
        if self.entity.get("id"):
            self.allowed_text.add(str(self.entity["id"]))
        if self.entity.get("name"):
            self.allowed_text.add(str(self.entity["name"]))

    # -- builders --
    def fact(self, label, value, kind="text", meaning="", display=None):
        """Add a fact. None -> recorded as missing (never zero). Returns the fact id or None."""
        if value is None or (isinstance(value, str) and value.strip() in ("", "—", "nan", "None")):
            # "missing is not zero" concerns NUMBERS. Empty descriptive fields (sector, rep, …) are
            # simply omitted — listing them made the model recommend "completing the data" (Q-1).
            if kind in NUMERIC_KINDS:
                self.missing.append(label)
            return None
        if len(self.facts) >= C.MAX_EVIDENCE_FACTS:
            return None
        fid = f"F{len(self.facts) + 1:03d}"
        disp = display if display is not None else FORMATTERS.get(kind, str)(value)
        f = {"fact_id": fid, "label": label, "display_value": disp, "kind": kind, "meaning": meaning}
        self.facts.append(f)
        self.by_id[fid] = f
        # Names may be repeated verbatim; numeric-looking values never are (they must be
        # referenced via [[FACT:..]] so Python, not the model, writes the number).
        if kind == "buyer" or (kind == "text" and not any(ch.isdigit() for ch in str(disp))):
            self.allowed_text.add(str(disp))
        return fid

    # Python-authored labels the model may repeat verbatim (not numbers it invented).
    # Free text typed by the user (user_instruction) is NOT allowed: echoing it is not grounding.
    _UNTRUSTED_CLASSIFICATIONS = {"user_instruction"}

    def classify(self, name, value):
        self.classifications.append({"name": name, "value": value})
        if name not in self._UNTRUSTED_CLASSIFICATIONS:
            self.allow(name, value if isinstance(value, str) else None)

    def flag(self, name):
        if name not in self.flags:
            self.flags.append(name)
            self.allow(name)

    def warn(self, code, text):
        if all(w["code"] != code for w in self.warnings):
            self.warnings.append({"code": code, "text": text})

    def allow(self, *texts):
        for t in texts:
            if t:
                self.allowed_text.add(str(t))

    # -- derived --
    def allowed_years(self):
        import re
        blob = " ".join([f["display_value"] + " " + f["label"] for f in self.facts]
                        + [str(self.as_of or "")] + list(self.flags)
                        + [str(c["value"]) for c in self.classifications
                           if c["name"] not in self._UNTRUSTED_CLASSIFICATIONS])
        return set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", blob))   # also inside flag names

    def to_payload(self):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "task": self.task,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "as_of_data_date": self.as_of,
            "entity": self.entity,
            "facts": [{k: f[k] for k in ("fact_id", "label", "display_value", "meaning")} for f in self.facts],
            "classifications": self.classifications,
            "flags": self.flags,
            "missing": self.missing,
            "warnings": self.warnings,
        }
        if self.items:
            payload["items"] = self.items
        return payload


# ---------------- data-minimization validation ----------------
class PacketRejected(ValueError):
    pass


_ALLOWED_SCALARS = (str, int, float, bool, type(None))


def validate_payload(payload):
    """Reject anything that is not a small JSON tree of primitives (no DataFrames, files, paths)."""
    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str):
                    raise PacketRejected(f"non-string key at {path}")
                if k in FORBIDDEN_KEYS:
                    raise PacketRejected(f"raw ERP column '{k}' at {path}")
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            if len(node) > C.MAX_EVIDENCE_FACTS * 2:
                raise PacketRejected(f"list too long at {path}")
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, _ALLOWED_SCALARS):
            if isinstance(node, str) and any(s in node for s in FORBIDDEN_SUBSTRINGS):
                raise PacketRejected(f"file/path-like string at {path}")
        else:
            raise PacketRejected(f"disallowed type {type(node).__name__} at {path}")

    walk(payload, "$")
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > C.MAX_EVIDENCE_PACKET_BYTES:
        raise PacketRejected(f"packet too large ({size} bytes)")
    if len(payload.get("facts", [])) > C.MAX_EVIDENCE_FACTS:
        raise PacketRejected("too many facts")
    return size


# ---------------- task builders ----------------
def _common(p, ctx):
    if ctx is None:
        return
    cfg = ctx.get("config", {})
    if cfg.get("inflation_rate") is None:
        p.warn("inflation_unavailable",
               "Enflasyon oranı mevcut değil; reel büyüme hesaplanmadı ve tahmin edilmemelidir.")


def _split_fact_string(s):
    """'Vadesi geçen: ₺800,000' -> ('Vadesi geçen', '₺800,000'). Python-generated strings only."""
    s = str(s)
    if ": " in s:
        label, value = s.split(": ", 1)
        return label.strip(), value.strip()
    return "Bilgi", s.strip()


def actions_packet(actions, ctx=None):
    """Daily deterministic actions -> one packet; each action keeps its own fact ids."""
    p = EvidencePacket("daily_actions", as_of=(ctx or {}).get("as_of_data_date"))
    _common(p, ctx)
    for i, a in enumerate(actions, start=1):
        ref = f"A{i:02d}"
        fids = []
        for s in a.get("facts", []):
            label, value = _split_fact_string(s)
            fid = p.fact(label, value, kind="text", display=value,
                         meaning=f"{ref} için Python tarafından hesaplanmış değer")
            if fid:
                fids.append(fid)
        p.allow(a.get("title"), a.get("entity_id"), ref)
        p.items.append({"action_ref": ref, "category": a.get("category"), "title": a.get("title"),
                        "priority": a.get("priority"), "confidence": a.get("confidence"),
                        "fact_ids": fids})
    return p


def customer_packet(cust, ctx=None, memory=None):
    c = cust or {}
    p = EvidencePacket("customer_analysis", as_of=(ctx or {}).get("as_of_data_date"),
                       entity={"type": "customer", "id": c.get("customer_code"), "name": c.get("name")})
    _common(p, ctx)
    p.fact("Müşteri", c.get("name"), "text")
    p.fact("Temsilci", c.get("rep"), "text")
    p.fact("Sektör", c.get("sector"), "text")
    # win-back
    if c.get("winback_tier") is not None:
        tier = c.get("winback_tier")
        p.fact("Geri kazanım kademesi", f"T{tier}", "text",
               "T1=3+ güçlü yıl, T2=2, T3=1 (yalnız 2024/2025)")
        p.classify("winback_tier", f"T{tier}")
        p.flag("lapsed_no_2026_purchase")
        p.fact("Toplam geçmiş harcama", c.get("historical_spend"), "money")
        p.fact("Son aktif yıl", c.get("last_active_year"), "year")
    for y in (c.get("years") or [])[-6:]:
        if y.get("y") is None:
            continue
        p.fact(f"{y['y']} ciro", y.get("spend"), "money")
        p.fact(f"{y['y']} sipariş sayısı", y.get("orders"), "count")
    tops = [t for t in (c.get("top_products") or []) if t][:5]
    if tops:
        p.fact("En çok aldığı ürünler", ", ".join(tops), "text")
        p.allow(*tops)
    # momentum (nominal vs real)
    if c.get("sales_2025") is not None or c.get("sales_2026") is not None:
        p.fact("2025 (Oca–bugün) ciro", c.get("sales_2025"), "money")
        p.fact("2026 (Oca–bugün) ciro", c.get("sales_2026"), "money")
        p.fact("Nominal değişim", c.get("nominal_pct"), "percent")
        p.fact("Reel (enflasyondan arındırılmış) değişim", c.get("real_pct"), "percent")
    # receivables / risk (Python-computed aging)
    p.fact("Vadesi geçen bakiye", c.get("overdue"), "money")      # None -> missing, never zero
    if (c.get("overdue") or 0) > 0:
        p.flag("has_overdue")
    # Collections/risk fields exist only for customers in those lists. A field that does not APPLY
    # to this customer (key absent) is omitted; a field that applies but has no value (key present,
    # None) is reported as missing. (Q-1: lapsed customers were shown 4 bogus "missing" fields.)
    if "oldest_overdue" in c:
        p.fact("En eski borç dönemi", c.get("oldest_overdue"), "text")
    if "revenue_25_26" in c:
        p.fact("2025–2026 ciro", c.get("revenue_25_26"), "money")
    if "debt_to_revenue_pct" in c:
        p.fact("Borç / ciro", c.get("debt_to_revenue_pct"), "percent", display=(
            None if c.get("debt_to_revenue_pct") is None else fmt_pct(c.get("debt_to_revenue_pct"), signed=False)))
    if "collections_score" in c:
        p.fact("Tahsilat öncelik puanı (0–100)", c.get("collections_score"), "decimal",
               display=None if c.get("collections_score") is None else fmt_decimal(c.get("collections_score"), 1))
    active = c.get("still_active") if c.get("still_active") is not None else c.get("still_ordering_2026")
    if active is not None:
        p.fact("2026'da sipariş veriyor mu", bool(active), "bool")
    if c.get("closed"):
        p.classify("account_status", "KAPALI")
        p.flag("closed_no_opportunities")
        p.warn("kapali", "Müşteri KAPALI: fırsat/satış önerisi yapılmaz; yalnızca tahsilat ve risk.")
    for m in (memory or [])[:5]:
        p.fact(f"Geçmiş not ({str(m.get('ts', ''))[:10]}, {m.get('kind', '')})", m.get("detail"), "text",
               "Kullanıcının girdiği not; doğrulanmış finansal veri değildir")
    return p


def product_packet(prod, ctx=None, max_buyers=5):
    pr = prod or {}
    p = EvidencePacket("product_analysis", as_of=(ctx or {}).get("as_of_data_date"),
                       entity={"type": "product", "id": pr.get("product_code"), "name": pr.get("name")})
    _common(p, ctx)
    p.fact("Ürün", pr.get("name"), "text")
    status = pr.get("status")
    p.classify("stock_status", status or "unknown")
    p.fact("Stok miktarı", pr.get("stock"), "count")
    p.fact("Fazla stok (envanter) değeri", pr.get("inventory_value"), "money")
    p.fact("Kapsama", pr.get("coverage"), "decimal")
    p.fact("Min seviye", pr.get("min_level"), "decimal")
    p.fact("Geçmiş alıcı sayısı (2024–2026)", pr.get("historical_buyer_count"), "count")
    for i, b in enumerate((pr.get("ranked_buyers") or [])[:max_buyers], start=1):
        p.fact(f"Aday alıcı {i}", b.get("name"), "buyer", "Python puanlamasıyla sıralanmış aday alıcı")
        p.fact(f"Aday alıcı {i} — bu ürüne geçmiş harcama", b.get("spend"), "money")
        p.fact(f"Aday alıcı {i} — son alım yılı", b.get("last"), "year")
        if b.get("has_overdue"):
            p.fact(f"Aday alıcı {i} — vadesi geçen borcu var", True, "bool")
    return p


def draft_packet(message_type, cust, prod=None, instruction=None, ctx=None, memory=None):
    p = customer_packet(cust, ctx, memory)
    p.task = "message_draft"
    p.classify("message_type", message_type)
    if prod:
        p.fact("Önerilecek ürün", prod.get("name"), "text")
        p.allow(prod.get("name"), prod.get("product_code"))
        p.fact("Önerilecek ürün stok miktarı", prod.get("stock"), "count")
    if instruction:
        p.classify("user_instruction", str(instruction)[:300])
    p.warn("no_internal_analysis", "Müşteriye giden metinde içsel analiz, puan veya sıralama ifşa edilmez.")
    return p
