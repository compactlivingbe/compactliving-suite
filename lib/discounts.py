"""Korting-resolutie voor leveranciers waar de inkoopprijs uit de publieke prijs
volgt (zoals All-Spark, waar we uitgelogd scrapen).

Korting wordt als fractie opgeslagen (0.30 = 30%). Precedentie, van specifiek
naar algemeen:
    product  >  categorie  >  merk  >  default

Config-vorm (data/<leverancier>_discounts.json):
    {
      "default": 0.0,
      "by_brand":    {"Victron": 0.30, ...},
      "by_category": {"Inverters": 0.32, ...},
      "by_product":  {"PIN122122500": 0.35, ...}
    }
"""
from __future__ import annotations

from typing import Optional

EMPTY = {"default": 0.0, "by_brand": {}, "by_category": {}, "by_product": {}}


def empty_config() -> dict:
    return {"default": 0.0, "by_brand": {}, "by_category": {}, "by_product": {}}


def normalize(cfg: Optional[dict]) -> dict:
    cfg = dict(cfg or {})
    out = empty_config()
    out["default"] = float(cfg.get("default") or 0.0)
    for k in ("by_brand", "by_category", "by_product"):
        src = cfg.get(k) or {}
        out[k] = {str(key): float(val) for key, val in src.items()
                  if _is_num(val)}
    return out


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def resolve_discount(cfg: dict, code: str = "", brand: str = "",
                     category: str = "") -> tuple[float, str]:
    """Geef (korting_fractie, herkomst). Herkomst is 'product'/'category'/
    'brand'/'default' zodat de UI kan tonen waaróm een korting geldt."""
    cfg = normalize(cfg)
    if code and code in cfg["by_product"]:
        return cfg["by_product"][code], "product"
    if category and category in cfg["by_category"]:
        return cfg["by_category"][category], "category"
    if brand and brand in cfg["by_brand"]:
        return cfg["by_brand"][brand], "brand"
    return cfg["default"], "default"


def cost_from_public(public_price: Optional[float], discount: float) -> Optional[float]:
    """Inkoopprijs = publieke prijs × (1 − korting), op 2 decimalen."""
    if public_price is None:
        return None
    try:
        return round(float(public_price) * (1.0 - float(discount)), 2)
    except (TypeError, ValueError):
        return None
