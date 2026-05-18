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


def _clean_keywords(beschrijving: str) -> list:
    """Tokenize beschrijving + strip verpakkings-suffixen (/1m, /2m, /500ml, ...)."""
    import re
    txt = beschrijving or ""
    # Verwijder hoeveelheid-suffixen die niet bij de productnaam horen
    # bv. "Platte Strip Alu zil.30x2/2m" → strip "/2m"
    txt = re.sub(r"/\d+(?:[,.]\d+)?\s*(?:m|mm|cm|ml|l|kg|g|stk|st)\b", " ", txt, flags=re.I)
    # Split op spaties + niet-alfanum (behalve cijfers en punten/strepen in codes)
    tokens = re.split(r"[\s,;()]+", txt)
    keywords = [t for t in tokens if len(t) > 2]
    return keywords


def find_product_candidates(odoo: OdooClient, beschrijving: str, artikelnr: str = None,
                            prijs_hint: float = None, top_n: int = 5,
                            partner_id: int = None) -> list:
    """
    Geef top-N kandidaat-producten terug voor UI-review (fuzzy match).
    Strategie:
      0. Verzendkost-detectie
      1. Exact SKU match (default_code)
      2. Match via supplierinfo van bill-partner (product_code of product_name)
      3. AND-search op alle keywords (alle moeten matchen) — beste kandidaten
      4. OR-search op keywords als fallback
    Returns: [{'id', 'name', 'default_code', 'standard_price', 'score', 'match_method'}, ...]
    """
    results = []
    seen_ids = set()

    def add(prod, method, score):
        if prod["id"] in seen_ids: return
        results.append({**prod, "match_method": method, "score": round(float(score), 3)})
        seen_ids.add(prod["id"])

    # 0. Verzendkost
    if is_shipping(beschrijving):
        res = odoo.search_read(
            "product.product", [("default_code", "=", "VERZEND")],
            ["id", "name", "default_code", "list_price", "standard_price"], 1)
        if res: add(res[0], "shipping_keyword", 1.0)

    # 1. Exact SKU
    if artikelnr:
        res = odoo.search_read(
            "product.product", [("default_code", "=", artikelnr)],
            ["id", "name", "default_code", "list_price", "standard_price"], 3)
        for r in res: add(r, "exact_sku", 1.0)

    # 2. Match via supplierinfo van bill-partner
    if partner_id and beschrijving:
        try:
            sis = odoo.search_read(
                "product.supplierinfo",
                ["&", ("partner_id", "=", int(partner_id)),
                 "|", ("product_name", "ilike", beschrijving[:30]),
                 ("product_code", "ilike", beschrijving[:30])],
                ["id", "product_tmpl_id", "product_id", "product_code", "product_name"], 10)
            tmpl_ids = list({s["product_tmpl_id"][0] for s in sis if s.get("product_tmpl_id")})
            if tmpl_ids:
                prods = odoo.search_read(
                    "product.product", [("product_tmpl_id", "in", tmpl_ids)],
                    ["id", "name", "default_code", "list_price", "standard_price"], 10)
                for p in prods:
                    add(p, "supplier_match", 0.95)
        except Exception:
            pass

    # 3+4. Fuzzy op naam
    if beschrijving and len(beschrijving) >= 3:
        keywords = _clean_keywords(beschrijving)[:5]
        if keywords:
            # 3) AND search: alle keywords moeten in naam voorkomen
            try:
                and_domain = [("name", "ilike", kw) for kw in keywords]
                and_cands = odoo.search_read(
                    "product.product", and_domain,
                    ["id", "name", "default_code", "list_price", "standard_price"], 20, "name")
            except Exception:
                and_cands = []
            scored = []
            for c in and_cands:
                if c["id"] in seen_ids: continue
                s = similarity(beschrijving, c["name"]) + 0.2  # AND-match bonus
                if prijs_hint and c.get("standard_price"):
                    sp = c["standard_price"]
                    if sp > 0 and abs(prijs_hint - sp) / sp < 0.2:
                        s += 0.1
                scored.append((s, c))

            # 4) OR search als aanvulling
            try:
                or_domain = ["|"] * (len(keywords[:3]) - 1) + [("name", "ilike", kw) for kw in keywords[:3]]
                or_cands = odoo.search_read(
                    "product.product", or_domain,
                    ["id", "name", "default_code", "list_price", "standard_price"], 50)
            except Exception:
                or_cands = []
            for c in or_cands:
                if c["id"] in seen_ids: continue
                if any(sc[1]["id"] == c["id"] for sc in scored): continue
                s = similarity(beschrijving, c["name"])
                if prijs_hint and c.get("standard_price"):
                    sp = c["standard_price"]
                    if sp > 0 and abs(prijs_hint - sp) / sp < 0.2:
                        s += 0.1
                scored.append((s, c))

            scored.sort(key=lambda x: -x[0])
            for s, c in scored[: top_n - len(results)]:
                add(c, "fuzzy", min(s, 1.0))

    return results[:top_n]


def find_partner(odoo: OdooClient, naam: str, vat: str = None) -> dict:
    """Zoek leverancier. Bij geen match: return None (laat caller beslissen)."""
    return odoo.find_partner(naam, vat)
