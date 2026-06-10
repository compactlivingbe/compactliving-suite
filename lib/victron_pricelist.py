"""Parser voor de officiële Victron-prijslijst (PDF) → master-catalogus.

De prijslijst is dé bron van waarheid voor "welke Victron-producten bestaan",
ook als geen enkele leverancier ze voert. Per product geeft de PDF: artikelcode,
naam, categorie, afmetingen, gewicht en de adviesprijs (ex BTW). Foto's,
EAN's en kenmerken zitten hier NIET in — die komen van de leveranciers of
worden handmatig aangevuld.

Structuur van de PDF (geverifieerd op Pricelist_Victron_EUR_C_2026-Q2):
  - Categorie-koppen: regels die eindigen op "Ex VAT" (bv. "INVERTERS Ex VAT",
    "INVERTER/CHARGERS Ex VAT") of volledig in hoofdletters staan
    (bv. "BLUE SMART BATTERY CHARGERS IP65 230V").
  - Productregels: <naam> <CODE> <H x W x D> [√ √] <kg> € <prijs>
      bv. "MultiPlus 12/800/35-16 PMP121800000 360 x 240 x 100 √ √ 6,4 € 425,00"
  - Codes: 2-4 hoofdletters + 6-9 cijfers + optioneel achtervoegsel-letter
      (PIN121251200, CMP122200000, QUA123020010, BPC120134034R, SDTG2400301).
  - Prijzen in Europees formaat: € 1.267,00.

Door op de CODE te ankeren negeren we automatisch kop-, kolom- en de in de
PDF "uitgelekte" afbeeldingsbijschriften (die geen code bevatten). Bij regels
waar een bijschrift vóór de echte naam lekt, kan de naam licht vervuild zijn;
de code/prijs/afmetingen blijven betrouwbaar.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

# Artikelcode: 2-4 hoofdletters, 6-9 cijfers, optioneel 1 trailing letter.
CODE_RE = re.compile(r"\b([A-Z]{2,4}\d{6,9}[A-Z]?)\b")
# Volledige-match variant (één los woord = precies een code).
CODE_FULL_RE = re.compile(r"^[A-Z]{2,4}\d{6,9}[A-Z]?$")
# "Nieuw"-markering in de Victron-prijslijst: een icoon-font glyph (geen tekst).
# Staat als los symbool náást de productregel, niet op dezelfde tekstregel.
NEW_GLYPH = chr(0xF0AB)
DIM_RE = re.compile(r"(\d+)\s*[x×]\s*(\d+)\s*[x×]\s*(\d+)")
PRICE_RE = re.compile(r"€\s*([\d.]*\d,\d{2})")
# Gewicht: getal (met optionele decimaal-komma) net vóór het euroteken.
WEIGHT_RE = re.compile(r"(\d+(?:,\d+)?)\s*€")

# Regels die nooit een categorie zijn, ook al staan ze in hoofdletters.
_SKIP_HEADER = re.compile(r"H\s*x\s*W\s*x\s*D|SINEWAVE|EX VAT EURO|PRICELIST")


def _eu_price(txt: str) -> Optional[float]:
    """'1.267,00' → 1267.0 ; '425,00' → 425.0."""
    if not txt:
        return None
    t = txt.strip().replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _is_category_header(line: str) -> Optional[str]:
    """Geef de categorienaam als de regel een kop is, anders None."""
    s = line.strip()
    if not s or CODE_RE.search(s):
        return None
    up = s.upper()
    # Expliciete "... Ex VAT"-kop
    if up.endswith("EX VAT"):
        cat = re.sub(r"Ex VAT\s*$", "", s, flags=re.I).strip()
        return cat or None
    # Volledig in hoofdletters (geen kleine letters), redelijk lang, geen
    # kolom-/documentkop.
    letters = [c for c in s if c.isalpha()]
    if letters and not any(c.islower() for c in s) and len(s) > 3:
        if _SKIP_HEADER.search(up):
            return None
        # niet enkel cijfers/symbolen
        if sum(c.isalpha() for c in s) >= 3:
            return s
    return None


def parse_text(text: str, log: Callable[[str], None] = print) -> dict[str, dict]:
    """Parse de volledige PDF-tekst tot {code: product-dict}."""
    products: dict[str, dict] = {}
    category = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        cat = _is_category_header(line)
        if cat:
            category = cat
            continue

        m = CODE_RE.search(line)
        if not m:
            continue
        code = m.group(1)

        name = line[:m.start()].strip(" -–·")
        rest = line[m.end():]

        dim = DIM_RE.search(rest)
        dimensions = ""
        if dim:
            dimensions = f"{dim.group(1)} x {dim.group(2)} x {dim.group(3)}"

        price = None
        pm = PRICE_RE.search(rest)
        if pm:
            price = _eu_price(pm.group(1))

        weight = None
        wm = WEIGHT_RE.search(rest)
        if wm:
            try:
                weight = float(wm.group(1).replace(",", "."))
            except ValueError:
                weight = None

        # Eerste voorkomen wint (PDF kan codes herhalen in bijschriften).
        if code in products:
            # Vul ontbrekende prijs aan als die nu wel gevonden is.
            if products[code].get("advice_price") is None and price is not None:
                products[code]["advice_price"] = price
            continue

        products[code] = {
            "code": code,
            "name": name,
            "brand": "Victron",
            "category": category,
            "dimensions": dimensions,
            "weight": weight,
            "advice_price": price,          # adviesprijs EX BTW
            "is_new": False,                # gezet door _find_new_codes (PDF-glyph)
            "source": "victron_pricelist",
        }

    log(f"Victron-prijslijst: {len(products)} producten geparset "
        f"({sum(1 for p in products.values() if p['advice_price'] is None)} zonder prijs)")
    return products


def _find_new_codes(pdf) -> set[str]:
    """Vind 'nieuw'-gemarkeerde codes via de icoon-glyph (U+F0AB).

    De glyph staat als los symbool náást een productregel. We matchen elke
    glyph op de code waarvan de verticale positie (bijna) gelijk is. De glyph
    bovenaan elke pagina (top < 60) is een legenda en wordt genegeerd.
    """
    found: set[str] = set()
    for pg in pdf.pages:
        try:
            words = pg.extract_words(use_text_flow=False)
        except Exception:
            continue
        coderows = [((w["top"] + w["bottom"]) / 2, w["text"])
                    for w in words if CODE_FULL_RE.match(w["text"])]
        if not coderows:
            continue
        glyphs = {(round(c["x0"]), round(c["top"]), round(c["bottom"]))
                  for c in pg.chars
                  if c.get("text") == NEW_GLYPH and c.get("top", 0) >= 60}
        for _gx, gt, gb in glyphs:
            gc = (gt + gb) / 2
            vc, code = min(coderows, key=lambda r: abs(r[0] - gc))
            if abs(vc - gc) < 14:        # zelfde regel (sub-punt nauwkeurig)
                found.add(code)
    return found


def _parse_open_pdf(pdf, log: Callable[[str], None] = print) -> dict[str, dict]:
    log(f"Victron-prijslijst: {len(pdf.pages)} pagina's")
    text = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
    prods = parse_text(text, log)
    new_codes = _find_new_codes(pdf)
    n = 0
    for code in new_codes:
        if code in prods:
            prods[code]["is_new"] = True
            n += 1
    log(f"Victron-prijslijst: {n} nieuwe producten gemarkeerd")
    return prods


def parse_pdf(path: str | Path, log: Callable[[str], None] = print) -> dict[str, dict]:
    """Open de PDF (pdfplumber) en parse alle pagina's."""
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        return _parse_open_pdf(pdf, log)


def parse_pdf_bytes(data: bytes, log: Callable[[str], None] = print) -> dict[str, dict]:
    """Parse een PDF die als bytes in het geheugen staat (Streamlit-upload)."""
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return _parse_open_pdf(pdf, log)


if __name__ == "__main__":
    import sys

    src = sys.argv[1] if len(sys.argv) > 1 else None
    if not src:
        print("gebruik: python victron_pricelist.py <pricelist.pdf> [out.json]")
        raise SystemExit(1)
    out = sys.argv[2] if len(sys.argv) > 2 else "data/master_catalog.json"

    prods = parse_pdf(src)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(prods, f, ensure_ascii=False, indent=2)
    print(f"geschreven: {out} ({len(prods)} producten)")
    # kleine sample
    for code in list(prods)[:5]:
        p = prods[code]
        print(f"  {code} | {p['category']} | {p['name'][:40]} | "
              f"{p['dimensions']} | {p['weight']} | € {p['advice_price']}")
