"""Product matching: fuzzy match factuurlijnen tegen Odoo product-database."""
from difflib import SequenceMatcher
from odoo_client import OdooClient

# Keywords die altijd naar het verzendkost-product wijzen
SHIPPING_KEYWORDS = [
    "versand", "verzend", "shipping", "transport", "porto",
    "frais de port", "freight", "delivery", "verzendkost", "leveringskost"
]


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def is_shipping(beschrijving: str) -> bool:
    """Detecteer verzendkost-lijn op basis van keywords."""
    if not beschrijving:
        return False
    low = beschrijving.lower()
    return any(kw in low for kw in SHIPPING_KEYWORDS)


def find_product(odoo: OdooClient, beschrijving: str, artikelnr: str = None, prijs_hint: float = None,
                 threshold: float = 0.65) -> dict:
    """
    Vind product in Odoo. Strategie:
    1. Match op artikelnummer (default_code) — exact
    2. Match op beschrijving — fuzzy, return beste match boven threshold
    3. Bij meerdere kandidaten: gebruik prijs_hint om te kiezen (binnen 20%)

    Returns: {'id': int, 'name': str, 'match_method': 'exact_sku' | 'fuzzy' | 'none', 'score': float}
            of None bij geen match.
    """
    # 0. Verzendkost-detectie: altijd naar VERZEND-product
    if is_shipping(beschrijving):
        res = odoo.search_read(
            "product.product",
            [("default_code", "=", "VERZEND")],
            ["id", "name", "default_code", "list_price", "standard_price"],
            1
        )
        if res:
            return {**res[0], "match_method": "shipping_keyword", "score": 1.0}

    # 1. Exact match op SKU
    if artikelnr:
        res = odoo.search_read(
            "product.product",
            [("default_code", "=", artikelnr)],
            ["id", "name", "default_code", "list_price", "standard_price"],
            1
        )
        if res:
            return {**res[0], "match_method": "exact_sku", "score": 1.0}

    # 2. Fuzzy op naam: zoek breder
    if not beschrijving or len(beschrijving) < 3:
        return None

    # Splits beschrijving in keywords, zoek op de eerste 2-3 belangrijke woorden
    keywords = [w for w in beschrijving.split() if len(w) > 2][:3]
    if not keywords:
        return None

    domain = ["|"] * (len(keywords) - 1) + [("name", "ilike", kw) for kw in keywords]
    candidates = odoo.search_read(
        "product.product",
        domain,
        ["id", "name", "default_code", "list_price", "standard_price"],
        20
    )

    if not candidates:
        return None

    # Score op fuzzy match + optionele prijs-match
    scored = []
    for c in candidates:
        s = similarity(beschrijving, c["name"])
        # Bonus als prijs binnen 20% van standard_price ligt
        if prijs_hint and c.get("standard_price"):
            sp = c["standard_price"]
            if sp > 0 and abs(prijs_hint - sp) / sp < 0.2:
                s += 0.1
        scored.append((s, c))

    scored.sort(key=lambda x: -x[0])
    best_score, best = scored[0]
    if best_score >= threshold:
        return {**best, "match_method": "fuzzy", "score": round(best_score, 3)}

    return None


def find_product_candidates(odoo: OdooClient, beschrijving: str, artikelnr: str = None,
                            prijs_hint: float = None, top_n: int = 5) -> list:
    """
    Geef top-N kandidaat-producten terug voor UI-review (fuzzy match).
    Returns: [{'id', 'name', 'default_code', 'standard_price', 'score', 'match_method'}, ...]
    """
    results = []
    seen_ids = set()

    # Verzendkost: 1 kandidaat
    if is_shipping(beschrijving):
        res = odoo.search_read(
            "product.product",
            [("default_code", "=", "VERZEND")],
            ["id", "name", "default_code", "list_price", "standard_price"], 1
        )
        if res:
            results.append({**res[0], "match_method": "shipping_keyword", "score": 1.0})
            seen_ids.add(res[0]["id"])

    # Exact SKU match
    if artikelnr:
        res = odoo.search_read(
            "product.product",
            [("default_code", "=", artikelnr)],
            ["id", "name", "default_code", "list_price", "standard_price"], 3
        )
        for r in res:
            if r["id"] not in seen_ids:
                results.append({**r, "match_method": "exact_sku", "score": 1.0})
                seen_ids.add(r["id"])

    # Fuzzy
    if beschrijving and len(beschrijving) >= 3:
        keywords = [w for w in beschrijving.split() if len(w) > 2][:3]
        if keywords:
            domain = ["|"] * (len(keywords) - 1) + [("name", "ilike", kw) for kw in keywords]
            candidates = odoo.search_read(
                "product.product", domain,
                ["id", "name", "default_code", "list_price", "standard_price"], 30
            )
            scored = []
            for c in candidates:
                if c["id"] in seen_ids:
                    continue
                s = similarity(beschrijving, c["name"])
                if prijs_hint and c.get("standard_price"):
                    sp = c["standard_price"]
                    if sp > 0 and abs(prijs_hint - sp) / sp < 0.2:
                        s += 0.1
                scored.append((s, c))
            scored.sort(key=lambda x: -x[0])
            for s, c in scored[: top_n - len(results)]:
                results.append({**c, "match_method": "fuzzy", "score": round(s, 3)})
                seen_ids.add(c["id"])

    return results[:top_n]


def find_partner(odoo: OdooClient, naam: str, vat: str = None) -> dict:
    """Zoek leverancier. Bij geen match: return None (laat caller beslissen)."""
    return odoo.find_partner(naam, vat)
