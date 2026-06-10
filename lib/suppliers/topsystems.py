"""Top Systems adapter — leest de Top Systems product-XML.

Top Systems levert een XML-productlijst (geen scrape nodig). De kostprijs is
exact bekend (pricenett, excl BTW), dus hier geen korting-berekening zoals bij
All-Spark. Deze adapter normaliseert de XML naar de gedeelde SupplierProduct-
vorm zodat het sync-dashboard en de inkoop-optimalisatie Top Systems en
All-Spark op dezelfde manier kunnen behandelen.

De volwassen missing/kost/verkoop-analyse blijft in lib/topsystems_sync.py
(pagina 4 draait die als subprocess). Deze adapter is de uniforme ingang voor
de overkoepelende tools.

LET OP: de XML-tags voor categorie/EAN/foto zijn niet geverifieerd tegen een
echt bestand. Bekend zijn: id, description, pricenett, pricegross, stock.
We mappen categorie/EAN/foto op een set kandidaat-tags en bewaren alle ruwe
tags onder "raw", zodat niets verloren gaat tot we het schema bevestigen.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Callable, Optional

from .base import Supplier, SupplierProduct, register

# Kandidaat-tagnamen (eerste niet-lege wint).
_CATEGORY_TAGS = ("category", "categoryname", "categorie", "group", "productgroup", "groep")
_EAN_TAGS = ("ean", "barcode", "gtin", "eancode")
_IMAGE_TAGS = ("image", "imageurl", "picture", "photo", "img")
_BRAND_TAGS = ("brand", "manufacturer", "merk", "fabrikant")


def _first(d: dict, tags) -> str:
    for t in tags:
        v = (d.get(t) or "").strip()
        if v:
            return v
    return ""


def _num(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_xml_root(root) -> dict[str, SupplierProduct]:
    products: dict[str, SupplierProduct] = {}
    for p in root.findall("product"):
        d = {c.tag: (c.text or "").strip() for c in p}
        code = d.get("id") or _first(d, ("code", "sku", "articlecode"))
        if not code:
            continue
        pricenett = _num(d.get("pricenett"))   # excl BTW = kostprijs
        pricegross = _num(d.get("pricegross"))  # incl BTW = publieke prijs
        prod: SupplierProduct = {
            "code": code.strip(),
            "name": d.get("description", "").strip(),
            "brand": _first(d, _BRAND_TAGS) or "Victron",
            "category": _first(d, _CATEGORY_TAGS),
            "public_price": pricegross,
            "price_incl_vat": True,
            "cost_price": pricenett,
            "ean": _first(d, _EAN_TAGS),
            "image_url": _first(d, _IMAGE_TAGS),
            "url": "",
            "specs": {},
            "raw": d,
        }
        products[prod["code"]] = prod
    return products


def parse_xml_path(path: str) -> dict[str, SupplierProduct]:
    return parse_xml_root(ET.parse(path).getroot())


def parse_xml_bytes(data: bytes) -> dict[str, SupplierProduct]:
    return parse_xml_root(ET.fromstring(data))


class TopSystemsSupplier(Supplier):
    def fetch(self, log: Callable[[str], None] = print,
              xml_path: Optional[str] = None, xml_bytes: Optional[bytes] = None,
              **kwargs) -> dict[str, SupplierProduct]:
        if xml_bytes is not None:
            prods = parse_xml_bytes(xml_bytes)
        elif xml_path:
            prods = parse_xml_path(xml_path)
        else:
            raise ValueError("Top Systems fetch vereist xml_path of xml_bytes")
        log(f"Top Systems: {len(prods)} producten uit XML")
        return prods

    def compute_cost(self, product: SupplierProduct) -> Optional[float]:
        # Kostprijs is exact bekend (pricenett, excl BTW).
        return product.get("cost_price")


TOPSYSTEMS = register(TopSystemsSupplier(
    key="topsystems",
    label="Top Systems",
    partner_env="TOPSYSTEMS_PARTNER_ID",
    partner_default=690,
    price_incl_vat=True,
))
