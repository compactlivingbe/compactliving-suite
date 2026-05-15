"""VBD Services (vbdservices.nl) - OpenCart productlijst scraper.
Geen login: openbare prijzen incl BTW (NL 21%), excl wordt afgeleid.
"""
import re, time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE = "https://vbdservices.nl"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 CompactLivingSync/1.0"
BTW_NL = 0.21

# Default categorieën - kan via UI uitgebreid worden
DEFAULT_CATEGORIES = [
    "/Autoterm-standkachel-diesel-kachel-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/Autoterm-standkachel-dieselkachel-boot-camper-importeur-nederland",
    "/Autoterm-standkachel-diesel-kachel-importeur/inbouwmateriaal-luchtverwarming-uitblaas-luchtslang-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/inbouwmateriaal-luchtverwarming-uitblaas-luchtslang-importeur/luchtslang",
    "/Autoterm-standkachel-diesel-kachel-importeur/inbouwmateriaal-luchtverwarming-uitblaas-luchtslang-importeur/uitblaasroosters",
    "/Autoterm-standkachel-diesel-kachel-importeur/inbouwmateriaal-luchtverwarming-uitblaas-luchtslang-importeur/T-en-stukken-verdeelstukken",
    "/Autoterm-standkachel-diesel-kachel-importeur/inbouwmateriaal-luchtverwarming-uitblaas-luchtslang-importeur/Verlopen-bochten",
    "/Autoterm-standkachel-diesel-kachel-importeur/inbouwmateriaal-luchtverwarming-uitblaas-luchtslang-importeur/Slangdoorvoeren-slangverbinders",
    "/Autoterm-standkachel-diesel-kachel-importeur/Autoterm-bedieningspaneel-modem-verlengkabel-standkachel-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/montageplaat-montagebeugel-montagebox-ophangbeugel-importeur-autoterm",
    "/Autoterm-standkachel-diesel-kachel-importeur/Autoterm-standkachel-diesel-kachel-convector-kachelradiator-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/Inbouwmateriaal-centrale-verwarming-autoterm-standkachel-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/Inbouwmateriaal-centrale-verwarming-autoterm-standkachel-importeur/Messing-fitwerk",
    "/Autoterm-standkachel-diesel-kachel-importeur/Inbouwmateriaal-centrale-verwarming-autoterm-standkachel-importeur/Expansietanks",
    "/Autoterm-standkachel-diesel-kachel-importeur/Autoterm-standkachel-uitlaat-demper-doorvoer-huiddoorvoer-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/Autoterm-standkachel-stille-pomp-brandstofpomp-inbouwmateriaal-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/Autoterm-standkachel-diesel-kachel-onderdelen-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/Autoterm-standkachel-diesel-kachel-onderdelen-importeur/Autoterm-Air-2d-onderdelen-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/Autoterm-standkachel-diesel-kachel-onderdelen-importeur/Autoterm-Air-4d-onderdelen-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/Autoterm-standkachel-diesel-kachel-onderdelen-importeur/Autoterm-Air-9d-onderdelen-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/Autoterm-standkachel-diesel-kachel-onderdelen-importeur/Autoterm-Flow-5d-onderdelen-importeur",
    "/Autoterm-standkachel-diesel-kachel-importeur/Autoterm-standkachel-diesel-kachel-onderdelen-importeur/Autoterm-Flow-14d-onderdelen-importeur",
]


def _parse_price(txt):
    """'€10,28' -> 10.28"""
    if not txt: return None
    m = re.search(r"([\d\.]+,\d{2})", txt)
    if not m: return None
    return float(m.group(1).replace(".", "").replace(",", "."))


def _parse_card(card):
    """Returns dict or None."""
    name_el = card.select_one(".name a")
    if not name_el: return None
    name = name_el.get_text(" ", strip=True)
    url = name_el.get("href", "")

    sku = ""
    for span in card.select(".stats .stat-2 span"):
        t = span.get_text(strip=True)
        if t and t != "Model:":
            sku = t
            break

    brand = ""
    for span in card.select(".stats .stat-1 span"):
        t = span.get_text(strip=True)
        if t and t != "Merk:":
            brand = t
            break

    price_incl = _parse_price(
        card.select_one(".price .price-normal").get_text(strip=True)
        if card.select_one(".price .price-normal") else ""
    )
    if price_incl is None:
        # fallback: any price-like element
        pe = card.select_one(".price")
        price_incl = _parse_price(pe.get_text(" ", strip=True) if pe else "")
    price_excl = round(price_incl / (1 + BTW_NL), 2) if price_incl else None

    desc_el = card.select_one(".description")
    desc = desc_el.get_text(" ", strip=True)[:300] if desc_el else ""

    img_el = card.select_one("img.img-first, img.img-responsive")
    img = img_el.get("src", "") if img_el else ""

    return {
        "sku": sku, "name": name, "brand": brand,
        "price_incl": price_incl, "price_excl": price_excl,
        "url": url, "description": desc, "image_url": img,
    }


def fetch_category(path, log=print, delay=0.4):
    """Crawl one category path with all pages. Returns list of products."""
    out = []
    seen_skus = set()
    s = requests.Session()
    s.headers["User-Agent"] = UA
    page = 1
    while True:
        url = urljoin(BASE, path)
        params = {"page": page} if page > 1 else None
        r = s.get(url, params=params, timeout=30)
        if r.status_code != 200:
            log(f"  [{path}] page {page} HTTP {r.status_code}")
            break
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select(".product-layout")
        if not cards:
            break
        new_count = 0
        for c in cards:
            p = _parse_card(c)
            if not p or not p["sku"]:
                continue
            if p["sku"] in seen_skus:
                continue
            seen_skus.add(p["sku"])
            p["category"] = path
            out.append(p)
            new_count += 1
        log(f"  [{path}] page {page}: {new_count} nieuwe (total {len(out)})")
        # Pagination check
        next_link = soup.select_one("ul.pagination li.active + li a, .pagination a[rel='next']")
        if not next_link or new_count == 0:
            break
        page += 1
        if page > 30:  # safety
            break
        time.sleep(delay)
    return out


def fetch_all(categories=None, log=print, delay=0.4):
    """Crawl all configured categories. Returns deduped list by SKU."""
    cats = categories or DEFAULT_CATEGORIES
    by_sku = {}
    for cat in cats:
        try:
            for p in fetch_category(cat, log=log, delay=delay):
                # First-seen wins (preserve original category)
                by_sku.setdefault(p["sku"], p)
        except Exception as e:
            log(f"  [{cat}] FOUT: {e}")
    return list(by_sku.values())


# ============================================================================
# Vergelijken met Odoo (via OdooClient)
# ============================================================================
def compare_with_odoo(odoo, partner_id, vbd_products, log=print):
    """Returns dict: missing, cost_diffs, sale_diffs."""
    skus = [p["sku"] for p in vbd_products]
    # Haal alle supplierinfo van VBD op
    sis = odoo.search_read(
        "product.supplierinfo",
        [["partner_id", "=", partner_id]],
        ["id", "product_tmpl_id", "product_code", "price"],
        limit=5000,
    )
    by_code = {}
    for s in sis:
        code = (s.get("product_code") or "").strip()
        if code: by_code[code] = s
    log(f"Odoo: {len(by_code)} VBD supplierinfo entries gevonden.")

    # Haal templates op voor cost+sale prijzen
    tmpl_ids = list({s["product_tmpl_id"][0] for s in by_code.values() if s.get("product_tmpl_id")})
    tmpls = odoo.read("product.template", tmpl_ids,
                       ["id", "name", "standard_price", "list_price"]) if tmpl_ids else []
    tmpl_by_id = {t["id"]: t for t in tmpls}

    missing, cost_diffs, sale_diffs = [], [], []
    for p in vbd_products:
        sku = p["sku"]
        cost = p["price_excl"]
        if sku not in by_code:
            missing.append(p)
            continue
        si = by_code[sku]
        tid = si["product_tmpl_id"][0] if si.get("product_tmpl_id") else None
        tmpl = tmpl_by_id.get(tid, {})
        # Kostprijs verschil (supplierinfo.price vs VBD excl BTW)
        if cost is not None and abs(float(si.get("price") or 0) - cost) > 0.01:
            cost_diffs.append({
                "sku": sku, "name": p["name"],
                "current_supplier_price": float(si.get("price") or 0),
                "new_pricenett": cost,
                "supplierinfo_id": si["id"],
                "template_id": tid,
            })
        # Verkoopprijs verschil (template.list_price vs VBD incl BTW als referentie)
        if p["price_incl"] is not None:
            cur_list = float(tmpl.get("list_price") or 0)
            if abs(cur_list - p["price_incl"]) > 0.01:
                sale_diffs.append({
                    "sku": sku, "name": p["name"],
                    "current_list_price": cur_list,
                    "vbd_incl_btw": p["price_incl"],
                    "template_id": tid,
                })
    return {
        "missing": missing,
        "cost_diffs": cost_diffs,
        "sale_diffs": sale_diffs,
        "total_vbd": len(vbd_products),
        "total_matched": len(vbd_products) - len(missing),
    }
