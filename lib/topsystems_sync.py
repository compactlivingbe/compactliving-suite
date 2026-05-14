"""
Top Systems (Victron) productlijst analyser.

Vergelijkt een Top Systems XML productlijst met Odoo:
  - Welke producten ontbreken nog in Odoo (geen supplierinfo met partner_id=690)?
  - Welke kostprijzen (supplierinfo.price) wijken af van pricenett (excl BTW)?
  - Welke verkoopprijzen (template.list_price) wijken af van pricegross/1.21?

Schrijft 3 CSV-rapporten naar reports/.

Optioneel: --apply om wijzigingen direct in Odoo door te voeren.

Gebruik:
  python analyze.py --xml "C:/path/Top.Systems Productlist 2026-05-12.xml"
  python analyze.py --xml ... --apply
  python analyze.py --xml ... --apply --apply-cost --apply-sale  # selectieve apply
"""
import argparse, csv, json, os, sys
from datetime import date
from pathlib import Path
import xml.etree.ElementTree as ET
import requests

PARTNER_ID_TOPSYSTEMS = 690
VICTRON_ROOT_CATEG = 154   # Odoo categ root "Victron"
# Compact Living conventie: list_price = pricegross direct (geen /1.21)
SALE_VAT_DIVISOR = 1.0
# Victron-only filter: codes met deze prefixes zijn Victron producten.
# Detecteerd uit bestaande Odoo Victron Top Systems items (38 unieke prefixes mei 2026).
VICTRON_PREFIXES = {
    "ADA","ARG","ASS","BAM","BAT","BBA","BCD","BMS","BPC","BPP","BPR","CCH","CEP",
    "CIN","CIP","CMP","COS","CTR","GSM","INTD","LYN","ORI","PCH","PIN","PMP","PPP",
    "QUA","RCD","REL","SCC","SDFI","SHP","SHU","SIN","SKY","SPM","SPP","VBS",
}

def is_victron_code(code):
    import re
    m = re.match(r"^([A-Z]+)", code or "")
    return bool(m) and m.group(1) in VICTRON_PREFIXES


def is_victron_product(code, description):
    """Strenger dan prefix alone:
    - Vv door X / Vervangen door X = obsolete cross-references -> NIET importeren
    - description noemt expliciet 'Victron' -> wel
    - prefix in VICTRON_PREFIXES én een echte beschrijving -> wel
    """
    desc = (description or "").strip()
    desc_low = desc.lower()
    # Skip obsolete cross-references
    if desc_low.startswith(("vv door", "vervangen door", "replaced by", "siehe ", "zie ")):
        return False
    if not is_victron_code(code):
        return False
    # If description mentions Victron explicitly, definitely yes
    if "victron" in desc_low:
        return True
    # Prefix-based: accept if it's one of the strict Victron-only prefixes
    # (ASS = Accessories is gemengd, BAT/BPC/PIN/QUA/SCC/etc. zijn 99% Victron)
    strict = {"BAT","BPC","BPP","BPR","CIP","INTD","LYN","ORI","PCH","PIN","PMP",
              "QUA","REL","SCC","SDFI","SHP","SHU","SIN","SKY","SPM","SPP","VBS",
              "BMS","BAM","BBA","BCD","CCH","CEP","CIN","CMP","COS","CTR","GSM","RCD",
              "ARG","ADA"}
    import re
    m = re.match(r"^([A-Z]+)", code or "")
    if m and m.group(1) in strict:
        return True
    # Otherwise skip (e.g. ASS items without "Victron" in description)
    return False


def load_skip_list(path):
    import csv
    skip = set()
    if not Path(path).exists():
        return skip
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            if code and not code.startswith("#"):
                skip.add(code)
    return skip
APP_DIR = Path(__file__).parent.parent  # repo root (parent of scripts/)
REPORTS_DIR = APP_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


class Odoo:
    def __init__(self, url, db, user, password, log=print):
        self.url = url.rstrip("/"); self.db = db; self.user = user; self.password = password
        self.log = log; self.s = requests.Session()
    def authenticate(self):
        r = self.s.post(f"{self.url}/web/session/authenticate",
                        json={"jsonrpc":"2.0","params":{"db":self.db,"login":self.user,"password":self.password}},
                        timeout=30); r.raise_for_status()
        d = r.json()
        if not (d.get("result") and d["result"].get("uid")):
            raise RuntimeError(f"Odoo login failed: {d}")
        self.uid = d["result"]["uid"]
        self.log(f"Odoo OK uid={self.uid}")
    def call(self, model, method, args, kwargs=None):
        r = self.s.post(f"{self.url}/web/dataset/call_kw",
                        json={"jsonrpc":"2.0","method":"call","params":
                              {"model":model,"method":method,"args":args,"kwargs":kwargs or {}}},
                        timeout=120); r.raise_for_status()
        d = r.json()
        if "error" in d: raise RuntimeError(json.dumps(d["error"])[:300])
        return d["result"]


def load_xml(path):
    prods = {}
    for p in ET.parse(path).getroot().findall("product"):
        d = {c.tag: (c.text or "").strip() for c in p}
        if d.get("id"): prods[d["id"]] = d
    return prods


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True, help="Top Systems Productlist XML")
    ap.add_argument("--config", default=str(APP_DIR / "config.json"))
    ap.add_argument("--apply", action="store_true",
                    help="Pas wijzigingen toe in Odoo (cost + sale, tenzij --apply-cost/--apply-sale gespecifieerd)")
    ap.add_argument("--apply-cost", action="store_true", help="Update supplierinfo.price")
    ap.add_argument("--apply-sale", action="store_true", help="Update template list_price")
    ap.add_argument("--cost-tolerance", type=float, default=0.01)
    ap.add_argument("--sale-tolerance", type=float, default=0.50)
    ap.add_argument("--skip-list", default=str(APP_DIR / "skip_list.csv"),
                    help="CSV met codes om uit missing report te weren")
    args = ap.parse_args()
    skip_codes = load_skip_list(args.skip_list)
    if skip_codes:
        print(f"Skip-list: {len(skip_codes)} codes worden genegeerd voor missing.")

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    odoo_cfg = cfg["odoo"]
    o = Odoo(odoo_cfg["url"], odoo_cfg["db"], odoo_cfg["user"], odoo_cfg["password"])
    o.authenticate()

    print(f"Loading XML: {args.xml}")
    xml_prods = load_xml(args.xml)
    print(f"  XML products: {len(xml_prods)}")

    print("Querying Odoo Top Systems supplierinfos (incl. archived)...")
    sis = o.call("product.supplierinfo", "search_read",
                 [[["partner_id", "=", PARTNER_ID_TOPSYSTEMS]],
                  ["id","product_tmpl_id","product_id","product_code","price","min_qty"]])
    ts_codes = {}
    for s in sis:
        code = (s["product_code"] or "").strip()
        if code: ts_codes.setdefault(code, s)
    print(f"  Top Systems supplierinfos: {len(sis)}, unique codes: {len(ts_codes)}")

    # Auto-skip: codes whose Odoo template is ARCHIVED -> user wil dit blijkbaar niet
    archived_tids = set(o.call("product.template", "search",
                                [[["seller_ids.partner_id","=",PARTNER_ID_TOPSYSTEMS],
                                  ["active","=",False]]]))
    archived_codes = set()
    if archived_tids:
        # Get codes for archived templates
        arch_sis = o.call("product.supplierinfo", "search_read",
                          [[["partner_id","=",PARTNER_ID_TOPSYSTEMS],
                            ["product_tmpl_id","in",list(archived_tids)]],
                           ["product_code"]])
        archived_codes = {(s["product_code"] or "").strip() for s in arch_sis if s["product_code"]}
    if archived_codes:
        print(f"  {len(archived_codes)} codes auto-overgeslagen (template gearchiveerd in Odoo)")
    skip_codes |= archived_codes

    # Templates with category info (we filter updates to Victron category only).
    # "Victron" detection: complete_name starts with "Victron".
    tids = list({s["product_tmpl_id"][0] for s in ts_codes.values() if s["product_tmpl_id"]})
    tmpl_data = {}
    all_cats = o.call("product.category", "search_read", [[], ["id","complete_name"]])
    victron_categ_ids = {c["id"] for c in all_cats if (c.get("complete_name") or "").startswith("Victron")}
    print(f"  Victron categories: {len(victron_categ_ids)}")
    for chunk in [tids[i:i+200] for i in range(0, len(tids), 200)]:
        rows = o.call("product.template", "read", [chunk, ["id","name","list_price","standard_price","barcode","categ_id"]])
        for r in rows:
            r["_is_victron"] = (r.get("categ_id") and r["categ_id"][0] in victron_categ_ids)
            tmpl_data[r["id"]] = r

    def _f(v):
        try: return float(v) if v else 0.0
        except (TypeError, ValueError): return None  # 'prijs op aanvraag' etc.

    # Compare. Filter:
    #   - Missing report: only Victron-prefix codes
    #   - Updates: only items whose Odoo template is in Victron category
    missing, cost_diffs, sale_diffs, no_price, skipped_non_victron = [], [], [], [], []
    for code, p in xml_prods.items():
        pn = _f(p.get("pricenett"))
        pg = _f(p.get("pricegross"))
        if pn is None or pg is None:
            no_price.append((code, p.get("description",""), p.get("pricenett",""), p.get("pricegross","")))
            continue
        new_cost = round(pn, 2)
        new_sale = round(pg / SALE_VAT_DIVISOR, 2)
        if code not in ts_codes:
            if code in skip_codes:
                continue   # user-skipped of auto-archived
            if is_victron_product(code, p.get("description","")):
                missing.append((code, p.get("description",""), pg, pn, p.get("stock","")))
            else:
                skipped_non_victron.append(code)
            continue
        si = ts_codes[code]
        cur_supplier = float(si.get("price") or 0)
        tid = si["product_tmpl_id"][0] if si["product_tmpl_id"] else None
        t = tmpl_data.get(tid, {})
        if not t.get("_is_victron"):
            skipped_non_victron.append(code)
            continue
        cur_lst = float(t.get("list_price") or 0)
        if abs(new_cost - cur_supplier) > args.cost_tolerance:
            cost_diffs.append((code, cur_supplier, new_cost, t.get("name",""), si["id"]))
        if abs(new_sale - cur_lst) > args.sale_tolerance:
            sale_diffs.append((code, cur_lst, new_sale, t.get("name",""), tid))

    extra = [c for c in ts_codes if c not in xml_prods]

    print(f"\nResultaat (Victron-only):")
    print(f"  Ontbrekend in Odoo (Victron prefix codes): {len(missing)}")
    print(f"  In Odoo niet meer in XML: {len(extra)}")
    print(f"  Geen prijs ('prijs op aanvraag'): {len(no_price)}")
    print(f"  Kostprijs verschillen: {len(cost_diffs)}")
    print(f"  Verkoopprijs verschillen: {len(sale_diffs)}")
    print(f"  Niet-Victron items (genegeerd): {len(skipped_non_victron)}")

    stamp = date.today().isoformat()
    # Reports
    with (REPORTS_DIR / f"missing_{stamp}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["code","description","pricegross_incl","pricenett_excl","stock"])
        for row in missing: w.writerow(row)
    with (REPORTS_DIR / f"cost_diffs_{stamp}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["code","current_supplier_price","new_pricenett","template_name","supplierinfo_id"])
        for row in cost_diffs: w.writerow(row)
    with (REPORTS_DIR / f"sale_diffs_{stamp}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["code","current_list_price","new_list_price","template_name","template_id"])
        for row in sale_diffs: w.writerow(row)
    with (REPORTS_DIR / f"extra_{stamp}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["code","current_supplier_price","template_name","supplierinfo_id"])
        for c in extra:
            si = ts_codes[c]
            tid = si["product_tmpl_id"][0] if si["product_tmpl_id"] else None
            w.writerow([c, si.get("price",0), tmpl_data.get(tid,{}).get("name",""), si["id"]])
    print(f"\nReports -> {REPORTS_DIR}")

    # Apply
    if args.apply:
        do_cost = args.apply_cost or not (args.apply_cost or args.apply_sale)
        do_sale = args.apply_sale or not (args.apply_cost or args.apply_sale)
        if do_cost:
            print(f"\nApply: {len(cost_diffs)} cost updates...")
            for i, (code, cur, new, name, si_id) in enumerate(cost_diffs, 1):
                try:
                    o.call("product.supplierinfo","write",[[si_id], {"price": new}])
                    print(f"  [{i}/{len(cost_diffs)}] {code}: €{cur:.2f} -> €{new:.2f}")
                except Exception as e:
                    print(f"  [{i}/{len(cost_diffs)}] {code} FOUT: {e}")
        if do_sale:
            print(f"\nApply: {len(sale_diffs)} sale price updates...")
            for i, (code, cur, new, name, tid) in enumerate(sale_diffs, 1):
                try:
                    o.call("product.template","write",[[tid], {"list_price": new}])
                    print(f"  [{i}/{len(sale_diffs)}] {code}: €{cur:.2f} -> €{new:.2f}")
                except Exception as e:
                    print(f"  [{i}/{len(sale_diffs)}] {code} FOUT: {e}")
    else:
        print("\nGeen wijzigingen toegepast (gebruik --apply om in Odoo door te voeren).")


if __name__ == "__main__":
    main()
