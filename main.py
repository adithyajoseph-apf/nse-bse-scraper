"""
main.py — FastAPI app that runs on GCP Cloud Run.

Endpoints:
  GET /health        — liveness check
  GET /nse-ebp       — scrape NSE EBP placement reporting
  GET /bse-bonds     — scrape BSE bond issuances
  GET /all           — scrape both and return combined JSON

The local weekly_report_fill.py calls these endpoints and writes
the returned data into the Excel sheet.
"""

import asyncio
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from playwright.sync_api import sync_playwright

from scraper import scrape_nse_ebp, scrape_bse_bonds, _make_browser_context

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Sync scrape helpers (run in thread executor) ───────────────────────────────

def _sync_nse(from_date, to_date):
    with sync_playwright() as pw:
        browser, ctx = _make_browser_context(pw)
        page = ctx.new_page()
        try:
            return scrape_nse_ebp(page, from_date, to_date)
        finally:
            page.close()
            ctx.close()
            browser.close()


def _sync_bse(from_date, to_date):
    with sync_playwright() as pw:
        browser, ctx = _make_browser_context(pw)
        page = ctx.new_page()
        try:
            return scrape_bse_bonds(page, from_date, to_date)
        finally:
            page.close()
            ctx.close()
            browser.close()


def _sync_all(from_date, to_date):
    with sync_playwright() as pw:
        browser, ctx = _make_browser_context(pw)
        try:
            page_nse = ctx.new_page()
            nse_data = scrape_nse_ebp(page_nse, from_date, to_date)
            page_nse.close()

            page_bse = ctx.new_page()
            bse_data = scrape_bse_bonds(page_bse, from_date, to_date)
            page_bse.close()
        finally:
            ctx.close()
            browser.close()
    return nse_data, bse_data


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="NSE/BSE Scraper", version="1.0.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/nse-ebp")
async def nse_ebp(
    from_date: str | None = Query(None, alias="from", description="YYYY-MM-DD"),
    to_date:   str | None = Query(None, alias="to",   description="YYYY-MM-DD"),
):
    try:
        loop    = asyncio.get_running_loop()
        records = await loop.run_in_executor(None, _sync_nse, from_date, to_date)
        log.info("/nse-ebp → %d records", len(records))
        return {"source": "NSE EBP", "count": len(records), "records": records}
    except Exception as exc:
        log.exception("NSE scrape failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/bse-bonds")
async def bse_bonds(
    from_date: str | None = Query(None, alias="from", description="YYYY-MM-DD"),
    to_date:   str | None = Query(None, alias="to",   description="YYYY-MM-DD"),
):
    try:
        loop    = asyncio.get_running_loop()
        records = await loop.run_in_executor(None, _sync_bse, from_date, to_date)
        log.info("/bse-bonds → %d records", len(records))
        return {"source": "BSE Bonds", "count": len(records), "records": records}
    except Exception as exc:
        log.exception("BSE scrape failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/all")
async def all_data(
    from_date: str | None = Query(None, alias="from", description="YYYY-MM-DD"),
    to_date:   str | None = Query(None, alias="to",   description="YYYY-MM-DD"),
):
    try:
        loop = asyncio.get_running_loop()
        nse_data, bse_data = await loop.run_in_executor(
            None, _sync_all, from_date, to_date)
        return {
            "nse": {"count": len(nse_data), "records": nse_data},
            "bse": {"count": len(bse_data), "records": bse_data},
        }
    except Exception as exc:
        log.exception("Combined scrape failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
