"""
Gedeelde logica voor sale_line_warn_msg: een handmatige 'EIGEN NOTITIE' bovenaan
(bewaard over syncs heen) met daaronder de automatische leverbaarheid per
leverancier (Reimo / VBD), elk onder een duidelijke kop.

Gebruikt door de Reimo-sync (lib/reimo_scraper.py) en de VBD-pagina.
"""

MANUAL_HEADER = "📝 ─── EIGEN NOTITIE ───"


def _delim(label: str) -> str:
    return f"📦 ─── {label} (automatisch — niet bewerken) ───"


REIMO_DELIM = _delim("REIMO LEVERBAARHEID")
VBD_DELIM = _delim("VBD LEVERBAARHEID")

# Alle bekende leverbaarheids-scheidingslijnen (incl. oude) om het handmatige
# deel correct af te splitsen, ongeacht welke leverancier de tekst schreef.
_ALL_DELIMS = (
    REIMO_DELIM, VBD_DELIM,
    "———— Reimo beschikbaarheid (automatisch — niet bewerken) ————",
)
# Starts van 'niet-handmatige' inhoud (oude losse meldingen zonder kop).
_AVAIL_START = (
    "🚫 NIET LEVERBAAR", "⚠️ Beperkt leverbaar", "⚠️ Beschikbaarheid",
    "⚠ Niet meer beschikbaar", *_ALL_DELIMS,
)


def extract_manual(current: str) -> str:
    """Haal het handmatige deel uit sale_line_warn_msg: alles boven de leverbaarheid,
    zonder de EIGEN NOTITIE-kop. Inhoud die enkel sync-/leverbaarheid is → ''."""
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
    if cur.startswith(MANUAL_HEADER):
        cur = cur[len(MANUAL_HEADER):].lstrip("\n")
    return cur.strip()


def compose(manual: str, availability: str, delim: str):
    """Bouw sale_line_warn_msg: EIGEN NOTITIE (met kop) + leverbaarheid (met kop)."""
    manual = (manual or "").strip()
    availability = (availability or "").strip()
    parts = []
    if manual:
        parts.append(f"{MANUAL_HEADER}\n{manual}")
    if availability:
        parts.append(f"{delim}\n{availability}")
    return "\n\n".join(parts) or False
