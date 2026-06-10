"""Generieke change-detectie voor leverancier-syncs.

Twee soorten vergelijking:

1. diff_snapshots(old, new) — wat is er veranderd t.o.v. de vorige scrape?
   nieuwe producten, verdwenen producten, prijswijzigingen, en nieuwe/
   verwijderde categorieën.

2. diff_vs_odoo(new, odoo_index, cost_of) — wat moet er in Odoo gebeuren?
   ontbrekende producten (importeren), kostprijsverschillen (supplierinfo),
   en producten die niet meer bij de leverancier zijn (kandidaat archiveren).

Beide werken op de genormaliseerde SupplierProduct-vorm, dus voor élke
leverancier identiek.
"""
from __future__ import annotations

from typing import Callable, Optional


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def diff_snapshots(old: dict, new: dict, price_field: str = "public_price",
                   tol: float = 0.005) -> dict:
    """Vergelijk vorige en nieuwe scrape. Returns dict met lijsten."""
    old = old or {}
    new = new or {}
    old_codes, new_codes = set(old), set(new)

    added = [new[c] for c in sorted(new_codes - old_codes)]
    removed = [old[c] for c in sorted(old_codes - new_codes)]

    price_changes = []
    for c in sorted(old_codes & new_codes):
        o, n = _num(old[c].get(price_field)), _num(new[c].get(price_field))
        if o is None or n is None:
            continue
        if abs(n - o) > tol:
            price_changes.append({
                "code": c, "name": new[c].get("name", ""),
                "old_price": o, "new_price": n, "delta": round(n - o, 2),
            })

    def _cats(snap):
        out = {}
        for p in snap.values():
            cat = p.get("category") or ""
            if cat:
                out.setdefault(cat, p.get("category_path", cat))
        return out

    old_cats, new_cats = _cats(old), _cats(new)
    cats_added = sorted(set(new_cats) - set(old_cats))
    cats_removed = sorted(set(old_cats) - set(new_cats))

    return {
        "added": added,
        "removed": removed,
        "price_changes": price_changes,
        "categories_added": cats_added,
        "categories_removed": cats_removed,
    }


def diff_vs_odoo(new: dict, odoo_index: dict,
                 cost_of: Callable[[dict], Optional[float]],
                 exclusions: Optional[dict] = None,
                 cost_tol: float = 0.01) -> dict:
    """Bepaal wat er in Odoo moet gebeuren.

    new          : {code: SupplierProduct}
    odoo_index   : {code: {tmpl_id, supplierinfo_id, supplier_price,
                           list_price, name, active}}
    cost_of      : functie die de inkoopprijs van een product berekent
    exclusions   : {"products": [...], "categories": [...]} — uitgesloten items
    """
    excl_products = set((exclusions or {}).get("products") or [])
    excl_categories = set((exclusions or {}).get("categories") or [])

    def _excluded(p) -> bool:
        return (p.get("code") in excl_products
                or (p.get("category") or "") in excl_categories)

    missing, cost_diffs = [], []
    for code, p in new.items():
        if _excluded(p):
            continue
        cost = cost_of(p)
        if code not in odoo_index:
            missing.append({
                "code": code, "name": p.get("name", ""),
                "brand": p.get("brand", ""), "category": p.get("category", ""),
                "public_price": p.get("public_price"), "cost_price": cost,
                "image_url": p.get("image_url", ""), "url": p.get("url", ""),
            })
            continue
        if cost is None:
            continue
        cur = _num(odoo_index[code].get("supplier_price"))
        if cur is None or abs(cost - cur) > cost_tol:
            cost_diffs.append({
                "code": code, "name": odoo_index[code].get("name", p.get("name", "")),
                "current_cost": cur, "new_cost": cost,
                "delta": None if cur is None else round(cost - cur, 2),
                "supplierinfo_id": odoo_index[code].get("supplierinfo_id"),
                "tmpl_id": odoo_index[code].get("tmpl_id"),
            })

    # In Odoo (met deze leverancier) maar niet meer bij de leverancier zelf
    gone = []
    for code, oi in odoo_index.items():
        if code not in new and oi.get("active", True):
            gone.append({"code": code, "name": oi.get("name", ""),
                         "tmpl_id": oi.get("tmpl_id"),
                         "current_cost": oi.get("supplier_price")})

    return {"missing": missing, "cost_diffs": cost_diffs, "gone": gone}
