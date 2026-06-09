"""
ΕΣΗΔΗΣ CPV Scraper — Selenium edition
Χρησιμοποιεί headless Chrome για να αναζητά διαγωνισμούς ανά CPV.
Τρέχει αυτόματα στο GitHub Actions κάθε 5 ώρες.
"""

import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException
)

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

SEARCH_URL = (
    "https://www.eprocurement.gov.gr"
    "/actSearch/faces/non_logged_in_search_tenders.jspx"
)

RESULTS_FILE = Path("results.json")
LOG_FILE     = Path("scraper.log")
PAGE_TIMEOUT = 30    # δευτερόλεπτα αναμονής για φόρτωση σελίδας
BETWEEN_CPV  = 3     # δευτερόλεπτα μεταξύ CPV αναζητήσεων

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


# ─── Chrome setup ─────────────────────────────────────────────────────────────

def make_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=el-GR")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    # Χρησιμοποιεί το pre-installed Chrome στο GitHub Actions ubuntu-latest
    service = Service()
    return webdriver.Chrome(service=service, options=opts)


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
        "tenders": sorted(
            active.values(),
            key=lambda x: x.get("deadline") or "9999-12-31"
        ),
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info(f"Αποθηκεύτηκαν {len(active)} διαγωνισμοί → {RESULTS_FILE}")


def parse_deadline(raw: str) -> str:
    if not raw:
        return ""
    m = re.search(r"(\d{2})[/\-\.](\d{2})[/\-\.](\d{4})", raw.strip())
    if m:
        try:
            return datetime(
                int(m.group(3)), int(m.group(2)), int(m.group(1))
            ).strftime("%Y-%m-%d")
        except ValueError:
            pass
    m2 = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m2:
        return m2.group(0)
    return ""


def clean_budget(raw: str) -> str:
    if not raw or raw.strip() in ("", "—", "-"):
        return "—"
    raw = raw.strip()
    # Ελληνική μορφή: 1.234,56
    if re.search(r"\d\.\d{3},\d{2}", raw):
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        val = float(re.sub(r"[^\d.]", "", raw))
        fmt = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{fmt} €"
    except ValueError:
        return raw or "—"


# ─── Selenium scraping ────────────────────────────────────────────────────────

def wait_for_table(driver: webdriver.Chrome, timeout: int = PAGE_TIMEOUT):
    """Αναμένει να φορτώσει ο πίνακας αποτελεσμάτων."""
    wait = WebDriverWait(driver, timeout)
    # Δοκιμάζει διάφορους selectors που χρησιμοποιεί το ΕΣΗΔΗΣ
    selectors = [
        (By.CSS_SELECTOR, "table.rf-dt"),           # RichFaces datatable
        (By.CSS_SELECTOR, "table[id*='tenders']"),
        (By.CSS_SELECTOR, "table[id*='result']"),
        (By.CSS_SELECTOR, "table[id*='search']"),
        (By.XPATH, "//table[contains(@class,'rf-dt')]"),
        (By.XPATH, "//table[.//th[contains(text(),'Α/Α') or contains(text(),'Αριθμ')]]"),
    ]
    for by, selector in selectors:
        try:
            el = wait.until(EC.presence_of_element_located((by, selector)))
            return el
        except TimeoutException:
            continue
    return None


def find_cpv_input(driver: webdriver.Chrome):
    """Βρίσκει το πεδίο εισαγωγής CPV."""
    selectors = [
        (By.XPATH, "//input[contains(@id,'cpv') or contains(@name,'cpv')]"),
        (By.XPATH, "//input[contains(@id,'CPV') or contains(@name,'CPV')]"),
        (By.XPATH, "//label[contains(text(),'CPV')]/following::input[1]"),
        (By.XPATH, "//span[contains(text(),'CPV')]/following::input[1]"),
        (By.CSS_SELECTOR, "input[id*='cpv']"),
        (By.CSS_SELECTOR, "input[id*='CPV']"),
    ]
    for by, sel in selectors:
        try:
            el = driver.find_element(by, sel)
            if el.is_displayed():
                return el
        except NoSuchElementException:
            continue
    return None


def find_search_button(driver: webdriver.Chrome):
    """Βρίσκει το κουμπί αναζήτησης."""
    selectors = [
        (By.XPATH, "//input[@type='submit']"),
        (By.XPATH, "//button[@type='submit']"),
        (By.XPATH, "//input[contains(@value,'Αναζήτ')]"),
        (By.XPATH, "//button[contains(text(),'Αναζήτ')]"),
        (By.XPATH, "//a[contains(text(),'Αναζήτ')]"),
        (By.CSS_SELECTOR, "input[id*='search'], button[id*='search']"),
    ]
    for by, sel in selectors:
        try:
            el = driver.find_element(by, sel)
            if el.is_displayed():
                return el
        except NoSuchElementException:
            continue
    return None


def extract_rows(driver: webdriver.Chrome, cpv: str) -> list[dict]:
    """Εξάγει τις γραμμές αποτελεσμάτων από τον πίνακα."""
    results = []

    # Βρες όλους τους πίνακες και επέλεξε τον σωστό
    tables = driver.find_elements(By.TAG_NAME, "table")
    target_table = None

    for table in tables:
        try:
            headers = table.find_elements(By.TAG_NAME, "th")
            header_texts = [h.text.strip().lower() for h in headers]
            header_combined = " ".join(header_texts)
            # Ψάχνει για headers που σχετίζονται με διαγωνισμούς
            if any(kw in header_combined for kw in
                   ["α/α", "αριθμ", "αναθέτ", "καταληκτ", "προϋπ", "τίτλ"]):
                target_table = table
                break
        except StaleElementReferenceException:
            continue

    if not target_table:
        return results

    # Ανίχνευση θέσης στηλών
    col_map = {"id": 0, "title": 1, "auth": 2, "deadline": 3, "budget": 4}
    try:
        headers = target_table.find_elements(By.TAG_NAME, "th")
        for i, h in enumerate(headers):
            txt = h.text.strip().lower()
            if re.search(r"α/α|αριθμ|κωδ|esidis", txt):
                col_map["id"] = i
            elif re.search(r"τίτλ|αντικείμ|περιγρ|θέμα", txt):
                col_map["title"] = i
            elif re.search(r"αναθέτ|φορέ|αρχή", txt):
                col_map["auth"] = i
            elif re.search(r"καταληκτ|λήξ|ημερομ|deadline", txt):
                col_map["deadline"] = i
            elif re.search(r"προϋπ|αξία|budget|τιμή|ποσό", txt):
                col_map["budget"] = i
    except Exception:
        pass

    # Εξαγωγή δεδομένων από γραμμές
    try:
        rows = target_table.find_elements(By.XPATH, ".//tbody/tr")
    except Exception:
        rows = target_table.find_elements(By.TAG_NAME, "tr")[1:]

    for row in rows:
        try:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 3:
                continue

            def cell(idx):
                return cells[idx].text.strip() if idx < len(cells) else ""

            raw_id = cell(col_map["id"])
            id_match = re.search(r"\d{4,8}", raw_id)
            if not id_match:
                continue
            esidis_id = id_match.group(0)

            # URL από link
            url = ""
            try:
                link = cells[col_map["id"]].find_element(By.TAG_NAME, "a")
                href = link.get_attribute("href") or ""
                url = href if href.startswith("http") else (
                    f"https://www.eprocurement.gov.gr{href}" if href else ""
                )
            except NoSuchElementException:
                try:
                    link = cells[col_map["title"]].find_element(By.TAG_NAME, "a")
                    href = link.get_attribute("href") or ""
                    url = href if href.startswith("http") else (
                        f"https://www.eprocurement.gov.gr{href}" if href else ""
                    )
                except NoSuchElementException:
                    pass

            results.append({
                "esidis_id":             esidis_id,
                "title":                 cell(col_map["title"]),
                "url":                   url,
                "contracting_authority": cell(col_map["auth"]),
                "deadline":              parse_deadline(cell(col_map["deadline"])),
                "deadline_raw":          cell(col_map["deadline"]),
                "budget":                clean_budget(cell(col_map["budget"])),
                "cpv_matched":           cpv,
                "scraped_at":            datetime.now().isoformat(),
            })
        except StaleElementReferenceException:
            continue
        except Exception as e:
            log.debug(f"  Row parse error: {e}")
            continue

    return results


def has_next_page(driver: webdriver.Chrome) -> bool:
    """Ελέγχει αν υπάρχει επόμενη σελίδα."""
    try:
        next_links = driver.find_elements(
            By.XPATH,
            "//a[contains(@class,'rf-ds-btn-next') or "
            "contains(text(),'Επόμενη') or contains(text(),'›') or "
            "contains(@title,'Next') or contains(@title,'Επόμενη')]"
        )
        for link in next_links:
            if link.is_displayed() and link.is_enabled():
                parent_class = link.get_attribute("class") or ""
                # Αν το κουμπί είναι disabled δεν υπάρχει επόμενη
                if "dis" not in parent_class.lower():
                    return True
    except Exception:
        pass
    return False


def click_next_page(driver: webdriver.Chrome) -> bool:
    """Κλικ στην επόμενη σελίδα."""
    try:
        next_link = driver.find_element(
            By.XPATH,
            "//a[contains(@class,'rf-ds-btn-next') and "
            "not(contains(@class,'dis'))]"
        )
        driver.execute_script("arguments[0].click();", next_link)
        time.sleep(2)
        return True
    except Exception:
        return False


def scrape_cpv(driver: webdriver.Chrome, cpv: str) -> list[dict]:
    """Scrape όλες τις σελίδες για ένα CPV."""
    results = []
    log.info(f"  CPV {cpv}...")

    try:
        # Φόρτωση σελίδας αναζήτησης
        driver.get(SEARCH_URL)
        wait = WebDriverWait(driver, PAGE_TIMEOUT)

        # Αναμονή για φόρτωση φόρμας
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "form")))
        time.sleep(2)

        # Εύρεση πεδίου CPV
        cpv_input = find_cpv_input(driver)
        if not cpv_input:
            log.warning(f"    Δεν βρέθηκε πεδίο CPV για {cpv}")
            # Debug: αποθήκευση HTML για ανάλυση
            with open(f"debug_{cpv.replace('-','')}.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source[:5000])
            return results

        # Εισαγωγή CPV
        cpv_input.clear()
        cpv_input.send_keys(cpv)
        time.sleep(0.5)

        # Εύρεση και κλικ κουμπιού αναζήτησης
        btn = find_search_button(driver)
        if btn:
            driver.execute_script("arguments[0].click();", btn)
        else:
            cpv_input.send_keys(Keys.RETURN)

        # Αναμονή για αποτελέσματα
        time.sleep(3)

        # Scrape σελίδες
        page = 1
        max_pages = 10
        while page <= max_pages:
            rows = extract_rows(driver, cpv)
            if not rows:
                if page == 1:
                    log.info(f"    0 αποτελέσματα")
                break

            results.extend(rows)
            log.info(f"    σελίδα {page}: {len(rows)} διαγωνισμοί")

            if not has_next_page(driver):
                break

            if not click_next_page(driver):
                break

            page += 1
            time.sleep(2)

    except TimeoutException:
        log.warning(f"    Timeout για CPV {cpv}")
    except Exception as e:
        log.error(f"    Σφάλμα CPV {cpv}: {e}")

    return results


# ─── Main ─────────────────────────────────────────────────────────────────────

def run():
    log.info("=" * 60)
    log.info(f"Έναρξη — {datetime.now().strftime('%d/%m/%Y %H:%M UTC')}")
    log.info(f"CPV: {len(CPV_CODES)}")

    existing = load_existing()
    new_count = 0

    driver = make_driver()
    try:
        for cpv in CPV_CODES:
            tenders = scrape_cpv(driver, cpv)
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
            time.sleep(BETWEEN_CPV)
    finally:
        driver.quit()

    save_results(existing)
    log.info(f"Νέοι: {new_count} | Σύνολο: {len(existing)}")
    log.info("=" * 60)


if __name__ == "__main__":
    run()
