"""
Gedeelde logica voor sale_line_warn_msg: een handmatige 'EIGEN NOTITIE' bovenaan
(bewaard over syncs heen) met daaronder de automatische leverbaarheid per
leverancier (Reimo / VBD), elk onder een duidelijke kop.

Odoo zet in de verkooporder-waarschuwing zelf "[code] Productnaam - " vóór de
tekst; daarom begint de samengestelde tekst met een newline zodat onze inhoud
op een eigen regel start.

Gebruikt door de Reimo-sync (lib/reimo_scraper.py) en de VBD-pagina.
"""

MANUAL_HEADER = "📝 EIGEN NOTITIE"
REIMO_DELIM = "📦 REIMO LEVERBAARHEID (automatisch — niet bewerken)"
VBD_DELIM = "📦 VBD LEVERBAARHEID (automatisch — niet bewerken)"

# Herken zowel de huidige als oudere kop-/scheidingsvarianten, zodat het
# handmatige deel altijd correct afgesplitst wordt (ook na format-wijzigingen).
_MANUAL_HEADERS = (MANUAL_HEADER, "📝 ─── EIGEN NOTITIE ───")
_ALL_DELIMS = (
    REIMO_DELIM, VBD_DELIM,
    "📦 ─── REIMO LEVERBAARHEID (automatisch — niet bewerken) ───",
    "📦 ─── VBD LEVERBAARHEID (automatisch — niet bewerken) ───",
    "———— Reimo beschikbaarheid (automatisch — niet bewerken) ————",
)
_AVAIL_START = (
    "🚫 NIET LEVERBAAR", "⚠️ Beperkt leverbaar", "⚠️ Beschikbaarheid",
    "⚠ Niet meer beschikbaar", *_ALL_DELIMS,
)


def extract_manual(current: str) -> str:
    """Haal het handmatige deel uit sale_line_warn_msg: alles boven de leverbaarheid,
    zonder de EIGEN NOTITIE-kop. Inhoud die enkel leverbaarheid is → ''."""
    cur = (current or "").strip()
    if not cur:
        return ""
    for d in _ALL_DELIMS:
        if d in cur:
            cur = cur.split(d, 1)[0].rstrip()
            break
    else:
        if cur.startswith(_AVAIL_START):
            return ""
    for h in _MANUAL_HEADERS:
        if cur.startswith(h):
            cur = cur[len(h):].lstrip("\n")
            break
    return cur.strip()


def compose(manual: str, availability: str, delim: str):
    """Bouw sale_line_warn_msg: EIGEN NOTITIE (met kop) + leverbaarheid (met kop).
    Begint met een newline zodat de inhoud onder Odoo's "[code] naam -" op een
    eigen regel staat."""
    manual = (manual or "").strip()
    availability = (availability or "").strip()
    parts = []
    if manual:
        parts.append(f"{MANUAL_HEADER}\n{manual}")
    if availability:
        parts.append(f"{delim}\n{availability}")
    body = "\n\n".join(parts)
    return ("\n" + body) if body else False
