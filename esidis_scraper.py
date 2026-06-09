"""
ΕΣΗΔΗΣ Portal Scraper (NEPPS-SEARCH) - PLAYWRIGHT EDITION
Παρακάμπτει τα Anti-Bot/Firewall συστήματα του ΕΣΗΔΗΣ.
"""

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

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

def load_existing():
    if RESULTS_FILE.exists():
        try:
            with open(RESULTS_FILE, encoding="utf-8") as f:
                data = json.load(f)
                return {str(r["esidis_id"]): r for r in data.get("tenders", []) if str(r.get("esidis_id", "")).isdigit()}
        except: pass
    return {}

def save_results(tenders):
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

def extract_table_data(page, cpv):
    results = []
    try:
        # Περιμένουμε μέχρι 10 δευτερόλεπτα να φορτώσει κάποιος πίνακας
        page.wait_for_selector("table tr", timeout=10000)
        rows = page.locator("table tr").all()
        log.info(f"      Σαρώνονται {len(rows)} γραμμές HTML...")
        
        valid_found = 0
        for row in rows:
            cells = row.locator("td").all_inner_texts()
            if len(cells) >= 11:
                esidis_id = cells[0].strip()
                if not esidis_id.isdigit():
                    continue
                    
                title = cells[2].strip()
                budget = cells[3].strip()
                deadline_raw = cells[6].strip()
                matched_cpv = cells[7].strip()
                auth = cells[9].strip()
                
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
                        
                results.append({
                    "esidis_id": esidis_id,
                    "title": title,
                    "url": f"https://nepps-search.eprocurement.gov.gr/actSearch/resources/search/{esidis_id}",
                    "contracting_authority": auth,
                    "deadline": clean_deadline,
                    "deadline_raw": deadline_raw,
                    "budget": f"{budget} €" if budget and budget not in ("—", "-") else "—",
                    "cpv_matched": matched_cpv if matched_cpv else cpv,
                    "scraped_at": datetime.now().isoformat()
                })
                valid_found += 1
                
        log.info(f"      Βρέθηκαν {valid_found} διαγωνισμοί.")
    except Exception as e:
        log.error(f"      Δεν βρέθηκε πίνακας ή υπήρξε σφάλμα (Πιθανώς 0 αποτελέσματα).")
    return results

def run():
    log.info("=" * 60)
    existing = load_existing()
    new_count = 0
    
    with sync_playwright() as p:
        # Το πιο βασικό βήμα: Μιμείται 100% αληθινό Chrome σε Windows (όχι Headless bot)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="el-GR",
            timezone_id="Europe/Athens"
        )
        page = context.new_page()
        
        try:
            log.info("Αρχική φόρτωση του ΕΣΗΔΗΣ...")
            # Περιμένουμε μέχρι να 'ηρεμήσει' το δίκτυο
            page.goto(SEARCH_URL, wait_until="networkidle")
            time.sleep(3)
            
            title = page.title()
            log.info(f"Τίτλος σελίδας: '{title}'")
            
            if "Αναζήτηση" not in title and "Εθνικό" not in title:
                log.warning("Η σελίδα δεν άνοιξε σωστά. Ίσως απαιτείται bypass του JSF redirect...")
                # Αν έχει κολλήσει στο script ανακατεύθυνσης του Oracle JSF, το προσπερνάμε
                page.evaluate("if(window.history && window.history.replaceState) { window.location.reload(); }")
                time.sleep(4)
                log.info(f"Νέος τίτλος μετά το refresh: '{page.title()}'")
            
            for cpv in CPV_CODES:
                try:
                    log.info(f"-> Ερευνούμε το CPV: {cpv}")
                    
                    # Καθαρίζουμε και βάζουμε το CPV
                    # Χρησιμοποιούμε text-based locator (πιο αξιόπιστο)
                    cpv_locator = page.locator("tr:has(label:has-text('Κωδικός CPV')) input[type='text']")
                    if cpv_locator.count() > 0:
                        cpv_locator.first.fill(cpv)
                    else:
                        log.error("      Δεν βρέθηκε το πεδίο CPV.")
                        continue
                        
                    # Πατάμε το κουμπί Αναζήτησης
                    search_btn = page.locator("a:has-text('Αναζήτηση'), button:has-text('Αναζήτηση')").first
                    if search_btn.count() > 0:
                        search_btn.click()
                        log.info("      Κλικ στην 'Αναζήτηση'. Αναμονή φόρτωσης...")
                        time.sleep(6) # Αναμονή του AJAX του ΕΣΗΔΗΣ
                    else:
                        log.error("      Δεν βρέθηκε το κουμπί 'Αναζήτηση'.")
                        continue
                    
                    # Εξαγωγή δεδομένων
                    rows = extract_table_data(page, cpv)
                    for t in rows:
                        if t["esidis_id"] not in existing:
                            existing[t["esidis_id"]] = t
                            new_count += 1
                        else:
                            existing[t["esidis_id"]].update({"deadline": t["deadline"], "budget": t["budget"], "scraped_at": t["scraped_at"]})
                            
                    # Κάνουμε "Καθαρισμό" της φόρμας για το επόμενο CPV
                    clear_btn = page.locator("a:has-text('Καθαρισμός'), button:has-text('Καθαρισμός')").first
                    if clear_btn.count() > 0:
                        clear_btn.click()
                        time.sleep(2)
                    else:
                        # Αν δεν βρει καθαρισμό, κάνει απλό refresh
                        page.goto(SEARCH_URL, wait_until="domcontentloaded")
                        time.sleep(2)
                        
                except Exception as e:
                    log.warning(f"      Σφάλμα στο CPV {cpv}: {e}")
                    page.goto(SEARCH_URL, wait_until="domcontentloaded")
                    time.sleep(2)
                    
        finally:
            browser.close()
            
    save_results(existing)
    log.info(f"Ολοκληρώθηκε. Νέοι ΕΣΗΔΗΣ: {new_count} | Σύνολο ΕΣΗΔΗΣ: {len(existing)}")
    log.info("=" * 60)

if __name__ == "__main__":
    run()
