"""
ΕΣΗΔΗΣ/ΚΗΜΔΗΣ API Scraper
Χρησιμοποιεί το δημόσιο API του ΚΗΜΔΗΣ για να ανακτά διαγωνισμούς άμεσα (JSON).
Με δυναμική/ασφαλή εξαγωγή πεδίων.
"""

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
import requests

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

API_BASE = "https://cerpp.eprocurement.gov.gr"
SEARCH_ENDPOINT = f"{API_BASE}/khmdhs-opendata/notice"

RESULTS_FILE = Path("results.json")
LOG_FILE     = Path("scraper.log")

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
    # Διατηρούμε αυτούς που δεν έχουν λήξει πάνω από 30 μέρες
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

# ─── Main Logic ───────────────────────────────────────────────────────────────

def run():
    log.info("=" * 60)
    log.info(f"Έναρξη API Scraper — {datetime.now().strftime('%d/%m/%Y %H:%M UTC')}")
    
    existing = load_existing()
    new_count = 0
    updated_count = 0
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    date_from = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    
    payload = {
        "cpvItems": CPV_CODES,
        "dateFrom": date_from
    }
    
    page = 0
    while True:
        log.info(f"Χτύπημα API (σελίδα {page})...")
        try:
            res = requests.post(f"{SEARCH_ENDPOINT}?page={page}", json=payload, headers=headers, timeout=30)
            res.raise_for_status()
            
            data = res.json()
            content = data.get("content", [])
            
            if not content:
                log.info("Δεν υπάρχουν άλλα αποτελέσματα.")
                break
                
            log.info(f"  Βρέθηκαν {len(content)} εγγραφές στη σελίδα {page}.")
            
            for item in content:
                # 1. ΑΔΑΜ / ID
                adam = item.get("referenceNumber") or item.get("sysCode") or item.get("noticeCode")
                if not adam:
                    continue
                
                # 2. Τίτλος
                title = item.get("title") or "—"
                
                # 3. Αναθέτουσα Αρχή (Safe extraction)
                org_val = item.get("organization") or item.get("organizations") or item.get("buyer") or {}
                if isinstance(org_val, list) and len(org_val) > 0:
                    org_val = org_val[0]
                
                if isinstance(org_val, dict):
                    org = org_val.get("value") or org_val.get("label") or org_val.get("name") or "—"
                elif isinstance(org_val, str):
                    org = org_val
                else:
                    org = "—"
                
                # 4. Ημερομηνία Λήξης (Safe extraction)
                deadline_raw = item.get("finalDateTo") or item.get("finalDateFrom") or item.get("endDate") or item.get("submissionDate") or ""
                deadline = str(deadline_raw).split(" ")[0] if deadline_raw else ""
                
                # 5. Προϋπολογισμός (Safe extraction)
                budget_num = item.get("amountWithoutVat") or item.get("totalCostFrom") or item.get("totalCostTo")
                
                if not budget_num:
                    budget_obj = item.get("budget") or item.get("totalAmount") or item.get("contractAmount") or {}
                    if isinstance(budget_obj, dict):
                        budget_num = budget_obj.get("amountWithoutVat") or budget_obj.get("value") or budget_obj.get("amount")
                
                if budget_num:
                    try:
                        budget_str = f"{float(budget_num):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
                    except ValueError:
                        budget_str = str(budget_num)
                else:
                    budget_str = "—"
                
                # 6. CPV (Safe extraction)
                item_cpvs = item.get("cpvItems") or item.get("cpvs") or []
                matched_cpv = "—"
                
                if isinstance(item_cpvs, dict):
                    item_cpvs = [item_cpvs]
                elif isinstance(item_cpvs, str):
                    item_cpvs = [item_cpvs]
                    
                for c in item_cpvs:
                    if isinstance(c, dict):
                        c_code = str(c.get("key") or c.get("code") or c.get("value") or "")
                    else:
                        c_code = str(c)
                        
                    clean_c = c_code.split('-')[0].strip()
                    for my_cpv in CPV_CODES:
                        if clean_c and clean_c in my_cpv:
                            matched_cpv = c_code
                            break
                    if matched_cpv != "—":
                        break
                
                # 7. Link για το PDF
                url = f"{API_BASE}/khmdhs-opendata/notice/attachment/{adam}"
                
                tender_obj = {
                    "esidis_id": adam,
                    "title": title,
                    "url": url,
                    "contracting_authority": org,
                    "deadline": deadline,
                    "deadline_raw": deadline_raw,
                    "budget": budget_str,
                    "cpv_matched": matched_cpv,
                    "scraped_at": datetime.now().isoformat()
                }
                
                if adam not in existing:
                    existing[adam] = tender_obj
                    new_count += 1
                else:
                    existing[adam].update({
                        "deadline": deadline,
                        "budget": budget_str,
                        "scraped_at": tender_obj["scraped_at"],
                    })
                    updated_count += 1
            
            if data.get("last") is True:
                break
                
            page += 1
            
        except requests.exceptions.RequestException as e:
            log.error(f"Αποτυχία κλήσης API: {e}")
            break
            
    log.info(f"Ολοκληρώθηκε. Νέοι: {new_count} | Ενημερώθηκαν: {updated_count} | Σύνολο: {len(existing)}")
    save_results(existing)
    log.info("=" * 60)

if __name__ == "__main__":
    run()
