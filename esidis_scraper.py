"""
ΕΣΗΔΗΣ CPV Scraper — GitHub Actions edition
Τρέχει αυτόματα κάθε 5 ώρες στους servers του GitHub.
Αποθηκεύει αποτελέσματα σε results.json (commit στο repo).
"""

import requests
from bs4 import BeautifulSoup
import json
import re
import time
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ─── CPV Codes ────────────────────────────────────────────────────────────────

CPV_CODES = [
    "31681500-8", "31158000-8", "34144900-7", "48422000-2", "32440000-9",
    "34410000-4", "34421000-7", "34422000-7", "34430000-0", "34432000-4",
    "48210000-3", "48421000-5", "48781000-6", "50000000-5", "50111100-7",
    "50111110-0", "50115100-5", "50115200-6", "51612000-5", "71311200-3",
    "71311210-6", "71311300-4", "71356300-1", "72212421-6", "72212781-7",
    "72262000-9", "72263000-6", "72265000-0", "72266000-7", "72416000-9",
    "73210000-7", "73220000-0", "73300000-5", "79311100-8", "79341000-6",
    "79341400-0", "79342200-5", "79342321-9", "79993100-2", "80533100-0",
]

# ─── URLs ΕΣΗΔΗΣ ──────────────────────────────────────────────────────────────
# Δημόσια αναζήτηση — δοκιμάζουμε διαδοχικά endpoints
SEARCH_ENDPOINTS = [
    "https://www.eprocurement.gov.gr/actSearch/faces/non_logged_in_search_tenders.jspx",
    "https://www.eprocurement.gov.gr/actSearch/faces/search_tenders.jspx",
]

RESULTS_FILE = Path("results.json")
LOG_FILE     = Path("scraper.log")
REQUEST_DELAY = 3   # δευτερόλεπτα μεταξύ requests (ευγένεια στον server)
MAX_PAGES     = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.eprocurement.gov.gr/",
}

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_existing() -> dict:
    if RESULTS_FILE.exists():
        try:
            with open(RESULTS_FILE, encoding="utf-8") as f:
                data = json.load(f)
                return {r["esidis_id"]: r for r in data.get("tenders", [])}
        except Exception:
            pass
    return {}


def save_results(tenders: dict) -> None:
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    active = {
        k: v for k, v in tenders.items()
        if not v.get("deadline") or v["deadline"] >= cutoff
    }
    payload = {
        "last_updated": datetime.now().isoformat(),
        "total": len(active),
        "tenders": sorted(active.values(), key=lambda x: x.get("deadline") or "9999-12-31"),
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info(f"Αποθηκεύτηκαν {len(active)} διαγωνισμοί → {RESULTS_FILE}")


def clean_budget(raw: str) -> str:
    if not raw:
        return "—"
    raw = re.sub(r"[€\s]", "", raw.strip())
    # Ελληνική μορφή: 1.234,56 → 1234.56
    if re.search(r"\d\.\d{3},\d{2}", raw):
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        val = float(re.sub(r"[^\d.]", "", raw))
        formatted = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{formatted} €"
    except ValueError:
        return raw or "—"


def parse_deadline(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    m = re.search(r"(\d{2})[/\-\.](\d{2})[/\-\.](\d{4})", raw)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).strftime("%Y-%m-%d")
        except ValueError:
            pass
    # ISO format
    m2 = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m2:
        return m2.group(0)
    return ""


# ─── Scraping ─────────────────────────────────────────────────────────────────

def detect_active_endpoint(session: requests.Session) -> str | None:
    """Βρίσκει ποιο endpoint του ΕΣΗΔΗΣ είναι ενεργό."""
    for url in SEARCH_ENDPOINTS:
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200 and len(r.text) > 500:
                log.info(f"Ενεργό endpoint: {url}")
                return url
        except requests.RequestException:
            continue
    return None


def parse_tenders_from_soup(soup: BeautifulSoup, cpv: str) -> list[dict]:
    """
    Εξάγει διαγωνισμούς από HTML.
    Δοκιμάζει πολλαπλές στρατηγικές parsing για ανθεκτικότητα σε layout αλλαγές.
    """
    results = []

    # Στρατηγική 1: πίνακας με συγκεκριμένο id/class
    table = (
        soup.find("table", id=re.compile(r"tender|search|result", re.I))
        or soup.find("table", class_=re.compile(r"tender|search|result|list", re.I))
    )

    # Στρατηγική 2: ο μεγαλύτερος πίνακας με > 3 στήλες
    if not table:
        tables = soup.find_all("table")
        for t in tables:
            headers = t.find_all("th")
            if len(headers) >= 3:
                table = t
                break

    if not table:
        return results

    rows = table.find_all("tr")
    header_row = rows[0] if rows else None

    # Ανίχνευση θέσης στηλών από headers
    col_map = {"id": 0, "title": 1, "auth": 2, "deadline": 3, "budget": 4}
    if header_row:
        headers_text = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]
        for i, h in enumerate(headers_text):
            if re.search(r"α/α|αριθμ|κωδ|esidis|id", h):
                col_map["id"] = i
            elif re.search(r"τίτλ|αντικείμ|περιγρ", h):
                col_map["title"] = i
            elif re.search(r"αναθέτ|φορέ|αρχή", h):
                col_map["auth"] = i
            elif re.search(r"καταληκτ|λήξ|ημερομ", h):
                col_map["deadline"] = i
            elif re.search(r"προϋπ|αξία|budget|τιμή", h):
                col_map["budget"] = i

    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        def cell_text(idx):
            return cells[idx].get_text(strip=True) if idx < len(cells) else ""

        esidis_id = cell_text(col_map["id"])
        if not re.search(r"\d{4,8}", esidis_id):
            continue

        # Εξαγωγή μόνο του αριθμού αν υπάρχει άλλο κείμενο
        id_match = re.search(r"\d{4,8}", esidis_id)
        if id_match:
            esidis_id = id_match.group(0)

        # URL
        link = row.find("a", href=True)
        url = ""
        if link:
            href = link["href"]
            url = href if href.startswith("http") else f"https://www.eprocurement.gov.gr{href}"

        tender = {
            "esidis_id":             esidis_id,
            "title":                 cell_text(col_map["title"]),
            "url":                   url,
            "contracting_authority": cell_text(col_map["auth"]),
            "deadline":              parse_deadline(cell_text(col_map["deadline"])),
            "deadline_raw":          cell_text(col_map["deadline"]),
            "budget":                clean_budget(cell_text(col_map["budget"])),
            "cpv_matched":           cpv,
            "scraped_at":            datetime.now().isoformat(),
        }
        results.append(tender)

    return results


def scrape_cpv(session: requests.Session, endpoint: str, cpv: str) -> list[dict]:
    results = []
    log.info(f"  CPV {cpv}...")

    for page in range(1, MAX_PAGES + 1):
        params = {
            "cpvCode":    cpv,
            "pageIndex":  page,
            "pageSize":   20,
            "tenderStatus": "ACTIVE",
            "lang":       "el",
        }
        try:
            resp = session.get(endpoint, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning(f"    Σφάλμα page {page}: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        page_results = parse_tenders_from_soup(soup, cpv)

        if not page_results:
            log.debug(f"    page {page}: 0 αποτελέσματα, τέλος")
            break

        results.extend(page_results)
        log.info(f"    page {page}: {len(page_results)} διαγωνισμοί")

        # Έλεγχος επόμενης σελίδας
        has_next = soup.find("a", string=re.compile(r"επόμεν|next|›|»|\d", re.I))
        if not has_next:
            break

        time.sleep(REQUEST_DELAY)

    return results


# ─── Main ─────────────────────────────────────────────────────────────────────

def run():
    log.info("=" * 60)
    log.info(f"Έναρξη — {datetime.now().strftime('%d/%m/%Y %H:%M UTC')}")

    session = requests.Session()
    session.headers.update(HEADERS)

    endpoint = detect_active_endpoint(session)
    if not endpoint:
        log.error("Κανένα endpoint ΕΣΗΔΗΣ δεν απάντησε. Τερματισμός.")
        sys.exit(1)

    existing = load_existing()
    new_count = 0

    for cpv in CPV_CODES:
        tenders = scrape_cpv(session, endpoint, cpv)
        for t in tenders:
            if t["esidis_id"] not in existing:
                existing[t["esidis_id"]] = t
                new_count += 1
            else:
                existing[t["esidis_id"]].update({
                    "deadline":   t["deadline"],
                    "budget":     t["budget"],
                    "scraped_at": t["scraped_at"],
                })
        time.sleep(REQUEST_DELAY)

    save_results(existing)
    log.info(f"Νέοι: {new_count} | Σύνολο: {len(existing)}")
    log.info("=" * 60)


if __name__ == "__main__":
    run()
