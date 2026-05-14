"""Reimo Profiweb checkout orderer (STUB - vereist HAR capture).

Voor full automation:
1. Capture een test bestelling met Chrome DevTools → Network → Save HAR
2. Implementeer hier de exacte POST endpoints + form fields
3. Test met --dry-run voor je echte orders plaatst
"""
import requests
from bs4 import BeautifulSoup

PROFIWEB_LOGIN_URL = (
    "https://profiweb.reimo.com/cgi-bin/r40msvc_menue.pl"
    "?var_hauptpfad=../r40/vc_reimo/&var_html_folgemaske=reimo_index.html"
    "&var_fa1_select=var_fa1_select||16|&var_sprache_select=var_sprache_select||DE|"
    "&var_sprache=var_sprache||DE|"
)
CALL_URL = "https://profiweb.reimo.com/cgi-bin/r40msvcas400_call.pl"


class ReimoOrderer:
    def __init__(self, user, password):
        self.user = user[:8]; self.password = password[:8]
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 CompactLiving/1.0"

    def login(self):
        r = self.s.get(PROFIWEB_LOGIN_URL, timeout=30); r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form", {"name": "form_anmelden"})
        if not form:
            raise RuntimeError("Login form niet gevonden")
        data = {inp.get("name"): inp.get("value", "") for inp in form.find_all("input") if inp.get("name")}
        data["USER"] = self.user; data["PASS"] = self.password
        action = (form.get("action") or CALL_URL).strip()
        url = "https://profiweb.reimo.com" + action if action.startswith("/") else action
        r = self.s.post(url, data=data, timeout=30); r.raise_for_status()
        if "form_anmelden" in r.text and "name=\"USER\"" in r.text:
            raise RuntimeError("Login mislukt")

    def add_to_cart(self, code, qty):
        """Voeg artikel + qty toe aan winkelmandje.
        TODO: Implementeer obv HAR capture. Endpoint vermoedelijk:
              POST CALL_URL met AKTION=ARTIKEL_BESTELLEN, ARTNR, ADMENGE, ...
        """
        raise NotImplementedError(
            "ReimoOrderer.add_to_cart vereist HAR capture van een test bestelling. "
            "Zie pages/3_Reimo_Bestellen.py voor instructies."
        )

    def checkout(self, kommission=""):
        """Bevestig winkelmandje en plaats bestelling.
        TODO: Implementeer obv HAR capture. Endpoint vermoedelijk:
              POST CALL_URL met AKTION=BESTELLEN_BESTAETIGEN of similar.
        """
        raise NotImplementedError("ReimoOrderer.checkout vereist HAR capture.")

    def place_order(self, items, kommission=""):
        """High-level: items = [(code, qty), ...]. Returns Reimo bestel-ref."""
        for code, qty in items:
            self.add_to_cart(code, qty)
        return self.checkout(kommission=kommission)
