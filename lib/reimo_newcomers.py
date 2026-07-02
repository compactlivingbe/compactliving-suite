"""
Reimo "Nieuwigheden" scraper — publieke Shopware-webshop (geen login nodig).

Loopt https://www.reimo.com/nl/nieuwigheden/ pagina per pagina af (?p=N,
12 producten per pagina) en parseert per productkaart:
  artikelnr, naam, url, afbeelding, prijs, leverbaarheid, leverancier, omschrijving.

De CLI (`main`) voegt het resultaat samen met een cumulatief bestand
`data/reimo_newcomers.json`: elk product krijgt bij de eerste keer dat het
gezien wordt een `toegevoegd_op`-datum. Bestaande producten worden verrijkt
(prijs/leverbaarheid up-to-date, `laatst_gezien` bijgewerkt) maar nooit
verwijderd — zo blijft de historiek van oude rapporten bewaard en komen nieuwe
producten er met datum bij.

Gebruik:
  python lib/reimo_newcomers.py                      # merge in data/reimo_newcomers.json
  python lib/reimo_newcomers.py --out pad.json       # ander doelbestand
  python lib/reimo_newcomers.py --max-pages 5        # beperkt (test)
  python lib/reimo_newcomers.py --date 2026-07-01    # override "vandaag"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.reimo.com/nl/nieuwigheden/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "reimo_newcomers.json"

_PRICE_RE = re.compile(r"([0-9][0-9.\s]*,[0-9]{2}|[0-9]+)")
_CIRCLE_RE = re.compile(r"circle_(green|yellow|orange|red|grey|gray)")


def _clean(txt: str) -> str:
    return re.sub(r"\s+", " ", (txt or "")).strip()


def _norm_image(url: str) -> str:
    """Maak beeld-URL absoluut; geef "" terug voor placeholders (no-picture)."""
    if not url:
        return ""
    if "no-picture" in url or "no_picture" in url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://www.reimo.com" + url
    return url


def _first_srcset_url(val: str) -> str:
    """Neem de eerste (of grootste) URL uit een srcset/normale src-waarde."""
    if not val:
        return ""
    parts = [p.strip() for p in val.split(",") if p.strip()]
    best = ""
    best_w = -1
    for p in parts:
        bits = p.split()
        url = bits[0]
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                w = int(bits[1][:-1])
            except ValueError:
                w = 0
        if w >= best_w:
            best_w, best = w, url
    return best or (parts[0].split()[0] if parts else "")


def parse_price(price_block) -> tuple[str, float | None]:
    """Geef (weergave, numerieke waarde) van een `.product--price` blok."""
    if price_block is None:
        return "", None
    node = price_block.select_one(".price--default") or price_block
    raw = _clean(node.get_text(" ", strip=True))
    # verwijder "*" en dubbele spaties, behoud "€ 54,90"
    disp = raw.replace("*", "").strip()
    val = None
    m = _PRICE_RE.search(raw.replace("\xa0", " "))
    if m:
        num = m.group(1).replace(" ", "").replace(".", "").replace(",", ".")
        try:
            val = float(num)
        except ValueError:
            val = None
    return disp, val


def parse_box(box) -> dict | None:
    """Parseer één `div.product--box` naar een dict, of None als onbruikbaar."""
    artikelnr = _clean(box.get("data-ordernumber", ""))
    if not artikelnr:
        num_el = box.select_one(".product--number")
        artikelnr = _clean(num_el.get_text()) if num_el else ""
    if not artikelnr:
        return None

    # titel + url
    title_el = box.select_one(".product--title")
    naam = _clean(title_el.get_text()) if title_el else ""
    url = ""
    if title_el and title_el.name == "a" and title_el.get("href"):
        url = title_el["href"]
    img_link = box.select_one("a.product--image")
    if img_link:
        if not url:
            url = img_link.get("href", "")
        if not naam:
            naam = _clean(img_link.get("title", ""))

    # afbeelding
    afbeelding = ""
    img = box.select_one(".product--image img") or box.select_one("img")
    if img:
        afbeelding = _first_srcset_url(
            img.get("srcset") or img.get("data-srcset") or ""
        ) or img.get("data-src") or img.get("src", "")
    afbeelding = _norm_image(afbeelding)

    # prijs
    prijs, prijs_waarde = parse_price(box.select_one(".product--price"))

    # leverbaarheid
    delivery = box.select_one(".product--delivery")
    leverbaarheid = ""
    voorraad_kleur = ""
    if delivery:
        short = delivery.select_one(".delivery-info--short")
        lng = delivery.select_one(".delivery-info--long")
        leverbaarheid = _clean((short or lng).get_text()) if (short or lng) else ""
        cls = " ".join(delivery.get("class", []))
        circle = delivery.select_one("[class*='circle_']")
        cls_full = cls + " " + (" ".join(circle.get("class", [])) if circle else "")
        m = _CIRCLE_RE.search(cls_full)
        if m:
            voorraad_kleur = m.group(1).replace("gray", "grey")

    # leverancier (merk)
    leverancier = ""
    sup = box.select_one(".product--supplier img")
    if sup:
        leverancier = _clean(sup.get("alt", ""))

    # omschrijving
    desc_el = box.select_one(".product--description")
    omschrijving = _clean(desc_el.get_text()) if desc_el else ""

    return {
        "artikelnr": artikelnr,
        "naam": naam,
        "url": url,
        "afbeelding": afbeelding,
        "prijs": prijs,
        "prijs_waarde": prijs_waarde,
        "leverbaarheid": leverbaarheid,
        "voorraad_kleur": voorraad_kleur,
        "leverancier": leverancier,
        "omschrijving": omschrijving,
    }


def fetch_page(session: requests.Session, page: int, timeout: int = 30) -> list[dict]:
    """Haal één listing-pagina op en geef de geparste producten terug."""
    params = {"p": page}
    if page > 1:
        params["loadMore"] = ""  # Shopware AJAX-partial-vlag
    r = session.get(BASE_URL, params=params, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for box in soup.select(".product--box"):
        p = parse_box(box)
        if p:
            out.append(p)
    return out


def fetch_newcomers(max_pages: int = 130, delay: float = 0.5, log=print) -> list[dict]:
    """Loop alle nieuwigheden-pagina's af. Stopt bij een lege pagina of wanneer
    een pagina enkel al-geziene artikelnrs oplevert."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Accept-Language"] = "nl-BE,nl;q=0.9"
    products: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        try:
            items = fetch_page(session, page)
        except Exception as e:  # noqa: BLE001
            log(f"  pagina {page} FOUT: {e}")
            break
        if not items:
            log(f"  pagina {page}: 0 producten — stop.")
            break
        new = 0
        for it in items:
            if it["artikelnr"] not in products:
                products[it["artikelnr"]] = it
                new += 1
        log(f"  pagina {page}: {len(items)} kaarten, {new} nieuw (totaal {len(products)})")
        if new == 0:
            log(f"  pagina {page}: enkel duplicaten — stop.")
            break
        time.sleep(delay)
    return list(products.values())


def merge_store(store: dict, scraped: list[dict], today: str) -> dict:
    """Voeg scrape-resultaat samen met cumulatief bestand (in-place-ish)."""
    products = store.get("products") or {}
    seen_now = set()
    for it in scraped:
        code = it["artikelnr"]
        seen_now.add(code)
        if code in products:
            rec = products[code]
            # verrijk met actuele waarden, behoud toegevoegd_op
            rec.update({
                "naam": it["naam"] or rec.get("naam", ""),
                "url": it["url"] or rec.get("url", ""),
                "afbeelding": it["afbeelding"] or rec.get("afbeelding", ""),
                "prijs": it["prijs"] or rec.get("prijs", ""),
                "prijs_waarde": it["prijs_waarde"] if it["prijs_waarde"] is not None else rec.get("prijs_waarde"),
                "leverbaarheid": it["leverbaarheid"] or rec.get("leverbaarheid", ""),
                "voorraad_kleur": it["voorraad_kleur"] or rec.get("voorraad_kleur", ""),
                "leverancier": it["leverancier"] or rec.get("leverancier", ""),
                "omschrijving": it["omschrijving"] or rec.get("omschrijving", ""),
                "laatst_gezien": today,
                "op_nieuwigheden": True,
            })
        else:
            rec = dict(it)
            rec["toegevoegd_op"] = today
            rec["laatst_gezien"] = today
            rec["op_nieuwigheden"] = True
            products[code] = rec
    # producten die niet meer op de nieuwigheden-lijst staan: markeren, niet wissen
    for code, rec in products.items():
        if code not in seen_now:
            rec["op_nieuwigheden"] = False
    store["products"] = products
    store["updated"] = today
    store["laatste_scrape_aantal"] = len(scraped)
    return store


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Doelbestand (JSON)")
    ap.add_argument("--max-pages", type=int, default=130)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--date", help="Override datum (YYYY-MM-DD), standaard vandaag")
    args = ap.parse_args()

    today = args.date or date.today().isoformat()

    def log(msg):
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)

    log(f"Reimo nieuwigheden scrapen (max {args.max_pages} pagina's)...")
    scraped = fetch_newcomers(max_pages=args.max_pages, delay=args.delay, log=log)
    log(f"Gescrapet: {len(scraped)} unieke producten.")

    out_path = Path(args.out)
    store = {}
    if out_path.exists():
        try:
            store = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log(f"Bestaand bestand onleesbaar ({e}) — start met leeg bestand.")
            store = {}

    prev = len(store.get("products", {}))
    store = merge_store(store, scraped, today)
    now = len(store["products"])
    nieuw = sum(1 for r in store["products"].values() if r.get("toegevoegd_op") == today)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Opgeslagen: {out_path}")
    log(f"Totaal {now} producten (+{now - prev} nieuw t.o.v. bestand, "
        f"{nieuw} met toegevoegd_op={today}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
