# Fully-Automated Tender Scraper — No CAPTCHA, No Human

Scrapes **public "Latest Tenders" widgets** from Indian government portals
(ULBs, metros, ports, NHAI, Railways). These widgets live on the portal home
page, update every 15 minutes, and are NOT CAPTCHA-protected because they're
designed as a public ticker.

Runs anywhere Python runs — locally, GitHub Actions, cron, Render, Fly.io.

## What it captures

For each tender:
- Title
- Reference No (used as Tender ID)
- Closing Date (Bid Submission)
- Bid Opening Date
- Portal link
- State / portal source
- Matched transport+consultancy keywords

What it does NOT capture:
- Full description (that's on the detail page, which needs CAPTCHA)
- Pre-bid meeting date
- Document downloads

For most prospecting purposes, title + date + reference + link is enough.

## Run locally

```bash
pip install httpx beautifulsoup4 pandas lxml
python latest_tenders_widget_scraper.py
```

Output: `widget_tenders_<timestamp>.csv` in the configured OUTPUT_DIR.

## Run on GitHub Actions (fully automated)

1. Push this folder to a private GitHub repo.
2. The workflow at `.github/workflows/scrape-tenders.yml` runs:
   - every 6 hours automatically
   - on manual trigger from the Actions tab
3. CSVs are committed back to the repo under `output/` AND uploaded as
   workflow artifacts (downloadable for 30 days from the Actions tab).
4. Optional: uncomment the email step in the workflow and add two repo secrets
   (`MAIL_USER` = your Gmail, `MAIL_PASS` = a Gmail app password) to get the
   CSV emailed to you.

## Add more portals

Open `latest_tenders_widget_scraper.py` and add tuples to the `PORTALS` list:

```python
PORTALS = [
    ("Your Portal Name", "https://portal.example.gov.in/", "optional note"),
    ...
]
```

Any portal with a visible "Latest Tenders" widget on its home page works.
The script tries three HTML patterns automatically; if none catch a specific
portal, add that URL to a test run with just the one entry and share the
output.

## Why this is the right pattern

- **Legitimate**: these widgets exist to be read by the public. No T&C violation.
- **Fully automated**: no CAPTCHA, no human in the loop, cron-friendly.
- **Diversified**: 12 portals already configured. More can be added freely.
- **Resilient**: if one portal breaks (markup change), others keep working.

Combine this with:
- World Bank Procurement API (fully automated, built already)
- ADB tenders via Playwright headless (fully automated, built already)
- Your own registered bidder email alerts (set up once, read via Gmail API)

That gives you a complete, zero-CAPTCHA, cron-schedulable pipeline.

## When this ISN'T enough

For deeper detail (full description, documents, pre-bid dates) you still need
to open the specific tender page, which usually requires CAPTCHA. Workflow:

1. This script surfaces interesting titles daily.
2. You manually open the 2–3 relevant ones per day on the portal, solve the
   CAPTCHA once, and download the tender docs.

Trading 100% automation for 95% automation + 5 minutes of manual review is
the realistic trade-off, and it keeps you safely within every portal's T&C.
