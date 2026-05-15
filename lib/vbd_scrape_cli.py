"""CLI scraper voor VBD Services - voor GitHub Actions runner.
Schrijft data/vbd_products.json met alle producten + timestamp."""
import argparse, json, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vbd_scraper import fetch_all, DEFAULT_CATEGORIES


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="data/vbd_products.json")
    p.add_argument("--delay", type=float, default=0.6)
    p.add_argument("--between-cats", type=float, default=1.5)
    p.add_argument("--categories", help="Custom categorie-paden (comma separated)")
    args = p.parse_args()

    cats = [c.strip() for c in args.categories.split(",")] if args.categories else DEFAULT_CATEGORIES

    def log(msg):
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)

    log(f"Scrape start ({len(cats)} categorieën, delay={args.delay}s, between_cats={args.between_cats}s)")
    products = fetch_all(cats, log=log, delay=args.delay, between_cats=args.between_cats)
    log(f"KLAAR: {len(products)} unieke producten")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(products),
        "categories": cats,
        "products": products,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Geschreven naar {out_path}")


if __name__ == "__main__":
    main()
