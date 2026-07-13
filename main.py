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

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from playwright.async_api import async_playwright, Browser, BrowserContext
import asyncio
import json

from scraper import scrape_nse_ebp, scrape_bse_bonds

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Browser lifecycle — one browser per Cloud Run instance ────────────────────

_browser: Browser | None = None
_playwright = None


async def get_context() -> BrowserContext:
    """
    Launch Chromium with anti-detection settings.
    NSE India blocks standard headless mode, so we use headless=False with
    Xvfb (virtual display) — the Dockerfile sets DISPLAY=:99 via Xvfb.
    """
    global _browser, _playwright
    if _browser is None or not _browser.is_connected():
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=False,   # NSE blocks headless — Xvfb provides virtual display
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        log.info("Chromium launched")
    ctx = await _browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )
    await ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return ctx


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
    """Scrape NSE EBP placement reporting. Optional ?from=YYYY-MM-DD&to=YYYY-MM-DD."""
    try:
        ctx  = await get_context()
        page = await ctx.new_page()
        loop    = asyncio.get_event_loop()
        records = await loop.run_in_executor(
            None, scrape_nse_ebp, page, from_date, to_date)
        await page.close()
        await ctx.close()
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
    """Scrape BSE bond issuances. Optional ?from=YYYY-MM-DD&to=YYYY-MM-DD."""
    try:
        ctx  = await get_context()
        page = await ctx.new_page()
        loop    = asyncio.get_event_loop()
        records = await loop.run_in_executor(
            None, scrape_bse_bonds, page, from_date, to_date)
        await page.close()
        await ctx.close()
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
    """Scrape both NSE and BSE in parallel. Optional ?from=YYYY-MM-DD&to=YYYY-MM-DD."""
    try:
        ctx_nse, ctx_bse = await asyncio.gather(get_context(), get_context())
        page_nse = await ctx_nse.new_page()
        page_bse = await ctx_bse.new_page()

        loop = asyncio.get_event_loop()
        nse_task = loop.run_in_executor(
            None, scrape_nse_ebp, page_nse, from_date, to_date)
        bse_task = loop.run_in_executor(
            None, scrape_bse_bonds, page_bse, from_date, to_date)
        nse_data, bse_data = await asyncio.gather(nse_task, bse_task)

        await asyncio.gather(page_nse.close(), page_bse.close(),
                             ctx_nse.close(), ctx_bse.close())
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
