"""
ΚΗΜΔΗΣ Opendata API Scraper - On Demand Edition
Αναζήτηση δημοσιεύσεων της "προηγούμενης ημέρας" με εξαγωγή PDF.
"""

import json
import logging
import sys
import io
from datetime import datetime, timedelta
from pathlib import Path
import requests
import PyPDF2

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

def extract_pdf_text(adam):
    """Κατεβάζει το PDF από το ΚΗΜΔΗΣ και εξάγει τις πρώτες 3 σελίδες ως κείμενο."""
    url = f"{API_BASE}/khmdhs-opendata/notice/attachment/{adam}"
    try:
        res = requests.get(url, timeout=20)
        if res.ok and 'application/pdf' in res.headers.get('Content-Type', ''):
            pdf_file = io.BytesIO(res.content)
            reader = PyPDF2.PdfReader(pdf_file)
            text = ""
            for i in range(min(3, len(reader.pages))):
                page_text = reader.pages[i].extract_text()
                if page_text:
                    text += page_text + "\n"
            
            clean_text = text.strip()
            if not clean_text:
                return "Το PDF είναι σκαναρισμένη εικόνα. Αδυναμία εξαγωγής κειμένου."
            return clean_text
    except Exception as e:
        log.error(f"Σφάλμα ανάγνωσης PDF για {adam}: {e}")
    return "Αδυναμία λήψης αρχείου."

def run():
    log.info("=" * 60)
    # Υπολογισμός "χθεσινής" ημερομηνίας (δηλαδή της προηγούμενης μέρας από την εκτέλεση)
    yesterday = datetime.now() - timedelta(days=1)
    target_date = yesterday.strftime("%Y-%m-%d")
    
    log.info(f"ΕΚΚΙΝΗΣΗ ΚΗΜΔΗΣ API (On Demand) - Αναζήτηση δημοσιεύσεων για: {target_date}")
    
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    all_tenders = []
    
    for cpv in CPV_CODES:
        log.info(f"-> Ελέγχω CPV: {cpv}")
        payload = {
            "cpvItems": [cpv],
            "dateFrom": target_date,
            "dateTo": target_date
        }
        
        page = 0
        while True:
            try:
                res = requests.post(f"{SEARCH_ENDPOINT}?page={page}", json=payload, headers=headers, timeout=15)
                if not res.ok: break
                
                data = res.json()
                content = data.get("content", [])
                if not content: break
                
                for item in content:
                    adam = item.get("referenceNumber") or item.get("sysCode")
                    if not adam: continue
                    
                    # Αποφυγή διπλοτυπιών
                    if any(t['adam'] == adam for t in all_tenders):
                        continue
                    
                    ada = item.get("internetNo") or "—"
                    title = item.get("title") or "—"
                    
                    org_val = item.get("organization") or {}
                    if isinstance(org_val, dict):
                        org = org_val.get("value") or org_val.get("label") or "—"
                    else:
                        org = str(org_val)
                        
                    budget_num = item.get("amountWithoutVat") or 0
                    if budget_num:
                        try:
                            budget_str = f"{float(budget_num):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
                        except:
                            budget_str = str(budget_num)
                    else:
                        budget_str = "—"
                    
                    log.info(f"    Βρέθηκε: {adam} - Κατέβασμα κειμένου...")
                    pdf_text = extract_pdf_text(adam)
                    
                    all_tenders.append({
                        "adam": adam,
                        "ada": ada,
                        "title": title,
                        "contracting_authority": org,
                        "budget": budget_str,
                        "cpv_matched": cpv,
                        "pdf_url": f"{API_BASE}/khmdhs-opendata/notice/attachment/{adam}",
                        "pdf_text": pdf_text,
                        "published_date": target_date
                    })
                
                if data.get("last") is True: break
                page += 1
                
            except Exception as e:
                log.error(f"Σφάλμα: {e}")
                break

    # Αποθήκευση στο JSON (κάνει πάντα overwrite με τη νέα αναζήτηση)
    payload_to_save = {
        "date_searched": target_date,
        "last_updated": datetime.now().isoformat(),
        "total": len(all_tenders),
        "tenders": all_tenders
    }
    
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload_to_save, f, ensure_ascii=False, indent=2)
        
    log.info(f"Ολοκληρώθηκε! Βρέθηκαν {len(all_tenders)} δημοσιεύσεις.")
    log.info("=" * 60)

if __name__ == "__main__":
    run()
