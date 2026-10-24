"""M8-CP1 (D-060, revised by D-061): who may call which route. One table, enforced by one middleware in app.py.

Levels
- ``viewer``      anyone who can reach the server may call it without logging in. Read-only: it never changes a human
                  decision (action status, CRM, fair campaigns, notes) and never triggers a refresh, a reload, an upload
                  or a paid AI call. The accuracy gate (services/accuracy.py) still withholds every unverified metric,
                  for viewers AND the owner alike.
- ``finance_read`` read-only financial views (Yönetim: Kredi / Gider-Gelir / Bütçe projection) for a financial-viewer
                  session OR the owner session. GET only; never any owner permission.
- ``owner_read``  read-only, but for the owner only (full Yönetim with system state, diagnostics, bulk data export).
- ``owner_write`` changes persistent state, starts work, spends money or administers the system: owner session +
                  CSRF token (+ same-origin Origin when sent).
- ``auth``        the login / session / logout endpoints themselves (handled inside the endpoint).

Fail closed: a route that is not listed is refused for everyone (HTTP 403), and a non-GET route is never treated as
``viewer`` even if it were listed as one. ``OWNER_QUERY`` lists query parameters that turn an otherwise viewer-safe
GET into an owner action (e.g. ``?retry=true`` restarts the Customer 360 build).

GET routes were checked one by one for side effects (D-060): ``/api/agent/actions`` and ``/api/sales-queue`` insert
the rows of the published deterministic queue into the action table (insert-only materialization of server data;
no status, note or decision is ever changed, and the client cannot choose what is inserted); ``/`` and the context
reads may finish the deterministic build of the already-selected sources (same as the startup warm-up). The owner
views' lazy workbook load is owner-only.
"""
from __future__ import annotations

VIEWER, FINANCE_READ, OWNER_READ, OWNER_WRITE, AUTH = "viewer", "finance_read", "owner_read", "owner_write", "auth"
LEVELS = (VIEWER, FINANCE_READ, OWNER_READ, OWNER_WRITE, AUTH)
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

ACCESS: dict[tuple[str, str], str] = {
    # --- the dashboard and its login page ---
    ("GET", "/"): VIEWER,                                        # only the published, gate-authorized bytes
    ("GET", "/giris"): VIEWER,                                   # fixed login page (no data)
    # --- owner session ---
    ("GET", "/api/auth/session"): AUTH,                          # anonymous → {authenticated: false}
    ("POST", "/api/auth/login"): AUTH,                           # owner password; throttled; HTTPS or loopback only
    ("POST", "/api/auth/finance-login"): AUTH,                   # financial-viewer password; same protections
    ("POST", "/api/auth/logout"): AUTH,                          # revokes this session (CSRF-checked when live)
    ("POST", "/api/auth/logout-all"): OWNER_WRITE,               # revokes every session (owner and financial)
    ("POST", "/api/auth/finance-sessions/revoke"): OWNER_WRITE,  # ends every financial-viewer session only
    # --- system / reference ---
    ("GET", "/api/health"): VIEWER,                              # context error text redacted for viewers
    ("GET", "/api/agent/categories"): VIEWER,
    ("GET", "/api/macro"): VIEWER,
    ("GET", "/api/ai/calls"): OWNER_READ,                        # AI usage / cost telemetry
    ("GET", "/api/accuracy/status"): OWNER_READ,                 # source file names, fingerprints, declared dates
    ("GET", "/api/context"): OWNER_READ,                         # bulk export of the whole context (no UI uses it)
    ("POST", "/api/context/refresh"): OWNER_WRITE,               # rebuilds data and replaces the dashboard
    # --- M5 action centre ---
    ("GET", "/api/agent/actions"): VIEWER,
    ("POST", "/api/agent/actions/enrich"): OWNER_WRITE,          # paid AI
    ("GET", "/api/agent/customer/{customer_code}"): VIEWER,
    ("POST", "/api/agent/customer/{customer_code}/analyze"): OWNER_WRITE,   # paid AI
    ("GET", "/api/agent/product/{product_code}"): VIEWER,
    ("POST", "/api/agent/product/{product_code}/analyze"): OWNER_WRITE,     # paid AI
    ("POST", "/api/agent/draft-message"): OWNER_WRITE,           # paid AI (draft only, nothing is sent)
    ("POST", "/api/scenario"): OWNER_WRITE,                      # POST; no UI; owner tool
    ("POST", "/api/actions/{action_id}/complete"): OWNER_WRITE,
    ("POST", "/api/actions/{action_id}/defer"): OWNER_WRITE,
    ("POST", "/api/actions/{action_id}/dismiss"): OWNER_WRITE,
    ("POST", "/api/actions/{action_id}/reopen"): OWNER_WRITE,
    # --- M7-1 Customer 360 / M7-2 Günlük Satış / M7-3 opportunities ---
    ("GET", "/api/customer360/status"): VIEWER,                  # ?retry=true is owner-only (OWNER_QUERY)
    ("GET", "/api/customer360/search"): VIEWER,
    ("GET", "/api/customer360/{customer_code}"): VIEWER,
    ("GET", "/api/sales-queue"): VIEWER,
    ("GET", "/api/opportunities/summary"): VIEWER,
    ("GET", "/api/opportunities/territories"): VIEWER,
    # --- M7-4 Fuar Merkezi ---
    ("GET", "/api/fairs/history"): VIEWER,
    ("GET", "/api/fairs/history/{event_id}"): VIEWER,
    ("GET", "/api/fairs/candidates"): VIEWER,
    ("GET", "/api/fairs/campaigns"): VIEWER,
    ("POST", "/api/fairs/campaigns"): OWNER_WRITE,
    ("GET", "/api/fairs/campaigns/{campaign_id}"): VIEWER,
    ("PUT", "/api/fairs/campaigns/{campaign_id}"): OWNER_WRITE,
    ("POST", "/api/fairs/campaigns/{campaign_id}/archive"): OWNER_WRITE,
    ("GET", "/api/fairs/campaigns/{campaign_id}/customers"): VIEWER,
    ("POST", "/api/fairs/campaigns/{campaign_id}/customers"): OWNER_WRITE,
    ("PUT", "/api/fairs/campaigns/{campaign_id}/customers"): OWNER_WRITE,
    # --- M7-5 CRM ---
    ("GET", "/api/crm/summary"): VIEWER,
    ("GET", "/api/crm/followups"): VIEWER,
    ("GET", "/api/crm/recent"): VIEWER,
    ("GET", "/api/crm/customers/{customer_code}"): VIEWER,
    ("POST", "/api/crm/customers/{customer_code}/interactions"): OWNER_WRITE,
    ("PUT", "/api/crm/customers/{customer_code}/followup"): OWNER_WRITE,
    # --- M7-6 Yönetim ---
    ("GET", "/api/finance/overview"): FINANCE_READ,              # D-061: Kredi / Gider-Gelir / Bütçe ONLY, source
                                                                 # diagnostics removed (services/owner_view.finance_*)
    ("GET", "/api/owner/overview"): OWNER_READ,                  # full Yönetim: + operations, system, issues, reload
    ("GET", "/api/owner/status"): OWNER_READ,                    # source file names, system state
    ("POST", "/api/owner/reload"): OWNER_WRITE,                  # re-reads the owner workbooks
    # --- M8-VKM-1 Veri Kontrol Merkezi (D-062) ---
    ("GET", "/api/vkm/summary"): VIEWER,                         # allow-listed counts only (schemas.VkmPublicSummary)
    ("GET", "/api/vkm/issues"): OWNER_READ,                      # locators, counts, observations, fingerprints
    ("GET", "/api/vkm/issues/{issue_id}"): OWNER_READ,           # + history and owner notes
    ("POST", "/api/vkm/issues/{issue_id}/review"): OWNER_WRITE,  # review decision (never certifies a metric)
    ("POST", "/api/vkm/issues/{issue_id}/notes"): OWNER_WRITE,   # append-only owner note
    ("POST", "/api/vkm/sync"): OWNER_WRITE,                      # re-observe the served generation (recovery)
}

# Query parameters that make a viewer GET an owner action (value considered set unless false-like).
OWNER_QUERY: dict[tuple[str, str], tuple[str, ...]] = {
    ("GET", "/api/customer360/status"): ("retry",),
}
_FALSE = {"", "0", "false", "no", "off"}


def level(method: str, template: str | None) -> str | None:
    """The access level of a matched route, or None when it is not classified (→ refused)."""
    lv = ACCESS.get((method, template))
    if lv in (VIEWER, FINANCE_READ) and method in MUTATING:
        return OWNER_WRITE                                   # fail closed: a read level can never mutate
    return lv


def owner_query_used(method: str, template: str | None, query_params) -> bool:
    for name in OWNER_QUERY.get((method, template), ()):
        for v in query_params.getlist(name):
            if str(v).strip().lower() not in _FALSE:
                return True
    return False
