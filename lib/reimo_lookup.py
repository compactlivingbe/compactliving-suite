"""
Gecombineerde Reimo-artikelopzoeking voor de 'product toevoegen'-tool.

Bronnen:
  - Profiweb (dealer portal, login vereist): naam (DE), EAN/barcode, Händlerpreis
    (= inkoopprijs), beschikbaarheid. Werkt voor elk artikelnummer.
  - reimo_newcomers.json (van de wekelijkse scraper): Nederlandse naam, foto,
    VK-verkoopprijs, categorie, url — voor producten die in het rapport staan.

De pagina houdt zelf een Profiweb-sessie bij (login is traag); deze module
levert enkel de losse bouwstenen + een merge-functie.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NEWCOMERS_PATH = REPO_ROOT / "data" / "reimo_newcomers.json"


def to_float(s) -> float | None:
    """'29,11 €' / '1.234,50' -> 29.11 / 1234.50."""
    if s is None:
        return None
    m = re.search(r"[0-9][0-9.\s]*,[0-9]{2}|[0-9]+", str(s).replace("\xa0", " "))
    if not m:
        return None
    num = m.group(0).replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(num)
    except ValueError:
        return None


def from_newcomers(artikelnr: str) -> dict | None:
    """Haal het product uit reimo_newcomers.json (indien aanwezig)."""
    if not NEWCOMERS_PATH.exists():
        return None
    try:
        store = json.loads(NEWCOMERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return (store.get("products") or {}).get(str(artikelnr).strip())


def combine(artikelnr: str, pw_info: dict | None, nc: dict | None) -> dict:
    """Voeg Profiweb- en shop-data samen tot één prefill-dict.

    Voorkeur: Nederlandse naam/foto/VK uit de shop; EAN + inkoop uit Profiweb.
    """
    artikelnr = str(artikelnr).strip()
    pw_info = pw_info or {}
    nc = nc or {}

    naam = (nc.get("naam") or "").strip() or (pw_info.get("title") or "").strip()
    barcode = re.sub(r"\D", "", str(pw_info.get("ean") or ""))[:14]
    inkoop = to_float(pw_info.get("haendler_price"))
    verkoop = nc.get("prijs_waarde")
    if verkoop is None:
        verkoop = to_float(nc.get("prijs"))

    foto = nc.get("afbeelding") or ""
    foto_groot = foto.replace("/w200/", "/full/") if foto else ""

    return {
        "artikelnr": artikelnr,
        "naam": naam,
        "barcode": barcode,
        "inkoop": inkoop,
        "verkoop": verkoop,
        "foto": foto_groot or foto,
        "categorie": nc.get("categorie") or "",
        "categorie_pad": nc.get("categorie_pad") or "",
        "url": nc.get("url") or "",
        "omschrijving": nc.get("omschrijving") or "",
        "beschikbaarheid": pw_info.get("verfuegbarkeit") or nc.get("leverbaarheid") or "",
        "profiweb_gevonden": bool(pw_info.get("found")),
        "in_rapport": bool(nc),
    }
