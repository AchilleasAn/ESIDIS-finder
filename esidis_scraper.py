"""
ΚΗΜΔΗΣ Opendata API Scraper - Total Reset
Ζητάει απευθείας διαγωνισμούς με βάση τα CPV και την Καταληκτική Ημερομηνία Υποβολής.
"""

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
import requests

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

API_BASE = "https://cerpp.eprocurement.gov.gr"
SEARCH_ENDPOINT = f"{API_BASE}/khmdhs-opendata/notice"

RESULTS_FILE = Path("results.json")
LOG_FILE = Path("scraper.log")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

def run():
    log.info("=" * 60)
    log.info("ΕΚΚΙΝΗΣΗ ΚΗΜΔΗΣ API - TOTAL RESET")
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # Ορίζουμε το παράθυρο αναζήτησης: Από σήμερα έως και +7 ημέρες
    today = datetime.now()
    date_from_str = today.strftime("%Y-%m-%d")
    date_to_str = (today + timedelta(days=7)).strftime("%Y-%m-%d")
    
    log.info(f"Φίλτρο Καταληκτικής Ημερομηνίας: Από {date_from_str} έως {date_to_str}")
    
    payload = {
        "cpvItems": CPV_CODES,
        "finalDateFrom": date_from_str,
        "finalDateTo": date_to_str
    }
    
    all_tenders = {}
    page = 0
    
    while True:
        log.info(f"Χτύπημα στο KHMDS API (σελίδα {page})...")
        try:
            res = requests.post(f"{SEARCH_ENDPOINT}?page={page}", json=payload, headers=headers, timeout=30)
            res.raise_for_status()
            data = res.json()
            
            content = data.get("content", [])
            if not content:
                break
                
            log.info(f" -> Βρέθηκαν {len(content)} εγγραφές.")
            
            for item in content:
                adam = item.get("referenceNumber") or item.get("sysCode")
                if not adam:
                    continue
                
                title = item.get("title") or "—"
                
                # Οργανισμός (Αναθέτουσα Αρχή)
                org_val = item.get("organization") or {}
                if isinstance(org_val, dict):
                    org = org_val.get("value") or org_val.get("label") or "—"
                else:
                    org = str(org_val)
                    
                # Καταληκτική Ημερομηνία
                deadline_raw = item.get("finalDateTo") or item.get("finalDateFrom") or ""
                deadline = str(deadline_raw).split(" ")[0] if deadline_raw else ""
                
                # Προϋπολογισμός (Συνολική Αξία)
                budget_num = item.get("amountWithoutVat") or item.get("totalCostFrom") or 0
                if not budget_num:
                    budget_obj = item.get("budget") or item.get("totalAmount") or {}
                    if isinstance(budget_obj, dict):
                        budget_num = budget_obj.get("amountWithoutVat") or budget_obj.get("value") or 0
                        
                budget_str = "—"
                if budget_num:
                    try:
                        budget_str = f"{float(budget_num):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
                    except:
                        budget_str = str(budget_num)
                        
                # Match CPV
                item_cpvs = item.get("cpvs") or []
                matched_cpv = "—"
                for c in item_cpvs:
                    if isinstance(c, dict):
                        c_code = str(c.get("key") or c.get("code") or "")
                        clean_c = c_code.split('-')[0].strip()
                        for my_cpv in CPV_CODES:
                            if clean_c and clean_c in my_cpv:
                                matched_cpv = c_code
                                break
                    if matched_cpv != "—":
                        break
                        
                all_tenders[adam] = {
                    "esidis_id": adam, # Στο ΚΗΜΔΗΣ αυτό είναι ο ΑΔΑΜ
                    "title": title,
                    "url": f"https://cerpp.eprocurement.gov.gr/khmdhs-opendata/notice/attachment/{adam}", # Link στο PDF
                    "contracting_authority": org,
                    "deadline": deadline,
                    "deadline_raw": deadline_raw,
                    "budget": budget_str,
                    "cpv_matched": matched_cpv,
                    "scraped_at": datetime.now().isoformat()
                }
            
            if data.get("last") is True:
                break
            page += 1
            
        except Exception as e:
            log.error(f"Σφάλμα στο API: {e}")
            break
            
    # Αποθήκευση - Κάνουμε overwrite τα πάντα αφού πλέον τραβάμε 100% φρέσκα δεδομένα
    payload_to_save = {
        "last_updated": datetime.now().isoformat(),
        "total": len(all_tenders),
        "tenders": sorted(all_tenders.values(), key=lambda x: x.get("deadline") or "9999-12-31")
    }
    
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload_to_save, f, ensure_ascii=False, indent=2)
        
    log.info(f"Ολοκληρώθηκε! Αποθηκεύτηκαν {len(all_tenders)} διαγωνισμοί.")
    log.info("=" * 60)

if __name__ == "__main__":
    run()
