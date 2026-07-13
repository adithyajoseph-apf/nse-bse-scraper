"""
scraper.py — NSE EBP + BSE Bond Issuances scraper using Playwright.

Strategy:
  1. Navigate to the page with a real browser (bypasses JS rendering).
  2. Intercept background API calls to capture JSON directly — faster and
     more structured than parsing HTML tables.
  3. If no API response is captured, fall back to reading the rendered HTML table.

Run standalone for testing:
  python scraper.py
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Page, sync_playwright

log = logging.getLogger(__name__)

NSE_URL = "https://www.nseindia.com/primary-market/ebp-placement-reporting"
BSE_URL = "https://www.bseindia.com/markets/publicissues/bond_issuances"

# How long to wait for page + API responses (ms)
NAV_TIMEOUT  = 90_000
IDLE_TIMEOUT = 10_000


# ── NSE EBP ──────────────────────────────────────────────────────────────────

def _extract_nse_from_response(body: bytes) -> list[dict]:
    """Try to parse a captured NSE API response body into a list of records."""
    try:
        data = json.loads(body)
    except Exception:
        return []

    # NSE wraps data differently depending on the endpoint
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "records", "placements", "issuances", "ebpData"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _fill_date_filter(page: Page, from_date: str | None, to_date: str | None,
                       from_selector: str, to_selector: str) -> None:
    """Fill date filter inputs on the page if selectors exist and dates are provided."""
    if not from_date and not to_date:
        return
    try:
        if from_date and page.locator(from_selector).count() > 0:
            page.fill(from_selector, from_date)
            log.info("Filled from-date: %s", from_date)
        if to_date and page.locator(to_selector).count() > 0:
            page.fill(to_selector, to_date)
            log.info("Filled to-date: %s", to_date)
        # Try clicking Search / Submit / Apply button
        for label in ("Search", "Submit", "Apply", "Go"):
            btn = page.locator(f"button:has-text('{label}'), input[value='{label}']")
            if btn.count() > 0:
                btn.first.click()
                log.info("Clicked '%s' button", label)
                page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT)
                break
    except Exception as exc:
        log.debug("Date filter fill failed (non-fatal): %s", exc)


def scrape_nse_ebp(page: Page,
                   from_date: str | None = None,
                   to_date: str | None = None) -> list[dict]:
    """
    Scrape NSE EBP placement reporting page.
    from_date / to_date: optional date strings "YYYY-MM-DD" for filtered scraping.
    Returns a list of dicts, one per issuance row.
    """
    captured: list[dict] = []

    def on_response(response):
        url = response.url
        # Only capture the main issuances endpoint: /api/ebp?finYear=...
        # Exclude helper lookups (ebp-finyears, ebp-issuernames, ebp-isin) — those return strings
        if ("nseindia.com/api/ebp" in url and
                "ebp-" not in url and
                "json" in response.headers.get("content-type", "")):
            try:
                records = _extract_nse_from_response(response.body())
                records = [r for r in records if isinstance(r, dict)]
                if records:
                    log.info("NSE: captured %d records from %s", len(records), url)
                    captured.extend(records)
            except Exception as exc:
                log.debug("NSE: could not parse response from %s: %s", url, exc)

    page.on("response", on_response)

    log.info("NSE: navigating to %s", NSE_URL)
    page.goto(NSE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)

    try:
        page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT)
    except Exception:
        pass

    # Try applying date filters if the page has date inputs
    _fill_date_filter(page, from_date, to_date,
                      from_selector="input[placeholder*='From'], input[id*='from'], input[name*='from']",
                      to_selector="input[placeholder*='To'], input[id*='to'], input[name*='to']")

    page.wait_for_timeout(3_000)

    if captured:
        log.info("NSE: %d total records", len(captured))
        return captured

    log.info("NSE: no API response captured — falling back to HTML table")
    return _parse_html_table(page, site="NSE")


# ── BSE Bond Issuances ────────────────────────────────────────────────────────

def _extract_bse_from_response(body: bytes) -> list[dict]:
    try:
        data = json.loads(body)
    except Exception:
        return []

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("Table", "Table1", "data", "records", "issuances", "bondData"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


BSE_API_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/"
    "Pubissues_Bond_Issuances_EBP_Dis_New_ng/w"
)


def scrape_bse_bonds(page: Page,
                     from_date: str | None = None,
                     to_date: str | None = None) -> list[dict]:
    """
    Scrape BSE Bond Issuances.
    The BSE API accepts FromDate / Todate query params directly — we navigate
    to the API URL with those params so Playwright carries the required cookies.
    from_date / to_date format: "YYYY-MM-DD"
    """
    # Convert YYYY-MM-DD → DD/MM/YYYY which BSE API expects
    def _bse_fmt(d: str | None) -> str:
        if not d:
            return ""
        try:
            import datetime as _dt
            return _dt.date.fromisoformat(d).strftime("%d/%m/%Y")
        except Exception:
            return d

    captured: list[dict] = []

    def on_response(response):
        url = response.url
        # Only capture the main issuances endpoint, not the company-name lookup
        if ("Pubissues_Bond_Issuances_EBP_Dis_New_ng" in url and
                "json" in response.headers.get("content-type", "")):
            try:
                records = _extract_bse_from_response(response.body())
                records = [r for r in records if isinstance(r, dict)]
                if records:
                    log.info("BSE: captured %d records from %s", len(records), url)
                    captured.extend(records)
            except Exception as exc:
                log.debug("BSE: could not parse response from %s: %s", url, exc)

    page.on("response", on_response)

    # First load the page to acquire session cookies
    log.info("BSE: loading page for cookies")
    page.goto(BSE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    try:
        page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT)
    except Exception:
        pass

    # Build current FY from today's date
    import datetime as _dt
    today = _dt.date.today()
    fin_year = today.year if today.month >= 4 else today.year - 1

    # Only navigate directly to the API if:
    # (a) date filters are requested (the page load uses no-date params), OR
    # (b) the page load didn't capture anything
    if from_date or to_date or not captured:
        params = (
            f"?Flag=&FinYear={fin_year}"
            f"&FromDate={_bse_fmt(from_date)}"
            f"&Todate={_bse_fmt(to_date)}"
            f"&ISSUE_NAME=&ISIN="
        )
        api_url = BSE_API_URL + params
        log.info("BSE: fetching API: %s", api_url)
        captured.clear()  # discard page-load records; use date-filtered ones
        page.goto(api_url, wait_until="networkidle", timeout=NAV_TIMEOUT)
        page.wait_for_timeout(2_000)

    if captured:
        log.info("BSE: %d total records", len(captured))
        return captured

    log.info("BSE: no API response captured — falling back to HTML table")
    return _parse_html_table(page, site="BSE")


# ── HTML table fallback ───────────────────────────────────────────────────────

def _parse_html_table(page: Page, site: str) -> list[dict]:
    """
    Generic HTML table extractor. Uses <th> cells as column headers,
    then maps each data row to a dict.
    """
    try:
        page.wait_for_selector("table tbody tr", timeout=20_000)
    except Exception:
        log.warning("%s: no table found within timeout", site)
        return []

    # Extract headers from <th> elements
    headers: list[str] = []
    th_cells = page.query_selector_all("table th")
    if th_cells:
        headers = [th.inner_text().strip() for th in th_cells if th.inner_text().strip()]

    # If no <th> found, treat first <tr> as the header row
    if not headers:
        first_row = page.query_selector("table tr")
        if first_row:
            cells = first_row.query_selector_all("td")
            headers = [c.inner_text().strip() for c in cells]

    if not headers:
        log.warning("%s: could not find table headers", site)
        return []

    log.info("%s: headers found: %s", site, headers)

    records: list[dict] = []
    data_rows = page.query_selector_all("table tbody tr")
    for row in data_rows:
        cells = row.query_selector_all("td")
        texts = [c.inner_text().strip() for c in cells]
        if not any(texts):
            continue
        records.append(dict(zip(headers, texts)))

    log.info("%s: HTML table fallback → %d rows", site, len(records))
    return records


# ── Debug runner ──────────────────────────────────────────────────────────────

def _debug_print(label: str, data: list) -> None:
    print(f"\n=== {label} ===")
    print(f"Records returned: {len(data)}")
    if not data:
        print("  (no data)")
        return
    first = data[0]
    if isinstance(first, dict):
        print(f"Keys ({len(first)}): {list(first.keys())}")
        print("First record:")
        print(json.dumps(first, indent=2, default=str))
    else:
        print(f"  WARNING: expected dict, got {type(first).__name__!r}: {first!r}")


def _make_browser_context(pw):
    """
    Launch Chromium with anti-detection settings.
    NSE India blocks standard headless mode — headless=False is required.
    For Cloud Run, use a virtual display (Xvfb) to run headless=False without a screen.
    """
    browser = pw.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, ctx


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    with sync_playwright() as pw:
        browser, ctx = _make_browser_context(pw)

        page = ctx.new_page()
        nse_data = scrape_nse_ebp(page)
        page.close()
        _debug_print("NSE EBP", nse_data)

        page = ctx.new_page()
        bse_data = scrape_bse_bonds(page)
        page.close()
        _debug_print("BSE Bonds", bse_data)

        browser.close()
        ctx.close()
