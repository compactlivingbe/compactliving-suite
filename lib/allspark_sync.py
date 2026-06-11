"""All-Spark scrape — headless snapshot-vernieuwer voor de geplande (cron) run.

Doet hetzelfde als de "Scrape All-Spark"-knop op pagina 9, maar zonder UI:
  1. scrape de publieke All-Spark webshop (optioneel met categorie/merk-mapping);
  2. bereken de inkoopprijs per product via de korting-config
     (data/allspark_discounts.json);
  3. schrijf data/allspark_snapshot.json weg.

Er worden GEEN wijzigingen in Odoo gedaan — dit ververst enkel de snapshot zodat
de prijswijziging-/nieuw-detectie in de app actueel blijft. Beoordelen en
toepassen doe je daarna zelf in de Suite.

Gebruik:
  python lib/allspark_sync.py                 # volledige scrape (met categorieën)
  python lib/allspark_sync.py --no-categories # sneller, zonder merk/categorie-mapping
  python lib/allspark_sync.py --max-pages 5   # testmodus
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# lib/ aan het pad toevoegen zodat losse modules importeerbaar zijn,
# net als in de Streamlit-pagina's.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gh_storage as ghs          # noqa: E402
import discounts as disc          # noqa: E402
from suppliers import get as get_supplier  # noqa: E402

SNAPSHOT_FILE = "allspark_snapshot.json"
DISCOUNTS_FILE = "allspark_discounts.json"


def _log(msg: str) -> None:
    print(msg, flush=True)


def run(with_categories: bool = True, max_pages: int | None = None) -> int:
    supplier = get_supplier("allspark")

    products = supplier.fetch(log=_log, with_categories=with_categories,
                              max_pages=max_pages)
    if not products:
        _log("All-Spark: 0 producten gescrapet — snapshot NIET overschreven.")
        return 1

    # Inkoopprijs berekenen met de opgeslagen korting-config.
    cfg = disc.normalize(ghs.load_json(DISCOUNTS_FILE, default=disc.empty_config()))
    for code, p in products.items():
        d, src = disc.resolve_discount(cfg, code, p.get("brand", ""),
                                       p.get("category", ""))
        p["discount"] = d
        p["discount_source"] = src
        p["cost_price"] = disc.cost_from_public(p.get("public_price"), d)

    snap = {
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "products": products,
        "categories_tree": getattr(supplier, "last_category_tree", []),
    }
    pushed, info = ghs.save_json(
        SNAPSHOT_FILE, snap,
        f"All-Spark snapshot (cron) {len(products)} producten")
    _log(f"All-Spark: {len(products)} producten · snapshot opgeslagen "
         f"({'GitHub ' + info if pushed else 'lokaal'})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="All-Spark scrape → snapshot")
    ap.add_argument("--no-categories", action="store_true",
                    help="Sla de (trage) categorie/merk-mapping over.")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="Beperk het aantal hoofdlijst-pagina's (testmodus).")
    args = ap.parse_args()
    return run(with_categories=not args.no_categories, max_pages=args.max_pages)


if __name__ == "__main__":
    raise SystemExit(main())
