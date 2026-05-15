"""VBD Services (vbdservices.nl) - OpenCart productlijst scraper.
Geen login: openbare prijzen incl BTW (NL 21%), excl wordt afgeleid.
"""
import re, time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE = "https://vbdservices.nl"
# Echte Chrome UA — anti-bot WAF blokkeert custom UA's
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BTW_NL = 0.21


def _make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    })
    return s


def _warmup(s, log=print):
    """Eerst homepage bezoeken om cookies/sessie te krijgen."""
    try:
        r = s.get(BASE + "/", timeout=20)
        log(f"  warmup: {r.status_code} ({len(s.cookies)} cookies)")
        time.sleep(1.0)
    except Exception as e:
        log(f"  warmup faalde: {e}")

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
    img = ""
    if img_el:
        # Probeer hogere resolutie uit srcset (2x = 500x500)
        srcset = img_el.get("srcset", "")
        m = re.search(r"(https?://\S+?)\s+2x", srcset)
        img = m.group(1) if m else img_el.get("src", "")

    return {
        "sku": sku, "name": name, "brand": brand,
        "price_incl": price_incl, "price_excl": price_excl,
        "url": url, "description": desc, "image_url": img,
    }


def fetch_category(path, log=print, delay=0.4, session=None, referer=None):
    """Crawl one category path with all pages. Returns list of products.
    Use shared session for cookie persistence across categories."""
    out = []
    seen_skus = set()
    s = session or _make_session()
    if session is None:
        _warmup(s, log=log)
    page = 1
    while True:
        url = urljoin(BASE, path)
        params = {"page": page} if page > 1 else None
        headers = {"Referer": referer or (BASE + "/")}
        # Retry-loop voor 403 (anti-bot)
        r = None
        for attempt in range(3):
            r = s.get(url, params=params, timeout=30, headers=headers)
            if r.status_code == 200:
                break
            if r.status_code in (403, 429, 503):
                wait = (attempt + 1) * 3
                log(f"  [{path}] page {page} HTTP {r.status_code} — backoff {wait}s (attempt {attempt+1}/3)")
                time.sleep(wait)
                continue
            break
        if r is None or r.status_code != 200:
            log(f"  [{path}] page {page} HTTP {r.status_code if r else '?'} — opgegeven")
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


def fetch_all(categories=None, log=print, delay=0.4, between_cats=1.5):
    """Crawl all configured categories. Returns deduped list by SKU.
    Eén shared session voor cookies; tussen categorieën pauze om WAF te ontwijken."""
    cats = categories or DEFAULT_CATEGORIES
    by_sku = {}
    s = _make_session()
    _warmup(s, log=log)
    prev_url = BASE + "/"
    for i, cat in enumerate(cats):
        try:
            prods = fetch_category(cat, log=log, delay=delay,
                                    session=s, referer=prev_url)
            for p in prods:
                by_sku.setdefault(p["sku"], p)
            prev_url = urljoin(BASE, cat)
        except Exception as e:
            log(f"  [{cat}] FOUT: {e}")
        if i < len(cats) - 1:
            time.sleep(between_cats)
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

    missing, cost_diffs, sale_diffs, matches = [], [], [], []
    for p in vbd_products:
        sku = p["sku"]
        cost = p["price_excl"]
        if sku not in by_code:
            missing.append(p)
            continue
        si = by_code[sku]
        tid = si["product_tmpl_id"][0] if si.get("product_tmpl_id") else None
        tmpl = tmpl_by_id.get(tid, {})
        cur_supplier_price = float(si.get("price") or 0)
        cur_list = float(tmpl.get("list_price") or 0)
        cur_cost = float(tmpl.get("standard_price") or 0)
        cost_delta = (cost - cur_supplier_price) if cost is not None else None
        sale_delta = (p["price_incl"] - cur_list) if p["price_incl"] is not None else None
        matches.append({
            "sku": sku,
            "name": p["name"],
            "odoo_name": tmpl.get("name", ""),
            "image_url": p.get("image_url", ""),
            "vbd_excl": cost,
            "vbd_incl": p["price_incl"],
            "odoo_supplier_price": cur_supplier_price,
            "odoo_standard_price": cur_cost,
            "odoo_list_price": cur_list,
            "Δ_kost": cost_delta,
            "Δ_verkoop": sale_delta,
            "template_id": tid,
            "supplierinfo_id": si["id"],
            "url": p.get("url", ""),
        })
        # Kostprijs verschil
        if cost is not None and abs(cur_supplier_price - cost) > 0.01:
            cost_diffs.append({
                "sku": sku, "name": p["name"],
                "current_supplier_price": cur_supplier_price,
                "new_pricenett": cost,
                "supplierinfo_id": si["id"],
                "template_id": tid,
            })
        # Verkoopprijs verschil
        if p["price_incl"] is not None and abs(cur_list - p["price_incl"]) > 0.01:
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
        "matches": matches,
        "total_vbd": len(vbd_products),
        "total_matched": len(vbd_products) - len(missing),
    }
