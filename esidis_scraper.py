"""
ΕΣΗΔΗΣ Portal Scraper (NEPPS-SEARCH) - SAFE MODE
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
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=el-GR")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)

def extract_rows(driver, cpv):
    results = []
    try:
        rows = driver.find_elements(By.TAG_NAME, "tr")
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) >= 11:
                esidis_id = cells[0].text.strip()
                if not esidis_id.isdigit():
                    continue
                
                title = cells[2].text.strip()
                budget = cells[3].text.strip()
                deadline_raw = cells[6].text.strip()
                matched_cpv = cells[7].text.strip()
                auth = cells[9].text.strip()
                
                clean_deadline = ""
                if deadline_raw and deadline_raw != "—":
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
                    "budget": f"{budget} €" if budget and budget != "—" else "—",
                    "cpv_matched": matched_cpv if matched_cpv else cpv,
                    "scraped_at": datetime.now().isoformat()
                })
    except Exception as e:
        log.error(f"Σφάλμα εξαγωγής πίνακα: {e}")
    return results

def run():
    log.info("=" * 60)
    existing = load_existing()
    new_count = 0
    
    driver = make_driver()
    # Ορίζουμε μέγιστο χρόνο αναμονής για να μην κρασάρει το script αν κολλήσει το ΕΣΗΔΗΣ
    driver.set_page_load_timeout(45) 
    
    try:
        for cpv in CPV_CODES:
            try: # <--- ΤΟ ΔΙΧΤΥ ΑΣΦΑΛΕΙΑΣ: Αν κάτι πάει στραβά σε αυτό το CPV, δεν κρασάρει το πρόγραμμα.
                log.info(f"Αναζήτηση στο ΕΣΗΔΗΣ για CPV: {cpv}")
                driver.get(SEARCH_URL)
                time.sleep(3)
                
                inputs = driver.find_elements(By.XPATH, "//tr[.//label[contains(text(), 'Κωδικός CPV')]]//input[@type='text']")
                cpv_input = next((inp for inp in inputs if inp.is_displayed()), None)
                if not cpv_input:
                    continue
                    
                cpv_input.clear()
                cpv_input.send_keys(cpv)
                
                buttons = driver.find_elements(By.XPATH, "//*[text()='Αναζήτηση']")
                search_btn = next((btn for btn in buttons if btn.is_displayed() and "title" not in btn.get_attribute("class").lower()), None)
                
                if search_btn:
                    driver.execute_script("arguments[0].click();", search_btn)
                else:
                    continue
                    
                time.sleep(6) # Αναμονή πίνακα
                
                page = 1
                while page <= 3: # Περιορίζουμε στις πρώτες 3 σελίδες ανά CPV για ταχύτητα
                    rows = extract_rows(driver, cpv)
                    if not rows:
                        break
                        
                    for t in rows:
                        if t["esidis_id"] not in existing:
                            existing[t["esidis_id"]] = t
                            new_count += 1
                        else:
                            existing[t["esidis_id"]].update({"deadline": t["deadline"], "budget": t["budget"], "scraped_at": t["scraped_at"]})
                    
                    next_links = driver.find_elements(By.XPATH, "//a[contains(text(), 'Επόμενη') or contains(text(), '›')]")
                    clicked = False
                    for link in next_links:
                        if link.is_displayed() and "dis" not in link.get_attribute("class").lower():
                            driver.execute_script("arguments[0].click();", link)
                            clicked = True
                            time.sleep(4)
                            break
                    if clicked:
                        page += 1
                    else:
                        break
                        
            except Exception as e:
                log.warning(f"Το CPV {cpv} προσπεράστηκε λόγω σφάλματος/καθυστέρησης του ΕΣΗΔΗΣ.")
                continue # Προχωράει ομαλά στο επόμενο CPV!
                
    finally:
        driver.quit()
        
    save_results(existing)
    log.info(f"Ολοκληρώθηκε με ασφάλεια. Νέοι ΕΣΗΔΗΣ: {new_count} | Σύνολο ΕΣΗΔΗΣ: {len(existing)}")
    log.info("=" * 60)

if __name__ == "__main__":
    run()
