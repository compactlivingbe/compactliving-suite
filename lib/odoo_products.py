"""Odoo-product helpers, gedeeld door alle leverancier-syncs.

- Bouw een index van bestaande producten per leverancier (op artikelcode).
- Maak ontbrekende producten aan (template + supplierinfo + foto).
- Update kostprijs (supplierinfo) en verkoopprijs (template.list_price).
- Zet kenmerken als Odoo-attributen (no-variant), conform de huidige conventie.

NB: de attribuut-functie is een eerste implementatie; verifieer ze tegen je
bestaande Victron-producten voor je ze breed inzet (zie set_specs_attributes).
"""
from __future__ import annotations

import base64
from typing import Optional

import requests


# ---------------------------------------------------------------------------
# Index van bestaande producten per leverancier
# ---------------------------------------------------------------------------
def build_supplier_index(odoo, partner_id: int) -> dict:
    """{code: {tmpl_id, supplierinfo_id, supplier_price, list_price, name,
               standard_price, active}} voor alle supplierinfos van partner."""
    sis = odoo.call(
        "product.supplierinfo", "search_read",
        [[["partner_id", "=", partner_id]]],
        {"fields": ["id", "product_tmpl_id", "product_code", "price"]},
    )
    index: dict[str, dict] = {}
    tmpl_ids = []
    for s in sis:
        code = (s.get("product_code") or "").strip()
        if not code or code in index:
            continue
        tid = s["product_tmpl_id"][0] if s.get("product_tmpl_id") else None
        index[code] = {"supplierinfo_id": s["id"], "tmpl_id": tid,
                       "supplier_price": s.get("price")}
        if tid:
            tmpl_ids.append(tid)

    # template-velden in batches ophalen
    by_tid = {}
    for i in range(0, len(tmpl_ids), 200):
        chunk = tmpl_ids[i:i + 200]
        for t in odoo.read("product.template", chunk,
                           ["name", "list_price", "standard_price", "active"]):
            by_tid[t["id"]] = t
    for code, e in index.items():
        t = by_tid.get(e["tmpl_id"], {})
        e["name"] = t.get("name", "")
        e["list_price"] = t.get("list_price")
        e["standard_price"] = t.get("standard_price")
        e["active"] = t.get("active", True)
    return index


# ---------------------------------------------------------------------------
# Foto
# ---------------------------------------------------------------------------
def download_image_b64(url: str, timeout: int = 30) -> Optional[str]:
    """Download een afbeelding en geef base64 (zoals Odoo image_1920 verwacht)."""
    if not url:
        return None
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200 and r.content:
            ctype = r.headers.get("Content-Type", "")
            if "image" in ctype or len(r.content) > 500:
                return base64.b64encode(r.content).decode("ascii")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Aanmaken / updaten
# ---------------------------------------------------------------------------
def create_product(odoo, partner_id: int, code: str, name: str,
                   cost: Optional[float], list_price: Optional[float],
                   image_b64: Optional[str] = None,
                   categ_id: Optional[int] = None,
                   description: str = "") -> int:
    """Maak product.template + supplierinfo aan. Returns template id."""
    vals = {
        "name": (name or code).strip(),
        "default_code": code.strip(),
        "type": "consu",
        "is_storable": True,
    }
    if cost is not None:
        vals["standard_price"] = cost
    if list_price is not None:
        vals["list_price"] = list_price
    if categ_id:
        vals["categ_id"] = categ_id
    if image_b64:
        vals["image_1920"] = image_b64
    if description:
        vals["description_sale"] = description
    tid = odoo.create("product.template", vals)
    odoo.create("product.supplierinfo", {
        "partner_id": partner_id,
        "product_tmpl_id": tid,
        "product_code": code.strip(),
        "price": cost if cost is not None else 0.0,
        "min_qty": 1, "delay": 1,
    })
    return tid


def update_cost(odoo, supplierinfo_id: int, cost: float) -> bool:
    return odoo.write("product.supplierinfo", [int(supplierinfo_id)],
                      {"price": float(cost)})


def update_list_price(odoo, tmpl_id: int, price: float) -> bool:
    return odoo.write("product.template", [int(tmpl_id)],
                      {"list_price": float(price)})


def update_image(odoo, tmpl_id: int, image_b64: str) -> bool:
    return odoo.write("product.template", [int(tmpl_id)],
                      {"image_1920": image_b64})


def update_description(odoo, tmpl_id: int, description: str) -> bool:
    return odoo.write("product.template", [int(tmpl_id)],
                      {"description_sale": description})


# ---------------------------------------------------------------------------
# Kenmerken als attributen (no-variant)
# ---------------------------------------------------------------------------
def set_specs_attributes(odoo, tmpl_id: int, specs: dict) -> int:
    """Zet kenmerken als product-attributen op de template.

    Maakt ontbrekende product.attribute (create_variant='no_variant') en
    product.attribute.value records aan en koppelt ze via
    product.template.attribute.line. Returns aantal gezette kenmerken.

    LET OP: verifieer dit tegen je bestaande Victron-producten — als die een
    specifieke attribuut-naamgeving of -categorie gebruiken, stem die hier af.
    """
    if not specs:
        return 0
    n = 0
    for key, value in specs.items():
        key, value = str(key).strip(), str(value).strip()
        if not key or not value:
            continue
        # attribuut zoeken/aanmaken
        attr = odoo.search_read("product.attribute", [["name", "=", key]],
                                ["id"], 1)
        if attr:
            attr_id = attr[0]["id"]
        else:
            attr_id = odoo.create("product.attribute",
                                  {"name": key, "create_variant": "no_variant"})
        # waarde zoeken/aanmaken
        val = odoo.search_read("product.attribute.value",
                               [["name", "=", value], ["attribute_id", "=", attr_id]],
                               ["id"], 1)
        if val:
            val_id = val[0]["id"]
        else:
            val_id = odoo.create("product.attribute.value",
                                 {"name": value, "attribute_id": attr_id})
        # bestaande lijn op deze template?
        line = odoo.search_read(
            "product.template.attribute.line",
            [["product_tmpl_id", "=", tmpl_id], ["attribute_id", "=", attr_id]],
            ["id"], 1)
        if line:
            odoo.write("product.template.attribute.line", [line[0]["id"]],
                       {"value_ids": [(4, val_id)]})
        else:
            odoo.create("product.template.attribute.line",
                        {"product_tmpl_id": tmpl_id, "attribute_id": attr_id,
                         "value_ids": [(6, 0, [val_id])]})
        n += 1
    return n
