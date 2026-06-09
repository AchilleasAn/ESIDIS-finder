"""
ΕΣΗΔΗΣ Portal Scraper (NEPPS-SEARCH) - TURBO & STABLE MODE
Αναγκάζει το JSF να απαντήσει και περιμένει αυστηρά τον πίνακα.
"""

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

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

SEARCH_URL = "https://nepps-search.eprocurement.gov.gr/actSearch/faces/active_search_main.jspx"
RESULTS_FILE = Path("results.json")
LOG_FILE = Path("scraper.log")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

def load_existing() -> dict:
    if RESULTS_FILE.exists():
        try:
            with open(RESULTS_FILE, encoding="utf-8") as f:
                data = json.load(f)
                valid = {}
                for r in data.get("tenders", []):
                    eid = str(r.get("esidis_id", ""))
                    if eid.isdigit():
                        valid[eid] = r
                return valid
        except Exception:
            pass
    return {}

def save_results(tenders: dict) -> None:
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    active = {k: v for k, v in tenders.items() if not v.get("deadline") or v["deadline"] >= cutoff}
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
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(60)
    return driver

def extract_rows(driver, cpv):
    results = []
    try:
        # Περιμένουμε μέχρι να φανεί έστω ένα TR που να μην είναι header
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.XPATH, "//table//tr[td]")))
        
        # Σαρώνουμε ΟΛΟΥΣ τους πίνακες για ασφάλεια
        rows = driver.find_elements(By.TAG_NAME, "tr")
        log.info(f"      Σαρώνονται {len(rows)} γραμμές HTML...")
        
        found_tenders = 0
        for row in rows:
            try:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 10:
                    esidis_id = cells[0].text.strip()
                    if not esidis_id.isdigit():
                        continue
                        
                    title = cells[2].text.strip()
                    budget = cells[3].text.strip()
                    deadline_raw = cells[6].text.strip()
                    matched_cpv = cells[7].text.strip()
                    auth = cells[9].text.strip()
                    
                    clean_deadline = ""
                    if deadline_raw and deadline_raw not in ("—", "-"):
                        try:
                            date_part = deadline_raw.split(" ")[0]
                            if "-" in date_part:
                                parts = date_part.split("-")
                                if len(parts) == 3:
                                    clean_deadline = f"{parts[2]}-{parts[1]}-{parts[0]}"
                        except:
                            clean_deadline = deadline_raw
                    
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
                        "deadline_raw": deadline_raw,
                        "budget": f"{budget} €" if budget and budget not in ("—", "-") else "—",
                        "cpv_matched": matched_cpv if matched_cpv else cpv,
                        "scraped_at": datetime.now().isoformat()
                    })
                    found_tenders += 1
            except StaleElementReferenceException:
                continue
        log.info(f"      Βρέθηκαν {found_tenders} έγκυροι διαγωνισμοί σε αυτή τη σελίδα.")
    except TimeoutException:
        log.info("      Δεν φορτώθηκε κανένας πίνακας (Πιθανώς 0 αποτελέσματα).")
    except Exception as e:
        log.error(f"      Σφάλμα εξαγωγής πίνακα: {e}")
    return results

def run():
    log.info("=" * 60)
    existing = load_existing()
    new_count = 0
    
    driver = make_driver()
    wait = WebDriverWait(driver, 20)
    
    try:
        # Φορτώνουμε τη σελίδα ΜΙΑ ΦΟΡΑ και κάνουμε τις αναζητήσεις (πολύ πιο γρήγορο)
        log.info("Αρχική φόρτωση του ΕΣΗΔΗΣ...")
        driver.get(SEARCH_URL)
        time.sleep(5)
        
        for cpv in CPV_CODES:
            try:
                log.info(f"CPV: {cpv}")
                
                # Βρίσκουμε το πεδίο
                cpv_input = wait.until(EC.element_to_be_clickable((By.XPATH, "//tr[.//label[contains(text(), 'Κωδικός CPV')]]//input[@type='text']")))
                cpv_input.clear()
                time.sleep(0.5)
                cpv_input.send_keys(cpv)
                
                # Βρίσκουμε το κουμπί
                search_btn = driver.find_element(By.XPATH, "//*[text()='Αναζήτηση' and not(contains(@title, 'Αναζήτηση'))]")
                driver.execute_script("arguments[0].scrollIntoView(true);", search_btn)
                time.sleep(1)
                
                # Κλικ μέσω JS για αποφυγή προβλημάτων επικάλυψης στοιχείων
                driver.execute_script("arguments[0].click();", search_btn)
                
                log.info("    Αναμονή δυναμικής φόρτωσης (JSF)...")
                # Περιμένουμε το "loading" εικονίδιο του ΕΣΗΔΗΣ να εξαφανιστεί αν υπάρχει
                time.sleep(4) 
                
                page = 1
                while page <= 2: # 2 σελίδες max για να μην αργεί αιώνια
                    rows = extract_rows(driver, cpv)
                    if not rows:
                        break
                        
                    for t in rows:
                        if t["esidis_id"] not in existing:
                            existing[t["esidis_id"]] = t
                            new_count += 1
                        else:
                            existing[t["esidis_id"]].update({"deadline": t["deadline"], "budget": t["budget"], "scraped_at": t["scraped_at"]})
                    
                    try:
                        next_link = driver.find_element(By.XPATH, "//a[contains(text(), 'Επόμενη') or contains(text(), '›')]")
                        if link.is_displayed() and "dis" not in next_link.get_attribute("class").lower():
                            log.info("      Πάμε στην επόμενη σελίδα...")
                            driver.execute_script("arguments[0].click();", next_link)
                            time.sleep(4)
                            page += 1
                        else:
                            break
                    except:
                        break
                        
            except Exception as e:
                log.warning(f"    Σφάλμα/Timeout στο CPV {cpv}. Πάμε στο επόμενο.")
                # Αν μπλοκάρει, κάνουμε refresh για να καθαρίσει η φόρμα για το επόμενο CPV
                driver.get(SEARCH_URL)
                time.sleep(3)
                
    finally:
        driver.quit()
        
    save_results(existing)
    log.info(f"Ολοκληρώθηκε με ασφάλεια. Νέοι ΕΣΗΔΗΣ: {new_count} | Σύνολο ΕΣΗΔΗΣ: {len(existing)}")
    log.info("=" * 60)

if __name__ == "__main__":
    run()
