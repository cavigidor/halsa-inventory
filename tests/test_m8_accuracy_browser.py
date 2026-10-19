"""M8-ACCURACY: Playwright check of the PRODUCTION app (`app:app`, real gate) in real Chromium.

The dashboard is built by the controlled refresh under the production gate and served by the production app. The
page must show only neutral withheld notices where analyses are not VERIFIED, embed and render no withheld rows,
never request the withheld queue / Customer 360 / opportunity APIs, keep operational features (refresh, CRM, fair
campaigns, owner system status) reachable, and never call the AI provider. Synthetic data only.
"""
import contextlib
import datetime as dt
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from services import accuracy as ACC
from services import refresh as RF
from tests.fixtures import owner_synth as OS
from tests.fixtures import synthetic_erp as SE
from tests.test_m5_browser import ROOT, _free_port, _get, browser, open_page  # noqa: F401  (fixture re-export)

pytestmark = pytest.mark.production_gate


@contextlib.contextmanager
def production_server(tmp_path):
    data = SE.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]          # production gate (marker)
    od = Path(tmp_path / "owner")
    od.mkdir()
    owner = {"KREDI_PATH": str(OS.write_kredi(od, dt.date.today())), "GIDER_GELIR_PATH": str(OS.write_gider_gelir(od)),
             "BUTCE_PATH": str(OS.write_butce(od))}                    # configured owner sources (synthetic)
    port = _free_port()
    log = open(tmp_path / "server.log", "w")
    env = {**os.environ, "STOCKAGENT_NO_DOTENV": "1", "AI_PROVIDER": "mock", "OPENAI_API_KEY": "",
           "MANUAL_INFLATION_RATE": "0.40", "DATA_FOLDER": data, "DASHBOARD_PATH": dash,
           "AGENT_DB": str(tmp_path / "state.db"), **owner}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            if proc.poll() is not None:
                log.flush()
                pytest.fail("server exited early:\n" + open(tmp_path / "server.log").read()[-3000:])
            try:
                if _get(base + "/api/health").get("context_ready"):
                    break
            except Exception:
                pass
            if time.monotonic() > deadline:
                pytest.fail("server not ready within 60 s")
            time.sleep(0.2)
        yield {"base": base}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


WITHHELD_APIS = ("/api/agent/actions", "/api/sales-queue", "/api/opportunities/", "/api/context",
                 "/api/agent/customer/", "/api/agent/product/", "/api/agent/draft-message")


def test_production_dashboard_shows_only_notices_and_never_requests_withheld_data(browser, tmp_path):
    with production_server(tmp_path) as srv:
        page = open_page(browser)
        requested = []
        page.on("request", lambda r: requested.append(r.url))
        assert page.goto(srv["base"] + "/").status == 200
        body = page.inner_text("body")
        assert "Bu özet göstergeler henüz doğrulanmadığı için gösterilmiyor" in body
        assert "Geri kazanım dağılımı" not in body
        tabs = page.eval_on_selector_all(".tab", "els => els.map(e => e.dataset.t)")
        for t in [x for x in tabs if x in ACC.MODULE_METRIC]:            # static modules: notice, no table
            page.click(f'.tab[data-t="{t}"]')
            panel = page.locator(f"#{t}")
            assert panel.locator(".aw-box").count() == 1, t
            assert panel.locator("table").count() == 0, t
            assert ACC.WITHHELD_MESSAGE in panel.inner_text(), t
        for t in ("sq", "c360", "opp"):                                   # server panels replaced by a notice
            page.click(f'.tab[data-t="{t}"]')
            assert ACC.WITHHELD_MESSAGE in page.locator(f"#{t}").inner_text(), t
        page.click('.tab[data-t="ai"]')
        page.wait_for_selector("#ai .ai-refresh:not([hidden])")          # the operational refresh stays usable
        assert ACC.WITHHELD_MESSAGE in page.locator("#ai").inner_text()
        assert page.locator("#ai .aic").count() == 0
        page.click('.tab[data-t="own"]')
        page.wait_for_selector('#own [data-sec="system"]:not([hidden])')
        for sec in ("kredi", "gg", "butce"):                              # configured, loaded, and withheld
            page.wait_for_selector(f'#own [data-sec="{sec}"] .aw-box', timeout=60000)
            assert page.locator(f'#own [data-sec="{sec}"] table').count() == 0
        own = page.locator("#own").inner_text()
        assert "Doğrulanmamış analizler gösterilmiyor" in own and "DQ-6" in own
        page.click('.tab[data-t="crm"]')
        page.wait_for_selector('#crm .cr-tiles .cr-tile')
        assert page.locator('#crm .cr-tile[data-k="active_customers_with_structured_contact"]').count() == 0
        page.click('.tab[data-t="fair"]')
        page.wait_for_selector("#fair .fm-h-list .fm-err")
        assert ACC.WITHHELD_MESSAGE in page.locator("#fair .fm-h-list").inner_text()
        text = page.inner_text("body")
        assert "₺" not in text and "Sentetik T1" not in text and "900.00.0006" not in text
        page.wait_for_timeout(300)
        bad = [u for u in requested if any(p in u for p in WITHHELD_APIS)]
        assert not bad, bad
        assert page.errors == []
        assert _get(srv["base"] + "/api/ai/calls")["calls"] == []       # the AI provider was never called
