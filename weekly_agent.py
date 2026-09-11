#!/usr/bin/env python3
"""
Weekly Stock + Sales Agent
--------------------------
Runs once a week, the first time the Mac is on over the weekend, and emails one
dashboard with:
  - Phase 1: stock items running low (reorder) and overstocked (push to sell)
  - Phase 2: lapsed high-value customers to win back, in three tiers

Phase 3 (cross-sell: 5 products -> 5 companies) is stubbed at the bottom.
"""

import os
import sys
import glob
import sqlite3
import smtplib
import datetime as dt
from email.message import EmailMessage

import pandas as pd
import numpy as np

# ======================================================================
# CONFIG  —  edit these, nothing else needs changing
# ======================================================================

DATA_FOLDER       = os.path.expanduser("~/StockAgent/data")
OUTPUT_FOLDER     = os.path.expanduser("~/StockAgent/dashboards")
DB_PATH           = os.path.expanduser("~/StockAgent/agent_history.db")

STOCK_FILE_PREFIX = "stok"          # stock export file name starts with this

# Email (Gmail App Password — your normal password will not work)
EMAIL_FROM         = "youraddress@gmail.com"
EMAIL_TO           = "youraddress@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# --- Phase 1: stock alert settings ---
TOP_N_LOW         = 5
TOP_N_OVERSTOCK   = 5

# --- Phase 2: lapsed-customer settings ---
TOP_N_LAPSED        = 5
YEAR_SPEND_THRESHOLD = 10000        # a year "counts" if the customer spent > this
MIN_TOTAL_SPEND      = 30000        # minimum total historical spend to qualify
QUAL_YEARS           = [2022, 2023, 2024, 2025]   # years we can measure (file starts 2022)
LAPSED_YEAR          = 2026         # "lapsed" = no purchase in this year
TIER3_RECENT_YEARS   = [2024, 2025] # 1-year customers only count if their year is one of these

NO_REPEAT_DAYS    = 90              # don't repeat any item/customer for this long

# Column names
COL = {  # stock sheet (header on Excel row 4)
    "code": "Stok_Kodu", "name": "Stok_Adı", "stock": "TOPLAM STOK MİKTAR ",
    "coverage": "STOK Kontrol %", "min": "minseviye", "max": "maxseviye",
    "inv_value": "Envanter değeri", "status": "DURUM",
}
SALES = {  # customer sales report
    "cust": "Musterikod", "cust_name": "Musteri_ismi",
    "date": "Tarih", "amount": "Nettutar",
}
STOCK_HEADER_ROW  = 3
DISCONTINUED_FLAG = "ÜRETİMDEN KALKTI"

# ======================================================================
# Run control: once per week, on the weekend, Monday catch-up if missed
# ======================================================================

def _db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS runs (iso_year INT, iso_week INT, run_at TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS shown (category TEXT, item_key TEXT, shown_date TEXT)")
    con.commit()
    return con

def already_ran_this_week(con, today):
    iy, iw, _ = today.isocalendar()
    return con.execute("SELECT 1 FROM runs WHERE iso_year=? AND iso_week=?", (iy, iw)).fetchone() is not None

def mark_ran(con, today):
    iy, iw, _ = today.isocalendar()
    con.execute("INSERT INTO runs VALUES (?,?,?)", (iy, iw, today.isoformat())); con.commit()

def should_run_today(con, today, force):
    if force: return True, "forced"
    if already_ran_this_week(con, today): return False, "already ran this week"
    if today.weekday() in (5, 6): return True, "weekend run"
    if today.weekday() == 0: return True, "Monday catch-up (weekend missed)"
    return False, "not the weekend yet"

def recently_shown(con, category, cutoff):
    rows = con.execute("SELECT item_key FROM shown WHERE category=? AND shown_date>=?",
                       (category, cutoff.isoformat())).fetchall()
    return {r[0] for r in rows}

def record_shown(con, category, keys, today):
    con.executemany("INSERT INTO shown VALUES (?,?,?)",
                    [(category, str(k), today.isoformat()) for k in keys]); con.commit()

# ======================================================================
# File discovery
# ======================================================================

def find_latest_file(folder, predicate, what):
    matches = [f for f in glob.glob(os.path.join(folder, "*.xlsx")) if predicate(os.path.basename(f))]
    if not matches:
        raise FileNotFoundError(f"No {what} file found in {folder}.")
    return max(matches, key=os.path.getmtime)

def is_stock(name): return name.lower().startswith(STOCK_FILE_PREFIX)
def is_sales(name):
    n = name.lower()
    return (not n.startswith("stok")) and any(k in n for k in ("sat", "müşter", "muster"))

# ======================================================================
# Phase 1 — stock
# ======================================================================

def load_stock(path):
    df = pd.read_excel(path, header=STOCK_HEADER_ROW)
    for key in ["stock", "coverage", "min", "max", "inv_value"]:
        df[COL[key]] = pd.to_numeric(df[COL[key]], errors="coerce")
    return df

def analyse_stock(df, con, today):
    cutoff = today - dt.timedelta(days=NO_REPEAT_DAYS)
    code, cov, mn, mx = COL["code"], COL["coverage"], COL["min"], COL["max"]
    stk, inv, status = COL["stock"], COL["inv_value"], COL["status"]
    band, hascov, instock = df[mx] > 0, df[cov].notna(), df[stk].fillna(0) != 0
    disc = df[status].astype(str).str.contains(DISCONTINUED_FLAG, na=False)

    low = df[band & hascov & (df[cov] < df[mn]) & ~disc].copy()
    low["_u"] = low[cov] / low[mn].replace(0, np.nan)
    low = low.sort_values("_u", ascending=True, na_position="first")

    over = df[band & hascov & (df[cov] > df[mx]) & instock].copy()
    over["_disc"] = disc[over.index]
    over = over.sort_values(inv, ascending=False)

    low_seen = recently_shown(con, "stock_low", cutoff)
    over_seen = recently_shown(con, "stock_over", cutoff)
    low_pick = low[~low[code].astype(str).isin(low_seen)].head(TOP_N_LOW)
    over_pick = over[~over[code].astype(str).isin(over_seen)].head(TOP_N_OVERSTOCK)
    return low_pick, over_pick, dict(total=len(df), low_total=len(low), over_total=len(over))

# ======================================================================
# Phase 2 — lapsed high-value customers (three tiers)
# ======================================================================

def load_sales(path):
    df = pd.read_excel(path)
    df["_yr"] = pd.to_datetime(df[SALES["date"]], errors="coerce").dt.year
    df[SALES["amount"]] = pd.to_numeric(df[SALES["amount"]], errors="coerce").fillna(0)
    return df

def analyse_lapsed(df, con, today):
    c, amt = SALES["cust"], SALES["amount"]
    piv = df.pivot_table(index=c, columns="_yr", values=amt, aggfunc="sum", fill_value=0)
    for y in QUAL_YEARS + [LAPSED_YEAR]:
        if y not in piv.columns: piv[y] = 0
    names = df.groupby(c)[SALES["cust_name"]].first()

    nqual = sum((piv[y] > YEAR_SPEND_THRESHOLD).astype(int) for y in QUAL_YEARS)
    total = piv[QUAL_YEARS].sum(axis=1)
    not_lapsed_year = piv[LAPSED_YEAR] <= 0
    base = not_lapsed_year & (total >= MIN_TOTAL_SPEND)

    def tier_of(code):
        n = nqual[code]
        if n >= 3: return 1
        if n == 2: return 2
        if n == 1:
            qs = [y for y in QUAL_YEARS if piv.loc[code, y] > YEAR_SPEND_THRESHOLD]
            if qs and qs[0] in TIER3_RECENT_YEARS: return 3
        return None

    rows = []
    for code in piv.index[base]:
        t = tier_of(code)
        if t is None: continue
        yrs_active = [y for y in QUAL_YEARS if piv.loc[code, y] > 0]
        rows.append(dict(code=code, name=names.get(code, ""), total=float(total[code]),
                         tier=t, nqual=int(nqual[code]),
                         last_year=int(max(yrs_active)) if yrs_active else None))
    cand = pd.DataFrame(rows)
    counts = dict(t1=0, t2=0, t3=0)
    if not cand.empty:
        vc = cand["tier"].value_counts()
        counts = dict(t1=int(vc.get(1, 0)), t2=int(vc.get(2, 0)), t3=int(vc.get(3, 0)))
        # tier first, then spend desc  ->  "tier 1 first, then 2, then 3"
        cand = cand.sort_values(["tier", "total"], ascending=[True, False])
        seen = recently_shown(con, "lapsed", today - dt.timedelta(days=NO_REPEAT_DAYS))
        cand = cand[~cand["code"].astype(str).isin(seen)]
    pick = cand.head(TOP_N_LAPSED) if not cand.empty else cand
    return pick, counts

# ======================================================================
# Dashboard
# ======================================================================

def _money(x):
    try: return "₺{:,.0f}".format(float(x))
    except (TypeError, ValueError): return "—"

def _num(x, d=1):
    try: return f"{float(x):,.{d}f}"
    except (TypeError, ValueError): return "—"

def build_dashboard(low, over, stats, lapsed, lapsed_counts, has_sales, today, stock_file, sales_file):
    code, name = COL["code"], COL["name"]
    cov, mn, stk, inv = COL["coverage"], COL["min"], COL["stock"], COL["inv_value"]

    def low_rows():
        if low.empty: return '<tr><td colspan="6" class="empty">No new low-stock items this week.</td></tr>'
        out = []
        for _, r in low.iterrows():
            crit = " critical" if (pd.notna(r[cov]) and r[cov] < 0) else ""
            out.append(f'<tr class="low{crit}"><td class="rule"></td><td class="mono code">{r[code]}</td>'
                       f'<td class="prod">{r[name]}</td><td class="mono num">{_num(r[stk],0)}</td>'
                       f'<td class="mono num">{_num(r[cov],2)}</td><td class="mono num min">{_num(r[mn],2)}</td></tr>')
        return "".join(out)

    def over_rows():
        if over.empty: return '<tr><td colspan="6" class="empty">No new overstock items this week.</td></tr>'
        out = []
        for _, r in over.iterrows():
            dc = ' <span class="tag">discontinued</span>' if r.get("_disc") else ""
            out.append(f'<tr class="over"><td class="rule"></td><td class="mono code">{r[code]}</td>'
                       f'<td class="prod">{r[name]}{dc}</td><td class="mono num">{_num(r[stk],0)}</td>'
                       f'<td class="mono num">{_num(r[cov],2)}</td><td class="mono num val">{_money(r[inv])}</td></tr>')
        return "".join(out)

    def lapsed_rows():
        if lapsed.empty: return '<tr><td colspan="6" class="empty">No new lapsed customers this week.</td></tr>'
        out = []
        for _, r in lapsed.iterrows():
            ly = r["last_year"] if r["last_year"] else "—"
            out.append(f'<tr class="lap"><td class="rule"></td>'
                       f'<td><span class="tier t{r["tier"]}">T{r["tier"]}</span></td>'
                       f'<td class="prod">{r["name"]}</td>'
                       f'<td class="mono num spend">{_money(r["total"])}</td>'
                       f'<td class="mono num">{ly}</td>'
                       f'<td class="mono num min">{r["nqual"]} yr</td></tr>')
        return "".join(out)

    over_value = _money(over[inv].sum()) if not over.empty else "₺0"
    lapsed_value = _money(lapsed["total"].sum()) if not lapsed.empty else "₺0"

    lapsed_section = ""
    if has_sales:
        lc = lapsed_counts
        lapsed_section = f"""
  <section>
    <div class="sec-head">
      <h2><span class="dot lap-dot"></span>Win back — lapsed customers</h2>
      <span class="sec-note">Top {TOP_N_LAPSED}, tier 1 first · {lc['t1']}/{lc['t2']}/{lc['t3']} across tiers 1/2/3</span>
    </div>
    <table>
      <thead><tr><th></th><th>Tier</th><th>Customer</th>
        <th class="num">Hist. spend</th><th class="num">Last yr</th><th class="min">Strong yrs</th></tr></thead>
      <tbody>{lapsed_rows()}</tbody>
    </table>
    <div class="total">Recoverable value shown: <b>{lapsed_value}</b></div>
  </section>"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weekly Report — {today:%d %b %Y}</title>
<style>
  :root {{ --ink:#16191d; --muted:#6b7480; --line:#e4e7ec; --paper:#fbfbf9;
    --low:#c2410c; --crit:#9f1239; --over:#0e7490; --gold:#92660a;
    --lap:#6d28d9; --lap-bg:#f5f1fe; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
    font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; font-size:15px; line-height:1.5; }}
  .wrap {{ max-width:880px; margin:0 auto; padding:32px 24px 60px; }}
  header {{ border-bottom:2px solid var(--ink); padding-bottom:16px; }}
  .eyebrow {{ font-size:12px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); font-weight:600; }}
  h1 {{ font-size:30px; font-weight:800; letter-spacing:-.02em; margin:6px 0 2px; }}
  .sub {{ color:var(--muted); font-size:13px; }}
  .stats {{ display:flex; gap:26px; margin:22px 0 34px; flex-wrap:wrap; }}
  .stat .n {{ font-size:24px; font-weight:800; font-variant-numeric:tabular-nums; }}
  .stat .l {{ font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }}
  section {{ margin-bottom:38px; }}
  .sec-head {{ display:flex; align-items:baseline; justify-content:space-between; margin-bottom:10px; gap:12px; }}
  h2 {{ font-size:17px; font-weight:700; margin:0; }}
  h2 .dot {{ display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:9px; }}
  .low-dot {{ background:var(--low); }} .over-dot {{ background:var(--over); }} .lap-dot {{ background:var(--lap); }}
  .sec-note {{ font-size:12px; color:var(--muted); text-align:right; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ text-align:left; font-size:11px; letter-spacing:.05em; text-transform:uppercase; color:var(--muted);
    font-weight:600; padding:0 10px 7px; border-bottom:1px solid var(--line); }}
  th.num, th.min, th.val {{ text-align:right; }}
  td {{ padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  .mono {{ font-family:"SF Mono",Menlo,Consolas,monospace; font-variant-numeric:tabular-nums; }}
  .code {{ font-size:13px; color:var(--muted); white-space:nowrap; }}
  .prod {{ font-weight:500; }}
  .num, .min, .val {{ text-align:right; white-space:nowrap; }}
  td.rule {{ width:3px; padding:0; }}
  tr.low td.rule {{ background:var(--low); }} tr.low.critical td.rule {{ background:var(--crit); }}
  tr.over td.rule {{ background:var(--over); }} tr.lap td.rule {{ background:var(--lap); }}
  tr.low.critical .num {{ color:var(--crit); font-weight:700; }}
  td.min {{ color:var(--muted); }} td.val {{ color:var(--gold); font-weight:600; }}
  td.spend {{ color:var(--lap); font-weight:700; }}
  .tier {{ font-family:"SF Mono",Menlo,monospace; font-size:11px; font-weight:700; padding:2px 7px;
    border-radius:4px; color:#fff; }}
  .tier.t1 {{ background:#6d28d9; }} .tier.t2 {{ background:#8b5cf6; }} .tier.t3 {{ background:#a78bfa; }}
  .tag {{ font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:var(--low);
    background:#fff4ed; padding:2px 6px; border-radius:3px; margin-left:6px; }}
  .empty {{ color:var(--muted); font-style:italic; text-align:center; padding:18px; }}
  .total {{ text-align:right; font-size:12px; color:var(--muted); margin-top:8px; }}
  .total b {{ font-size:14px; }}
  footer {{ border-top:1px solid var(--line); padding-top:14px; font-size:12px; color:var(--muted); }}
  @media print {{ body {{ background:#fff; }} }}
</style></head>
<body><div class="wrap">
  <header>
    <div class="eyebrow">Haftalık Rapor · Weekly Report</div>
    <h1>Weekly report — {today:%d %B %Y}</h1>
    <div class="sub">Stock: {stock_file}{' · Sales: ' + sales_file if has_sales else ''}</div>
  </header>

  <div class="stats">
    <div class="stat"><div class="n">{stats['low_total']:,}</div><div class="l">below min</div></div>
    <div class="stat"><div class="n">{stats['over_total']:,}</div><div class="l">above max</div></div>
    <div class="stat"><div class="n">{over_value}</div><div class="l">overstock value</div></div>
    {f'<div class="stat"><div class="n">{lapsed_counts["t1"]+lapsed_counts["t2"]+lapsed_counts["t3"]:,}</div><div class="l">lapsed accounts</div></div>' if has_sales else ''}
  </div>

  <section>
    <div class="sec-head"><h2><span class="dot low-dot"></span>Reorder — running low</h2>
      <span class="sec-note">Top {TOP_N_LOW} by urgency · coverage below minseviye</span></div>
    <table><thead><tr><th></th><th>Code</th><th>Product</th>
      <th class="num">Stock</th><th class="num">Coverage</th><th class="min">Min</th></tr></thead>
      <tbody>{low_rows()}</tbody></table>
  </section>

  <section>
    <div class="sec-head"><h2><span class="dot over-dot"></span>Overstock — push to sell</h2>
      <span class="sec-note">Top {TOP_N_OVERSTOCK} by inventory value · coverage above maxseviye</span></div>
    <table><thead><tr><th></th><th>Code</th><th>Product</th>
      <th class="num">Stock</th><th class="num">Coverage</th><th class="val">Inv. value</th></tr></thead>
      <tbody>{over_rows()}</tbody></table>
    <div class="total">Tied-up value shown: <b style="color:var(--gold)">{over_value}</b></div>
  </section>
{lapsed_section}
  <footer>
    Stock coverage is your <b>STOK Kontrol %</b> (low below <b>minseviye</b>, overstock above <b>maxseviye</b>;
    discontinued excluded from reorder). Lapsed customers spent over ₺{YEAR_SPEND_THRESHOLD:,.0f} in 1–4 years
    (2022–2025) with nothing in {LAPSED_YEAR}; tier 1 = 3+ strong years, tier 2 = 2, tier 3 = one recent year.
    Everything rotates — nothing repeats for {NO_REPEAT_DAYS} days.
  </footer>
</div></body></html>"""

# ======================================================================
# Email
# ======================================================================

def build_email_summary(low, over, lapsed, has_sales, today):
    code, name, cov = COL["code"], COL["name"], COL["coverage"]
    def mini(rows, color, fmt):
        if rows.empty: return '<p style="color:#888;margin:4px 0 14px;">Nothing new this week.</p>'
        items = "".join(f'<li style="margin:3px 0;"><span style="color:{color};">●</span> {fmt(r)}</li>'
                        for _, r in rows.iterrows())
        return f'<ul style="margin:4px 0 14px;padding-left:18px;">{items}</ul>'
    f_stock = lambda r: f'<b>{r[code]}</b> — {r[name]} <span style="color:#888;">(cov {_num(r[cov],2)})</span>'
    f_lap = lambda r: f'<b>T{r["tier"]}</b> {r["name"]} <span style="color:#888;">({_money(r["total"])})</span>'
    html = (f'<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;max-width:600px;">'
            f'<h2 style="margin:0 0 2px;">Weekly report</h2>'
            f'<p style="color:#888;margin:0 0 18px;">{today:%d %B %Y} · full dashboard attached</p>'
            f'<h3 style="color:#c2410c;margin:0 0 4px;">Reorder — running low</h3>{mini(low,"#c2410c",f_stock)}'
            f'<h3 style="color:#0e7490;margin:0 0 4px;">Overstock — push to sell</h3>{mini(over,"#0e7490",f_stock)}')
    if has_sales:
        html += f'<h3 style="color:#6d28d9;margin:0 0 4px;">Win back — lapsed customers</h3>{mini(lapsed,"#6d28d9",f_lap)}'
    html += (f'<p style="color:#888;font-size:12px;">Open the attached HTML for the full tables. '
             f'Nothing repeats for {NO_REPEAT_DAYS} days.</p></div>')
    return html

def send_email(html_body, attachment_path, today):
    if not GMAIL_APP_PASSWORD:
        print("  [email skipped] no GMAIL_APP_PASSWORD set — dashboard written only.")
        return False
    msg = EmailMessage()
    msg["Subject"] = f"Weekly report — {today:%d %b %Y}"
    msg["From"], msg["To"] = EMAIL_FROM, EMAIL_TO
    msg.set_content("Your client does not support HTML. The dashboard is attached.")
    msg.add_alternative(html_body, subtype="html")
    with open(attachment_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="text", subtype="html", filename=os.path.basename(attachment_path))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_FROM, GMAIL_APP_PASSWORD); smtp.send_message(msg)
    print(f"  [email sent] to {EMAIL_TO}")
    return True

# ======================================================================
# Main
# ======================================================================

def run(force=False, data_folder=None, output_folder=None, send=True):
    today = dt.date.today()
    con = _db()
    ok, reason = should_run_today(con, today, force)
    print(f"Run check: {reason}")
    if not ok: return

    data_folder = data_folder or DATA_FOLDER
    output_folder = output_folder or OUTPUT_FOLDER
    os.makedirs(output_folder, exist_ok=True)

    stock_src = find_latest_file(data_folder, is_stock, "stock")
    print(f"Stock: {stock_src}")
    low, over, stats = analyse_stock(load_stock(stock_src), con, today)
    print(f"  low {stats['low_total']} -> {len(low)} | overstock {stats['over_total']} -> {len(over)}")

    # Phase 2 — only if a sales file is present
    lapsed = pd.DataFrame(); lapsed_counts = dict(t1=0, t2=0, t3=0); has_sales = False
    sales_name = ""
    try:
        sales_src = find_latest_file(data_folder, is_sales, "sales")
        sales_name = os.path.basename(sales_src)
        print(f"Sales: {sales_src}")
        lapsed, lapsed_counts = analyse_lapsed(load_sales(sales_src), con, today)
        has_sales = True
        print(f"  lapsed {lapsed_counts} -> showing {len(lapsed)}")
    except FileNotFoundError:
        print("Sales: none found — skipping lapsed-customer section.")

    html = build_dashboard(low, over, stats, lapsed, lapsed_counts, has_sales, today,
                           os.path.basename(stock_src), sales_name)
    out_path = os.path.join(output_folder, f"weekly_report_{today:%Y-%m-%d}.html")
    with open(out_path, "w", encoding="utf-8") as f: f.write(html)
    print(f"  dashboard: {out_path}")

    if send:
        send_email(build_email_summary(low, over, lapsed, has_sales, today), out_path, today)

    record_shown(con, "stock_low", low[COL["code"]].tolist(), today)
    record_shown(con, "stock_over", over[COL["code"]].tolist(), today)
    if has_sales and not lapsed.empty:
        record_shown(con, "lapsed", lapsed["code"].tolist(), today)
    mark_ran(con, today)
    print("Done.")

# --- Phase 3 (cross-sell: 5 products -> 5 companies) — next
# def propose_cross_sell(...): ...

if __name__ == "__main__":
    run(force="--force" in sys.argv)
