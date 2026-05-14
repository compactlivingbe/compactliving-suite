"""Reimo Profiweb checkout orderer - HTTP automation.

Flow gemapt uit echte HAR capture (mei 2026):
1. Login (form_anmelden -> POST naar r40msvcas400_call.pl)
2. Naviger naar Schnellbestellung (AKTION=NAVIGATION, schnittstelle=000070)
3. POST BEST_REG met alle items in één submit (max 10 per submit -- Reimo Quick Order beperkt)
4. Parse response voor Auftrag-Nr (= bestelreferentie)

Gebruik:
    o = ReimoOrderer(user="730478", password="ComLiv78")
    o.login()
    aunr = o.place_order([("31650", 2), ("ASS030064900", 1)],
                         kommission="PO00123",
                         email="leveranciers@compactliving.be")
    # aunr = "1707127" (Reimo bestelnummer)
"""
import re
import requests
from datetime import date
from bs4 import BeautifulSoup

PROFIWEB_LOGIN_URL = (
    "https://profiweb.reimo.com/cgi-bin/r40msvc_menue.pl"
    "?var_hauptpfad=../r40/vc_reimo/"
    "&var_html_folgemaske=reimo_index.html"
    "&var_fa1_select=var_fa1_select||16|"
    "&var_sprache_select=var_sprache_select||DE|"
    "&var_sprache=var_sprache||DE|"
)
CALL_URL = "https://profiweb.reimo.com/cgi-bin/r40msvcas400_call.pl"

MAX_LINES_PER_ORDER = 10  # Reimo Schnellbestellung limit


class ReimoOrderError(Exception):
    pass


class ReimoOrderer:
    def __init__(self, user, password, log=print):
        self.user = (user or "")[:8]
        self.password = (password or "")[:8]
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 CompactLiving/1.0 (autoorder)"
        self.log = log
        self.transaktionsnr = None    # bewaard tussen requests
        self.last_response_html = None

    # ---------- LOGIN ----------
    def login(self):
        r = self.s.get(PROFIWEB_LOGIN_URL, timeout=30); r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form", {"name": "form_anmelden"})
        if not form:
            raise ReimoOrderError("Login form niet gevonden")
        data = {inp.get("name"): inp.get("value", "")
                for inp in form.find_all("input") if inp.get("name")}
        data["USER"] = self.user
        data["PASS"] = self.password
        action = (form.get("action") or CALL_URL).strip()
        url = "https://profiweb.reimo.com" + action if action.startswith("/") else action
        r = self.s.post(url, data=data, timeout=30); r.raise_for_status()
        if "form_anmelden" in r.text and 'name="USER"' in r.text:
            raise ReimoOrderError("Login mislukt - verkeerde USER/PASS")
        self.last_response_html = r.text
        self._extract_transaktionsnr(r.text)
        self.log("Profiweb login OK")

    def _extract_transaktionsnr(self, html):
        """Pakt var_transaktionsnr uit de huidige pagina voor latere POSTs."""
        m = re.search(r'name="var_transaktionsnr"\s+(?:type="hidden"\s+)?value="([^"]*)"', html)
        if m:
            self.transaktionsnr = m.group(1)
            return
        # Variant: value before name
        m = re.search(r'value="([^"]*)"\s+name="var_transaktionsnr"', html)
        if m:
            self.transaktionsnr = m.group(1)

    # ---------- ARTIKEL TOEVOEGEN AAN WINKELMANDJE ----------
    def add_to_cart(self, code, qty):
        """Voegt 1 item toe aan het Profiweb winkelmandje (Bestellung lijst).
        Equivalent van klik op 'Bestellen' knop op de Artikel-Details pagina.
        Items zijn dan zichtbaar voor user die in Profiweb inlogt onder dezelfde account.
        """
        # Stap A: open de artikel-detail pagina (om var_transaktionsnr te krijgen)
        # Hergebruik bestaande pagina als die al goed staat
        if not self.last_response_html or "form_artikelanzeige_suche" not in self.last_response_html:
            # Nav naar artikeldetails
            soup = BeautifulSoup(self.last_response_html or "", "html.parser")
            nav = soup.find("form", {"name": "form_navigation_artikel_detail_info"})
            if nav:
                nav_data = {inp.get("name"): inp.get("value", "")
                            for inp in nav.find_all("input") if inp.get("name")}
                action = (nav.get("action") or CALL_URL).strip()
                url = "https://profiweb.reimo.com" + action if action.startswith("/") else action
                r = self.s.post(url, data=nav_data, timeout=30); r.raise_for_status()
                self.last_response_html = r.text
                self._extract_transaktionsnr(r.text)

        # Stap B: zoek artikel op (ARTIKEL_ANZEIGEN)
        data = {
            "var_schnittstelle": "000120",
            "var_hauptpfad": "../r40/easyweb400/kunde_reimo/",
            "var_folgemaske": "reimo_artikel_detail_info.html",
            "var_transaktionsnr": self.transaktionsnr or "",
            "var_sprache": "DE",
            "var_htmlpruefliste": "ARTNR|ADMENGE",
            "var_liste_zahlenfelder": "ADMENGE=5=0",
            "AKTION": "ARTIKEL_ANZEIGEN",
            "ARTNR": str(code).strip(),
            "ADMENGE": str(int(qty)),
            "ANZ_PREIS": "J",
        }
        r = self.s.post(CALL_URL, data=data, timeout=30); r.raise_for_status()
        self.last_response_html = r.text
        self._extract_transaktionsnr(r.text)

        # Stap C: klik "Bestellen" (= ARTIKEL_BESTELLEN, voegt toe aan lijst)
        data["AKTION"] = "ARTIKEL_BESTELLEN"
        r = self.s.post(CALL_URL, data=data, timeout=30); r.raise_for_status()
        self.last_response_html = r.text
        self._extract_transaktionsnr(r.text)
        self.log(f"  + toegevoegd aan winkelmandje: {code} x {qty}")

    # ---------- NAVIGATIE NAAR SCHNELLBESTELLUNG ----------
    def goto_schnellbestellung(self):
        """Open de Schnellbestellung pagina (AKTION=NAVIGATION, schnittstelle=000070)."""
        # We zoeken naar form_navigation_suche_schnellbestellung in de huidige pagina
        # (zit in de menu sidebar van elke ingelogde pagina)
        soup = BeautifulSoup(self.last_response_html or "", "html.parser")
        form = soup.find("form", {"name": "form_navigation_suche_schnellbestellung"})
        if not form:
            raise ReimoOrderError("form_navigation_suche_schnellbestellung niet gevonden -- niet ingelogd?")
        data = {inp.get("name"): inp.get("value", "")
                for inp in form.find_all("input") if inp.get("name")}
        action = (form.get("action") or CALL_URL).strip()
        url = "https://profiweb.reimo.com" + action if action.startswith("/") else action
        r = self.s.post(url, data=data, timeout=30); r.raise_for_status()
        self.last_response_html = r.text
        self._extract_transaktionsnr(r.text)
        if "Schnellbestellung" not in r.text and "schnellbestellung" not in r.text.lower():
            self.log("WAARSCHUWING: response bevat geen 'Schnellbestellung' tekst")
        self.log("Schnellbestellung pagina geopend")

    # ---------- BESTELLEN ----------
    def place_order(self, items, kommission="", email="", abholdatum=None,
                    bemerkung="", versand="SOFORT", dry_run=False):
        """Plaats een bestelling.

        items: list of (artnr, qty) tuples. Max 10 per call (Reimo Schnellbestellung).
        kommission: vrije tekst (bv. Odoo PO nummer) -- komt op de bestelbon.
        email: leveringsbevestiging adres.
        abholdatum: 'DD.MM.YY' (default = vandaag).
        versand: 'SOFORT' / 'TEILLIEFERUNG' / etc.
        dry_run: doe alles BEHALVE de finale BEST_REG (geen echte order).

        Returns: Auftrag-Nr als string (bv. '1707127').
        """
        if not items:
            raise ReimoOrderError("Geen items in bestelling")
        if len(items) > MAX_LINES_PER_ORDER:
            raise ReimoOrderError(
                f"Max {MAX_LINES_PER_ORDER} items per bestelling (kreeg {len(items)}). "
                f"Splits in meerdere orders.")

        if not abholdatum:
            abholdatum = date.today().strftime("%d.%m.%y")

        # Stap 1: voeg elk item toe aan winkelmandje (zichtbaar in Profiweb voor user)
        self.log(f"Items toevoegen aan Profiweb winkelmandje ({len(items)}):")
        for code, qty in items:
            try:
                self.add_to_cart(code, qty)
            except Exception as e:
                self.log(f"  ✗ {code} faalde: {e}")

        if dry_run:
            self.log(f"DRY RUN klaar: {len(items)} items zitten nu in Profiweb winkelmandje.")
            self.log(f"   Login op profiweb.reimo.com → Bestellung → Schnellbestellung om te zien.")
            self.log(f"   Geen echte order geplaatst (BEST_REG overgeslagen).")
            return "DRY_RUN_ITEMS_IN_CART"

        # Stap 2: open Schnellbestellung pagina (om var_transaktionsnr te refreshen)
        self.goto_schnellbestellung()

        # Stap 2: parse current form to get hidden fields + structure
        soup = BeautifulSoup(self.last_response_html, "html.parser")
        # The form name varies — try common ones
        form = (soup.find("form", {"name": "form_artikelanzeige_suche"}) or
                soup.find("form", attrs={"name": re.compile(r".*schnellbest.*", re.I)}) or
                soup.find("form"))
        base_data = {}
        if form:
            for inp in form.find_all("input"):
                name = inp.get("name")
                if name:
                    base_data[name] = inp.get("value", "")

        # Stap 3: Bouw BEST_REG payload
        # Gebaseerd op echte HAR capture (mei 2026)
        data = {
            "var_schnittstelle": "000070",
            "var_hauptpfad": "../r40/easyweb400/kunde_reimo/",
            "var_folgemaske": "reimo_suche_schnellbestellung.html",
            "var_anzahl_zeilen": "00010",
            "var_transaktionsnr": self.transaktionsnr or "",
            "var_datumprf8": "ABHOLDATUM|ABHOLDATUM2|TERMIN1|TERMIN2|TERMIN3|TERMIN4|TERMIN5|TERMIN6|TERMIN7|TERMIN8|TERMIN9|TERMIN10",
            "var_liste_zahlenfelder": "AUNR=7=0|MENGE=9=0|MENGE1=9=0|MENGE2=9=0|MENGE3=9=0|MENGE4=9=0|MENGE5=9=0|MENGE6=9=0|MENGE7=9=0|MENGE8=9=0|MENGE9=9=0|MENGE10=9=0|",
            "var_sprache": "DE",
            "var_back_key": "001",
            "var_first_key": "001",
            "var_nextkey": "011",
            "AKTION": "BEST_REG",
            "MENGE": "1",
            "ABHOLDATUM": abholdatum,
            "ABHOLDATUM2": abholdatum,
            "BUENDELUNG": "N",
            "ANZ_EK_PREIS": "J",
            "VADR_NUMMER": "0000000",
            "VADR_LAND": "B  ",
            "VADR_AVIS": "M",
            "VERSAND": versand,
            "MAILADRESSE": email or "",
            "KOMM": kommission,
            "BEMERK": bemerkung,
        }

        # Item lijnen APOS1..APOS10
        for idx in range(1, MAX_LINES_PER_ORDER + 1):
            apos = f"{idx:03d}"
            data[f"APOS{idx}"] = apos
            if idx <= len(items):
                code, qty = items[idx - 1]
                data[f"ARTNR{idx}"] = str(code)
                data[f"MENGE{idx}"] = str(int(qty))
                data[f"RABATT{idx}"] = "   0,00"
            else:
                data[f"MENGE{idx}"] = " "
                data[f"RABATT{idx}"] = "   0,00"

        # Stap 4: POST BEST_REG (= echte commit, items uit cart worden order)
        self.log(f"BEST_REG: {len(items)} items, abholdatum={abholdatum}, versand={versand}")
        r = self.s.post(CALL_URL, data=data, timeout=60); r.raise_for_status()
        self.last_response_html = r.text

        # Stap 5: parse Auftrag-Nr uit confirmation page
        aunr = self._parse_auftrag_nr(r.text)
        if not aunr:
            # Probeer ook in error/warning te zoeken
            err = self._parse_error(r.text)
            if err:
                raise ReimoOrderError(f"Bestelling geweigerd: {err}")
            raise ReimoOrderError(
                "Geen Auftrag-Nr in response gevonden. Mogelijk niet bevestigd. "
                "Check Profiweb manueel.")
        self.log(f"Order geplaatst! Reimo Auftrag-Nr = {aunr}")
        return aunr

    @staticmethod
    def _parse_auftrag_nr(html):
        for pat in [
            r'Auftrag[\s-]*Nr\.\s*([0-9]+)',
            r'name="AUNR"\s+type="hidden"\s+value="([0-9]+)"',
            r'name="AUNR"\s+value="([0-9]+)"',
        ]:
            m = re.search(pat, html, re.I)
            if m:
                return m.group(1).strip()
        return None

    @staticmethod
    def _parse_error(html):
        for pat in [r'Fehler:\s*([^<\n]+)', r'class=["\']?error["\']?[^>]*>([^<]+)']:
            m = re.search(pat, html, re.I)
            if m:
                return m.group(1).strip()
        return None
