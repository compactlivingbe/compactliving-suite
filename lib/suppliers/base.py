"""Adapter-interface + registry voor leveranciers.

Een leverancier-adapter levert producten in een genormaliseerde vorm
(SupplierProduct). De rest van de Suite (diff, import naar Odoo, dashboard,
inkoop-optimalisatie) werkt uitsluitend met die vorm, zodat een nieuwe
leverancier toevoegen neerkomt op: subclass Supplier + register().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, TypedDict


class SupplierProduct(TypedDict, total=False):
    code: str                 # leverancier-/artikelcode (matcht Odoo product_code)
    name: str
    brand: str                # bv. "Victron"
    category: str             # leesbare categorie (diepste niveau)
    category_path: str        # volledige categorie-hiërarchie (voor diff/filter)
    public_price: float       # publieke prijs (incl. of excl. BTW — zie price_incl_vat)
    price_incl_vat: bool
    cost_price: float         # berekende inkoopprijs (na korting), kan None zijn
    image_url: str
    description: str
    specs: dict               # kenmerken -> waarde (worden Odoo-attributen)
    ean: str
    url: str


@dataclass
class Supplier:
    """Basisklasse. Subclass en implementeer fetch()."""
    key: str                          # stabiele sleutel, bv. "allspark"
    label: str                        # weergavenaam, bv. "All-Spark"
    partner_env: str = ""             # env-var met de Odoo res.partner id
    partner_default: Optional[int] = None
    price_incl_vat: bool = True       # publieke prijs incl. BTW?

    def fetch(self, log: Callable[[str], None] = print,
              **kwargs) -> dict[str, SupplierProduct]:
        """Haal de actuele productlijst op. Returns {code: SupplierProduct}."""
        raise NotImplementedError

    def compute_cost(self, product: SupplierProduct) -> Optional[float]:
        """Bereken de inkoopprijs voor één product. Default: publieke prijs."""
        return product.get("public_price")

    def partner_id(self, odoo=None) -> Optional[int]:
        """Odoo res.partner id van deze leverancier.
        Volgorde: env-var -> default -> live opzoeken op naam in Odoo."""
        import os
        if self.partner_env:
            v = os.environ.get(self.partner_env)
            if v and v.isdigit():
                return int(v)
        if self.partner_default:
            return self.partner_default
        if odoo is not None:
            try:
                res = odoo.search_read(
                    "res.partner",
                    [("supplier_rank", ">", 0), ("name", "ilike", self.label)],
                    ["id", "name"], 1)
                if res:
                    return res[0]["id"]
            except Exception:
                pass
        return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, Supplier] = {}


def register(supplier: Supplier) -> Supplier:
    _REGISTRY[supplier.key] = supplier
    return supplier


def get(key: str) -> Optional[Supplier]:
    return _REGISTRY.get(key)


def all_suppliers() -> list[Supplier]:
    return list(_REGISTRY.values())
