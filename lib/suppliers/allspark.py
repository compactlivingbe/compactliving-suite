"""All-Spark adapter — scrape de publieke webshop (uitgelogd).

All-Spark (all-spark.eu) draait op Odoo eCommerce. We scrapen uitgelogd, dus
we zien enkel de publieke prijs; de inkoopprijs berekenen we via een instelbare
korting per merk/categorie/product (zie lib/discounts.py).

Structuur van de shop (geverifieerd 2026):
  - Hoofdlijst pagineert via /en/shop/page/<N>, 16 producten per pagina.
  - Een productkaart (.oe_product) bevat:
      * img[itemprop=image] alt = "[CODE] Productnaam"   <- betrouwbare code
      * input[name=product_template_id]                  <- voor de foto-URL
      * a[itemprop=name] / titel-link                     <- naam + product-URL
      * span.oe_currency_value                            <- publieke prijs
  - Categorieën staan als /shop/category/<slug>-<id>; het merk zit meestal in
    het slug-pad (victron, voltsmile, aiko, ...).
  - Foto op hoge resolutie: /web/image/product.template/<tid>/image_1920
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

import cloudscraper
from bs4 import BeautifulSoup

from .base import Supplier, SupplierProduct, register

BASE = "https://www.all-spark.eu"
SHOP = BASE + "/en/shop"

# Merken die All-Spark voert; gedetecteerd uit het categorie-pad.
BRAND_KEYWORDS = {
    "victron": "Victron",
    "voltsmile": "Voltsmile",
    "aiko": "AIKO Solar",
    "sunbeamsystem": "SUNBEAMsystem",
    "sunbeam": "SUNBEAMsystem",
    "ruuvi": "Ruuvi",
    "scanstrut": "Scanstrut",
    "cobalt": "Cobalt",
    "all-in-one-by-all-spark": "All-Spark",
}

# Victron artikelcodes beginnen met deze letterprefixes (voor merk-fallback
# wanneer het categorie-pad geen merk prijsgeeft).
_VICTRON_PREFIX = re.compile(
    r"^(ADA|ARG|ASS|BAM|BAT|BBA|BCD|BMS|BPC|BPP|BPR|CCH|CEP|CIN|CIP|CMP|COS|"
    r"CTR|GSM|INTD|LYN|ORI|PCH|PIN|PMP|PPP|QUA|RCD|REL|SCC|SDFI|SDTG|SHP|SHU|"
    r"SIN|SKI|SKY|SPM|SPP|STG|VBB|VBS)")

_ALT_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$", re.S)
_SLUG_ID_RE = re.compile(r"-(\d+)$")


def _scraper():
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False})


def _parse_price(txt: str) -> Optional[float]:
    """'1,312.90' / '14.40' (en-locale: komma=duizend, punt=decimaal)."""
    if not txt:
        return None
    t = txt.strip().replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def _brand_from_path(path: str) -> str:
    p = (path or "").lower()
    for kw, brand in BRAND_KEYWORDS.items():
        if kw in p:
            return brand
    return ""


def _parse_card(card) -> Optional[SupplierProduct]:
    link = card.select_one("a.oe_product_image_link[href], a[itemprop=name][href]")
    url = link.get("href") if link else ""
    if url and url.startswith("/"):
        url = BASE + url

    img = card.select_one("img[itemprop=image]")
    alt = img.get("alt", "") if img else ""
    code, name = "", ""
    m = _ALT_RE.match(alt)
    if m:
        code, name = m.group(1).strip(), m.group(2).strip()
    if not name:
        title = card.select_one("a[itemprop=name]")
        if title:
            name = title.get_text(strip=True)
    if not code and url:
        # fallback: slug-prefix vóór de trailing -<id>
        slug = url.rstrip("/").split("/")[-1]
        slug = _SLUG_ID_RE.sub("", slug)
        code = slug.split("-")[0].upper()
    if not code:
        return None

    tid_in = card.select_one("input[name=product_template_id]")
    tid = tid_in.get("value") if tid_in else None
    image_url = (f"{BASE}/web/image/product.template/{tid}/image_1920"
                 if tid else "")

    price_el = card.select_one(".oe_currency_value")
    price = _parse_price(price_el.get_text() if price_el else "")

    prod: SupplierProduct = {
        "code": code,
        "name": name,
        "public_price": price,
        "image_url": image_url,
        "url": url,
        "specs": {},
    }
    return prod


def crawl_catalog(log: Callable[[str], None] = print,
                  max_pages: Optional[int] = None,
                  pause: float = 0.0) -> dict[str, SupplierProduct]:
    """Loop de hoofdlijst af tot er geen nieuwe producten meer bijkomen."""
    s = _scraper()
    products: dict[str, SupplierProduct] = {}
    page = 1
    empty_streak = 0
    while True:
        if max_pages and page > max_pages:
            break
        url = SHOP if page == 1 else f"{SHOP}/page/{page}"
        try:
            r = s.get(url, timeout=40)
        except Exception as e:
            log(f"  pagina {page}: netwerkfout {e}")
            break
        if r.status_code != 200:
            log(f"  pagina {page}: HTTP {r.status_code} — stop")
            break
        cards = BeautifulSoup(r.text, "html.parser").select(".oe_product")
        new = 0
        for card in cards:
            p = _parse_card(card)
            if p and p["code"] not in products:
                products[p["code"]] = p
                new += 1
        log(f"  pagina {page}: {len(cards)} kaarten, {new} nieuw "
            f"(totaal {len(products)})")
        if new == 0:
            empty_streak += 1
            if empty_streak >= 2:   # twee pagina's zonder nieuws -> einde
                break
        else:
            empty_streak = 0
        page += 1
        if pause:
            time.sleep(pause)
    return products


def crawl_category_map(log: Callable[[str], None] = print,
                       pause: float = 0.0) -> dict[str, dict]:
    """Bouw {template_id: {category, category_path, brand}} door per categorie
    de productkaarten te bekijken. Het diepste (langste slug) wint per product."""
    s = _scraper()
    soup = BeautifulSoup(s.get(SHOP, timeout=40).text, "html.parser")
    cats = {}
    for a in soup.select('a[href*="/shop/category/"]'):
        href = a.get("href") or ""
        label = a.get_text(strip=True)
        slug = href.rstrip("/").split("/shop/category/")[-1]
        if slug and label:
            cats[slug] = label
    n_cats = len(cats)
    log(f"  {n_cats} categorieën gevonden — nu per categorie de producten "
        "in kaart brengen (dit kan even duren)...")

    tid_map: dict[str, dict] = {}
    for i, (slug, label) in enumerate(cats.items(), 1):
        cat_url = f"{BASE}/en/shop/category/{slug}"
        brand = _brand_from_path(slug)
        page = 1
        while True:
            u = cat_url if page == 1 else f"{cat_url}/page/{page}"
            try:
                r = s.get(u, timeout=25)
            except Exception:
                break
            if r.status_code != 200:
                break
            cards = BeautifulSoup(r.text, "html.parser").select(".oe_product")
            if not cards:
                break
            got = False
            for card in cards:
                tid_in = card.select_one("input[name=product_template_id]")
                tid = tid_in.get("value") if tid_in else None
                if not tid:
                    continue
                got = True
                prev = tid_map.get(tid)
                # diepere/langere slug => specifiekere categorie
                if not prev or len(slug) > prev["_slug_len"]:
                    tid_map[tid] = {"category": label, "category_path": slug,
                                    "brand": brand, "_slug_len": len(slug)}
            if not got or len(cards) < 16:
                break
            page += 1
            if pause:
                time.sleep(pause)
        if i % 5 == 0 or i == n_cats:
            log(f"  ...{i}/{n_cats} categorieën verwerkt "
                f"({len(tid_map)} producten gekoppeld)")
    for v in tid_map.values():
        v.pop("_slug_len", None)
    return tid_map


class AllSparkSupplier(Supplier):
    def fetch(self, log: Callable[[str], None] = print,
              with_categories: bool = True, max_pages: Optional[int] = None,
              **kwargs) -> dict[str, SupplierProduct]:
        log("All-Spark: hoofdcatalogus crawlen...")
        products = crawl_catalog(log, max_pages=max_pages)
        log(f"All-Spark: {len(products)} producten in catalogus")

        if with_categories:
            log("All-Spark: categorieën + merken in kaart brengen...")
            # tid per product opbouwen uit de foto-URL
            tid_by_code = {}
            for code, p in products.items():
                m = re.search(r"product\.template/(\d+)/", p.get("image_url", ""))
                if m:
                    tid_by_code[code] = m.group(1)
            cat_map = crawl_category_map(log)
            for code, p in products.items():
                info = cat_map.get(tid_by_code.get(code, ""))
                if info:
                    p["category"] = info["category"]
                    p["category_path"] = info["category_path"]
                    p["brand"] = info["brand"]

        # merk-fallback via Victron-codeprefix
        for code, p in products.items():
            if not p.get("brand") and _VICTRON_PREFIX.match(code):
                p["brand"] = "Victron"
            p["price_incl_vat"] = self.price_incl_vat
        return products

    def compute_cost(self, product: SupplierProduct) -> Optional[float]:
        # Inkoopprijs wordt via discounts.py berekend met de korting-config;
        # zonder config is de "kost" onbekend (None), niet de publieke prijs.
        return product.get("cost_price")


ALLSPARK = register(AllSparkSupplier(
    key="allspark",
    label="All-Spark",
    partner_env="ALLSPARK_PARTNER_ID",
    price_incl_vat=True,
))
