"""
ΕΣΗΔΗΣ Portal Scraper (NEPPS-SEARCH)
Αναζητά με Selenium στη σελίδα των Ηλεκτρονικών Διαγωνισμών (ΕΣΗΔΗΣ)
ώστε να λαμβάνει τον πραγματικό Α/Α, Ημερομηνία Λήξης και Προϋπολογισμό.
"""

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

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

# Το σωστό URL από το screenshot σου
SEARCH_URL = "https://nepps-search.eprocurement.gov.gr/actSearch/faces/active_search_main.jspx"
RESULTS_FILE = Path("results.json")
LOG_FILE = Path("scraper.log")

# ─── Logging & Helpers ────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

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
        "tenders": sorted(active.values(), key=lambda x: x.get("deadline") or "9999-12-31")
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info(f"Αποθηκεύτηκαν {len(active)} διαγωνισμοί → {RESULTS_FILE}")

def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=el-GR")
    return webdriver.Chrome(options=opts)

# ─── DOM Scrapers ─────────────────────────────────────────────────────────────

def find_cpv_input(driver):
    selectors = [
        "//tr[.//label[contains(text(), 'Κωδικός CPV')]]//input[@type='text']",
        "//label[contains(text(), 'Κωδικός CPV')]/following::input[@type='text'][1]",
        "//input[contains(@title, 'CPV')]"
    ]
    for sel in selectors:
        try:
            el = driver.find_element(By.XPATH, sel)
            if el.is_displayed(): return el
        except: continue
    return None

def find_search_button(driver):
    selectors = [
        "//a[contains(text(), 'Αναζήτηση') and not(contains(text(), 'Κριτήρια'))]",
        "//button[contains(text(), 'Αναζήτηση')]",
        "//input[@value='Αναζήτηση']"
    ]
    for sel in selectors:
        try:
            el = driver.find_element(By.XPATH, sel)
            if el.is_displayed(): return el
        except: continue
    return None

def extract_rows(driver, cpv):
    results = []
    try:
        tables = driver.find_elements(By.XPATH, "//table[.//th[contains(., 'Α/Α Διαγωνιστικής')]]")
        if not tables: return results
        
        rows = tables[0].find_elements(By.XPATH, ".//tbody/tr")
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 10: continue
            
            # Ανάγνωση στηλών βάσει του πίνακα του ΕΣΗΔΗΣ
            esidis_id = cells[0].text.strip()
            title = cells[2].text.strip()
            budget = cells[3].text.strip()
            deadline = cells[6].text.strip()
            matched_cpv = cells[7].text.strip()
            auth = cells[9].text.strip()
            
            if not esidis_id or esidis_id == "—": continue
            
            # Μετατροπή ημερομηνίας σε μορφή YYYY-MM-DD για σωστή ταξινόμηση
            clean_deadline = ""
            if deadline and deadline != "—":
                try:
                    parts = deadline.split(" ")[0].split("-")
                    if len(parts) == 3:
                        clean_deadline = f"{parts[2]}-{parts[1]}-{parts[0]}"
                except:
                    clean_deadline = deadline
            
            # Εύρεση URL
            try:
                link = cells[0].find_element(By.TAG_NAME, "a")
                url = link.get_attribute("href")
            except:
                url = f"https://nepps-search.eprocurement.gov.gr/actSearch/resources/search/{esidis_id}"
                
            results.append({
                "esidis_id": esidis_id,
                "title": title,
                "url": url,
                "contracting_authority": auth,
                "deadline": clean_deadline,
                "deadline_raw": deadline,
                "budget": f"{budget} €" if budget else "—",
                "cpv_matched": matched_cpv if matched_cpv else cpv,
                "scraped_at": datetime.now().isoformat()
            })
    except Exception as e:
        log.error(f"Σφάλμα εξαγωγής πίνακα: {e}")
        
    return results

# ─── Main Logic ───────────────────────────────────────────────────────────────

def run():
    log.info("=" * 60)
    existing = load_existing()
    new_count = 0
    driver = make_driver()
    
    try:
        for cpv in CPV_CODES:
            log.info(f"Αναζήτηση στο ΕΣΗΔΗΣ για CPV: {cpv}")
            driver.get(SEARCH_URL)
            time.sleep(3) # Αναμονή να φορτώσει το JSF
            
            cpv_input = find_cpv_input(driver)
            if not cpv_input:
                log.error(f"Δεν βρέθηκε το πεδίο CPV για το {cpv}")
                continue
                
            cpv_input.clear()
            cpv_input.send_keys(cpv)
            time.sleep(1)
            
            search_btn = find_search_button(driver)
            if search_btn:
                driver.execute_script("arguments[0].click();", search_btn)
            else:
                log.error("Δεν βρέθηκε το κουμπί Αναζήτηση!")
                continue
                
            time.sleep(5) # Αναμονή για να επιστρέψει ο server τον πίνακα
            
            page = 1
            max_pages = 5
            while page <= max_pages:
                rows = extract_rows(driver, cpv)
                if not rows: break
                
                for t in rows:
                    if t["esidis_id"] not in existing:
                        existing[t["esidis_id"]] = t
                        new_count += 1
                    else:
                        existing[t["esidis_id"]].update({
                            "deadline": t["deadline"], 
                            "budget": t["budget"], 
                            "scraped_at": t["scraped_at"]
                        })
                
                # Έλεγχος για επόμενη σελίδα
                try:
                    next_link = driver.find_element(By.XPATH, "//a[contains(text(), 'Επόμενη') or contains(text(), '›')]")
                    if "dis" not in next_link.get_attribute("class").lower():
                        driver.execute_script("arguments[0].click();", next_link)
                        time.sleep(4)
                        page += 1
                    else:
                        break
                except:
                    break
                    
    finally:
        driver.quit()
        
    save_results(existing)
    log.info(f"Ολοκληρώθηκε. Νέοι: {new_count} | Σύνολο: {len(existing)}")
    log.info("=" * 60)

if __name__ == "__main__":
    run()
