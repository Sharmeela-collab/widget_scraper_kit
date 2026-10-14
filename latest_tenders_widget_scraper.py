"""
"Latest Tenders" widget scraper — fully automated, no CAPTCHA.
---------------------------------------------------------------

Many Indian/SAR government portals show a "Latest Tenders — updates every 15 min"
widget on their home page. This widget is a public marketing feed with NO CAPTCHA
on it — only the full search is gated. We scrape the widget directly.

What you get per tender:
  - Tender Title
  - Reference No (Tender ID)
  - Closing Date (Bid Submission Date)
  - Bid Opening Date
  - State / Portal source

What you WON'T get from the widget:
  - Full description
  - Organisation details beyond the portal
  - Pre-bid meeting dates
  - Document links (those usually need CAPTCHA)

That's fine — this gives you a running list of ACTIVE tenders you can scan
for transport+consultancy keywords in the title, then look up manually for
anything worth bidding.

GitHub Actions compatible: no Playwright, no browser, just httpx + BeautifulSoup.
Add this to .github/workflows/scrape.yml with a cron schedule.

Install:
    pip install httpx beautifulsoup4 pandas lxml
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

OUTPUT_DIR = Path(r"G:\Transport tender Tracker Claude\Transport Tender Extractor Output")
# OUTPUT_DIR = Path("./scraper_output")   # on Linux/GitHub Actions

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

TODAY = datetime.now().strftime("%Y-%m-%d")

OUTPUT_COLUMNS = [
    "Date Found", "State", "Organisation", "Tender ID", "Tender Title",
    "Tender Category", "Pre-bid Meeting Date", "Bid Submission Date",
    "Portal Link", "Matched Keywords", "Source",
]

# Transport + consultancy filter (shortened — adjust via TRANSPORT_TERMS below)
TRANSPORT_TERMS = [
    "transport", "transportation", "highway", "expressway", "road",
    "railway", "rail", "metro", "mrt", "brt", "bus",
    "port", "airport", "aviation", "maritime", "waterway",
    "logistics", "multimodal", "freight", "corridor", "bridge",
    "urban mobility", "public transport", "transit", "traffic",
    "mobility", "gati shakti", "bharatmala", "sagarmala",
    # Also include infrastructure-adjacent terms that could be transport
    "infrastructure",
]

CONSULTANCY_TERMS = [
    "transaction advisor", "transaction advisory", "consultan", "advisor",
    "advisory", "feasibility", "pre-feasibility", "dpr",
    "detailed project report", "capacity building", "capacity strengthening",
    "institutional", "technical assistance", "ppp", "public-private",
    "bot", "dbfot", "hybrid annuity", "concession",
    "business model", "business case", "business plan", "business strategy",
    "revenue model", "policy", "regulatory", "demand study",
    "financial model", "study", "assessment", "preparation", "survey",
    "design",  # Design consultancy is common in infra tenders
]


def is_relevant(text: str) -> tuple[bool, list[str]]:
    """Return (True, matched keywords) if text has transport + consultancy."""
    t = (text or "").lower()
    th = [k for k in TRANSPORT_TERMS if k in t]
    ch = [k for k in CONSULTANCY_TERMS if k in t]
    if th and ch:
        return True, list(dict.fromkeys(th[:3] + ch[:3]))
    return False, []


def is_still_open(deadline: str) -> bool:
    """Parse a date string, True if today-or-later or unparseable."""
    if not deadline:
        return True
    # Try common formats
    deadline_clean = re.sub(r"\s+", " ", deadline).strip()
    for fmt in ("%d-%b-%Y %I:%M %p", "%d-%b-%Y", "%Y-%m-%d",
                "%d/%m/%Y", "%d-%B-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            d = datetime.strptime(deadline_clean[:25], fmt).date()
            return d >= date.today()
        except (ValueError, TypeError):
            continue
    return True


# ---------------------------------------------------------------------------
# PORTALS — each has a "Latest Tenders" widget on the public home page.
# No CAPTCHA on this specific view.
# ---------------------------------------------------------------------------

PORTALS = [
    # Format: (State/Portal, home_url, notes)
    # Indian municipal/state portals with public "Latest Tenders" widgets:
    ("Nashik Municipal Corp", "https://nmc.gov.in/", "ULB that uses this widget"),
    ("Pune Municipal Corp", "https://www.pmc.gov.in/", "ULB"),
    ("Mumbai Municipal (BMC)", "https://portal.mcgm.gov.in/", "ULB"),
    ("Chennai Corporation", "https://chennaicorporation.gov.in/", "ULB"),
    # NHAI publishes a public active-tenders feed:
    ("NHAI", "https://nhai.gov.in/#/tenderDetails", "National Highways Authority"),
    # IRCTC & Indian Railways tender portal has public listings:
    ("Indian Railways IREPS", "https://www.ireps.gov.in/", "no CAPTCHA on homepage"),
    # Metros:
    ("DMRC", "https://www.delhimetrorail.com/tenders", "Delhi Metro"),
    ("CMRL", "https://chennaimetrorail.org/tenders/", "Chennai Metro"),
    ("BMRCL", "https://english.bmrc.co.in/Tenders", "Bangalore Metro"),
    ("MMRC", "https://www.mmrcl.com/en/tenders", "Mumbai Metro"),
    # Ports:
    ("Chennai Port", "https://www.chennaiport.gov.in/tenders", "Major port"),
    ("JNPT", "https://www.jnport.gov.in/tenders", "Jawaharlal Nehru Port"),
    # Add more as you discover them — any portal with a visible
    # "Latest Tenders" widget on its public home page can be added here.
]


# ---------------------------------------------------------------------------
# Generic widget parser
# ---------------------------------------------------------------------------
# The "Latest Tenders" widget on these portals typically has one of these patterns:
#
# Pattern A: An HTML table under a div/section labelled "Latest Tenders"
#            with columns Tender Title | Reference No | Closing Date | Opening Date
# Pattern B: An unordered list of <li> items, each with title + dates inline
# Pattern C: A "ticker" / marquee with each tender as a <li> or <div>
#
# We try all three.
# ---------------------------------------------------------------------------

DATE_PATTERN = re.compile(
    r"\d{1,2}[-/\s][A-Za-z]{3,9}[-/\s]\d{4}(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM)?)?"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/\d{4}",
    re.IGNORECASE,
)


def extract_from_widget(html: str, portal: str, base_url: str) -> list[dict]:
    """Extract tenders from a 'Latest Tenders' widget on an HTML page."""
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []
    seen_titles: set[str] = set()

    # Pattern A: Find any table whose header or caption mentions "Tender"
    tables = soup.find_all("table")
    for table in tables:
        header_text = ""
        thead = table.find("thead")
        if thead:
            header_text = thead.get_text(" ", strip=True).lower()
        else:
            first_row = table.find("tr")
            if first_row:
                header_text = first_row.get_text(" ", strip=True).lower()

        if "tender" not in header_text and "reference" not in header_text:
            continue

        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            cell_texts = [c.get_text(" ", strip=True) for c in cells]
            full = " | ".join(cell_texts)

            # Find a date in the row
            dates = DATE_PATTERN.findall(full)
            if not dates:
                continue

            # Title = first cell with meaningful text; link = first <a>
            title = ""
            link = ""
            for c in cells:
                a = c.find("a")
                if a and a.get_text(strip=True):
                    title = re.sub(r"\s+", " ", a.get_text(strip=True))
                    link = a.get("href", "")
                    break
            if not title:
                # Fall back to longest cell text
                title = max(cell_texts, key=len)
            # Clean "1.", "2." numbering prefixes
            title = re.sub(r"^\s*\d+\.\s*", "", title).strip()
            if not title or len(title) < 8 or title in seen_titles:
                continue
            seen_titles.add(title)

            # Reference No = cell that looks like an ID, not a date, not the title
            ref = ""
            for ct in cell_texts:
                if ct == title or DATE_PATTERN.search(ct):
                    continue
                if re.search(r"\d", ct) and len(ct) < 60:
                    ref = ct
                    break
            if not ref:
                ref = f"{portal[:4].upper()}-{title[:15].replace(' ', '')}"

            # Dates — first is usually closing, second is opening
            closing_date = dates[0] if dates else ""
            opening_date = dates[1] if len(dates) > 1 else ""

            if not is_still_open(closing_date):
                continue

            # Absolute link
            if link.startswith("http"):
                abs_link = link
            elif link.startswith("/"):
                m = re.match(r"(https?://[^/]+)", base_url)
                abs_link = (m.group(1) if m else base_url.rstrip("/")) + link
            elif link:
                abs_link = base_url.rstrip("/") + "/" + link
            else:
                abs_link = base_url

            # Relevance filter
            relevant, matched = is_relevant(title)
            if not relevant:
                continue

            results.append({
                "Date Found": TODAY,
                "State": portal,
                "Organisation": portal,
                "Tender ID": ref,
                "Tender Title": title,
                "Tender Category": "Tender",
                "Pre-bid Meeting Date": "Refer tender document",
                "Bid Submission Date": closing_date,
                "Portal Link": abs_link,
                "Matched Keywords": ", ".join(matched),
                "Source": f"{portal} (widget)",
            })

    # Pattern B / C: list items under a "Latest Tenders" header
    headers = soup.find_all(
        ["h1", "h2", "h3", "h4", "div"],
        string=re.compile(r"latest\s+tenders?", re.IGNORECASE),
    )
    for header in headers:
        container = header.find_next(["ul", "ol", "div"])
        if not container:
            continue
        items = container.find_all("li") or container.find_all("a", href=True)
        for item in items:
            text = re.sub(r"\s+", " ", item.get_text(" ", strip=True))
            if not text or len(text) < 10:
                continue

            dates = DATE_PATTERN.findall(text)
            # Title = text with dates stripped
            title = text
            for d in dates:
                title = title.replace(d, "")
            title = re.sub(r"^\s*\d+\.\s*", "", title).strip(" ,|:")
            title = re.sub(r"\s+", " ", title)
            if not title or len(title) < 8 or title in seen_titles:
                continue
            seen_titles.add(title)

            link_el = item if item.name == "a" else item.find("a")
            link = link_el.get("href", "") if link_el else ""
            if link.startswith("http"):
                abs_link = link
            elif link.startswith("/"):
                m = re.match(r"(https?://[^/]+)", base_url)
                abs_link = (m.group(1) if m else base_url.rstrip("/")) + link
            else:
                abs_link = base_url

            closing_date = dates[0] if dates else ""
            if closing_date and not is_still_open(closing_date):
                continue

            relevant, matched = is_relevant(title)
            if not relevant:
                continue

            results.append({
                "Date Found": TODAY,
                "State": portal,
                "Organisation": portal,
                "Tender ID": f"{portal[:4].upper()}-{title[:15].replace(' ', '')}",
                "Tender Title": title,
                "Tender Category": "Tender",
                "Pre-bid Meeting Date": "Refer tender document",
                "Bid Submission Date": closing_date,
                "Portal Link": abs_link,
                "Matched Keywords": ", ".join(matched),
                "Source": f"{portal} (widget-list)",
            })

    return results


async def scrape_portal(client: httpx.AsyncClient, portal: str, url: str) -> list[dict]:
    print(f"[{portal}] fetching {url}")
    try:
        r = await client.get(url, timeout=30, headers=HTTP_HEADERS,
                             follow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        print(f"[{portal}]   error: {e}")
        return []

    results = extract_from_widget(r.text, portal, url)
    print(f"[{portal}]   found {len(results)} transport-consultancy tenders")
    return results


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [scrape_portal(client, p, url) for p, url, _ in PORTALS]
        results_lists = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results_lists:
        if isinstance(r, list):
            all_rows.extend(r)

    if not all_rows:
        print("\nNo matching tenders found. Expand PORTALS list or loosen keywords.")
        return

    df = pd.DataFrame(all_rows, columns=OUTPUT_COLUMNS)
    df.drop_duplicates(subset=["Tender ID", "Tender Title"], inplace=True)
    df.sort_values(by=["Source"], inplace=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_file = OUTPUT_DIR / f"widget_tenders_{timestamp}.csv"
    df.to_csv(out_file, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 65)
    print(f"  Saved {len(df)} unique tenders → {out_file}")
    print("=" * 65)
    print("\nBreakdown:")
    print(df.groupby("Source").size().sort_values(ascending=False).to_string())


if __name__ == "__main__":
    asyncio.run(main())
