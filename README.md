# NSE/BSE EBP Scraper — Cloud Run Service

Scrapes NSE EBP placement reporting and BSE bond issuances, filters to A-grade credit ratings, and writes the data into the weekly report Excel file.

Runs as a REST API on GCP Cloud Run (asia-south1). The local `weekly_report_fill.py` calls it automatically on each weekly run.

---

## Service URL

```
https://nse-bse-scraper-530275631634.asia-south1.run.app
```

---

## Endpoints

| Endpoint | What it does |
|----------|-------------|
| `GET /health` | Liveness check — returns `{"status": "ok"}` |
| `GET /nse-ebp` | Scrape NSE EBP issuances |
| `GET /bse-bonds` | Scrape BSE bond issuances |
| `GET /all` | Scrape both NSE + BSE together |

All data endpoints accept optional date filter query params:

```
/all?from=2026-04-01&to=2026-07-13
```

Response time: **1–2 minutes** (browser scrape runs on each request).

---

## Files

| File | Purpose |
|------|---------|
| `scraper.py` | Playwright-based scraper for NSE EBP and BSE bonds |
| `main.py` | FastAPI app — exposes scraper as REST endpoints |
| `excel_writer.py` | Fetches from Cloud Run API and writes to Excel workbook |
| `Dockerfile` | Container definition — Playwright + Xvfb on Linux |
| `requirements.txt` | Python package dependencies |
| `deploy.py` | One-shot deploy script using GCP APIs (alternative to console) |

---

## Data Sources

### NSE EBP
- **Page:** `https://www.nseindia.com/primary-market/ebp-placement-reporting`
- **API intercepted:** `https://www.nseindia.com/api/ebp?finYear=2026-2027`
- **Records:** ~135 for FY 2026-27
- **Key fields:** `eidBiddingDate`, `eidIssuerName`, `eidIsin`, `eidCreditRatings`, `eidYield`, `eidAmountRaised`, etc.

### BSE Bonds
- **Page:** `https://www.bseindia.com/markets/publicissues/bond_issuances`
- **API intercepted:** `https://api.bseindia.com/BseIndiaAPI/api/Pubissues_Bond_Issuances_EBP_Dis_New_ng/w`
- **Records:** ~244 for FY 2026-27
- **Key fields:** `ENTRY_DATE`, `COMPANY_NAME`, `ISIN`, `CREDIT_RATING`, `YIELD`, `AMOUNT_RAISED`, etc.

Both sources write into the same sheet: **`NSE EBP - Issuances-2026-2027-0`**

---

## Filters Applied

1. **Credit rating — A-grade only:** Only rows with AAA, AA+, AA, AA-, A+, A, or A- are written. Sub-A ratings (BBB, BB, unrated, etc.) are dropped.
2. **Deduplication by ISIN:** Rows whose ISIN already exists in the sheet are skipped.

---

## Why Playwright + Xvfb

NSE India detects and blocks standard headless Chromium. The scraper runs with `headless=False` (a real browser window) using **Xvfb** — a virtual display — so the browser has a screen to render into on the Linux Cloud Run container.

---

## Local Testing

Run the scraper directly (requires a display — works on Windows):

```
python scraper.py
```

Run the API server locally:

```
pip install -r requirements.txt
python main.py
```

Then test at `http://localhost:8080/health`.

---

## Integration with weekly_report_fill.py

Set the `SCRAPER_URL` environment variable before running the weekly report:

```powershell
# Temporary (current session only)
$env:SCRAPER_URL = "https://nse-bse-scraper-530275631634.asia-south1.run.app"

# Permanent (survives restarts)
[System.Environment]::SetEnvironmentVariable("SCRAPER_URL", "https://nse-bse-scraper-530275631634.asia-south1.run.app", "User")
```

`weekly_report_fill.py` will call `/all` automatically and write the results into the Excel sheet. If `SCRAPER_URL` is not set, the NSE/BSE update step is silently skipped.

---

## Redeployment

The service is connected to GitHub via Cloud Run's continuous deployment. Push changes to the repo and Cloud Run automatically rebuilds and redeploys.

**Rebuild time:**
- First build: ~10 minutes (pulls Playwright base image + installs Chromium)
- Subsequent builds: ~2–3 minutes (Docker layers cached)

Monitor progress in the Cloud Run console → **Revisions** tab.

---

## GCP Project

| Setting | Value |
|---------|-------|
| Project | `gphd-dev` |
| Region | `asia-south1` (Mumbai) |
| Service name | `nse-bse-scraper` |
| CPU | 2 vCPU |
| Memory | 2 GiB |
| Billing | Request-based |
| Authentication | Public (no auth required) |
