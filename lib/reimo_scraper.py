"""
HTTP-only Reimo Profiweb -> Odoo scraper.
Geen Playwright nodig: gebruikt requests + BeautifulSoup.
Geschikt voor:
  - Lokaal command-line gebruik
  - Windows Task Scheduler
  - Toekomstige Anthropic Claude routine (ingebed als prompt)

Gebruik:
  python http_scraper.py --config config.json
  python http_scraper.py --config config.json --dry-run        # geen Odoo writes
  python http_scraper.py --config config.json --code 31650     # 1 artikel testen
"""
import argparse, json, os, re, sys, time
from datetime import datetime, date
from pathlib import Path
import requests
from bs4 import BeautifulSoup

PROFIWEB_LOGIN_URL = (
    "https://profiweb.reimo.com/cgi-bin/r40msvc_menue.pl"
    "?var_hauptpfad=../r40/vc_reimo/"
    "&var_html_folgemaske=reimo_index.html"
    "&var_fa1_select=var_fa1_select||16|"
    "&var_sprache_select=var_sprache_select||DE|"
    "&var_sprache=var_sprache||DE|"
)
PROFIWEB_CALL_URL = "https://profiweb.reimo.com/cgi-bin/r40msvcas400_call.pl"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ReimoScraper/1.0"


# ============================================================================
# Profiweb HTTP client
# ============================================================================
class Profiweb:
    def __init__(self, user, password, log=print):
        self.user = user[:8]
        self.password = password[:8]
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.log = log

    def login(self):
        # 1) GET login page (initialises session cookies + var_datei_selektionen)
        r = self.s.get(PROFIWEB_LOGIN_URL, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form", {"name": "form_anmelden"})
        if not form:
            raise RuntimeError("Login form not found at PROFIWEB_LOGIN_URL")
        data = {inp.get("name"): inp.get("value", "")
                for inp in form.find_all("input") if inp.get("name")}
        data["USER"] = self.user
        data["PASS"] = self.password
        action = (form.get("action") or "/cgi-bin/r40msvcas400_call.pl").strip()
        login_url = "https://profiweb.reimo.com" + action if action.startswith("/") else action
        r = self.s.post(login_url, data=data, timeout=30)
        r.raise_for_status()
        if "form_anmelden" in r.text and "name=\"USER\"" in r.text:
            raise RuntimeError("Login failed (still on login form)")
        self.log("Profiweb login OK.")
        # Save the post-login response so we can find form_navigation_artikel_detail_info
        self._post_login_html = r.text
        self._nav_to_articles()

    def _nav_to_articles(self):
        """Navigate from index to Artikel-Details page so form_artikelanzeige_suche exists."""
        soup = BeautifulSoup(self._post_login_html, "html.parser")
        nav = soup.find("form", {"name": "form_navigation_artikel_detail_info"})
        if not nav:
            self.log("form_navigation_artikel_detail_info niet gevonden in post-login pagina.")
            return
        action = (nav.get("action") or "/cgi-bin/r40msvcas400_call.pl").strip()
        data = {inp.get("name"): inp.get("value", "")
                for inp in nav.find_all("input") if inp.get("name")}
        url = "https://profiweb.reimo.com" + action if action.startswith("/") else action
        r = self.s.post(url, data=data, timeout=30)
        r.raise_for_status()
        self._articles_page_html = r.text
        if "form_artikelanzeige_suche" in r.text:
            self.log("Artikel-Details pagina geladen.")
        else:
            self.log("Artikel-Details pagina niet bereikt - form_artikelanzeige_suche ontbreekt.")

    def lookup(self, code):
        """POST artikelanzeige search and parse availability fields."""
        # Get fresh page (or use cached) to extract current form state
        html = getattr(self, "_articles_page_html", None)
        if not html or "form_artikelanzeige_suche" not in html:
            self._nav_to_articles()
            html = self._articles_page_html
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", {"name": "form_artikelanzeige_suche"})
        if not form:
            return self._error_result(code, "form_artikelanzeige_suche niet gevonden")
        # Build payload from all hidden inputs on the form
        data = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name: continue
            data[name] = inp.get("value", "")
        # Override key fields
        data["ARTNR"] = str(code).strip()
        data["ADMENGE"] = data.get("ADMENGE") or "1"
        data["ANZ_PREIS"] = "J"  # show prices
        data["GEKLICKT"] = "ARTIKEL_ANZEIGEN"
        # Some Reimo handlers expect AKTION
        data.setdefault("AKTION", "ARTIKEL_ANZEIGEN")
        action = (form.get("action") or PROFIWEB_CALL_URL).strip()
        url = "https://profiweb.reimo.com" + action if action.startswith("/") else action
        try:
            r = self.s.post(url, data=data, timeout=30)
            r.raise_for_status()
        except Exception as e:
            return self._error_result(code, f"HTTP error: {e}")
        # Cache the response so subsequent lookups can reuse this page state
        self._articles_page_html = r.text
        # Optional debug dump
        if os.environ.get("DUMP_LOOKUP"):
            dump_dir = Path(__file__).parent / "dumps"
            dump_dir.mkdir(exist_ok=True)
            (dump_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{code}.html").write_text(r.text, encoding="utf-8")
        return self.parse(code, r.text)

    @staticmethod
    def _error_result(code, msg):
        return {
            "code": code, "found": False, "title": "", "ean": "",
            "klassifizierung": "", "klassifizierung_text": "",
            "verfuegbarkeit": "", "expected_date": "", "expected_days": None,
            "backorder": "", "last_delivery": "", "discontinued": False,
            "raw_status": "ERROR", "haendler_price": "",
            "icon_status": "", "verf_icon_raw": "",
            "error": msg,
        }

    def parse(self, code, html):
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        out = {
            "code": code, "found": False, "title": "", "ean": "",
            "klassifizierung": "", "klassifizierung_text": "",
            "verfuegbarkeit": "", "expected_date": "", "expected_days": None,
            "backorder": "", "last_delivery": "", "discontinued": False,
            "raw_status": "", "haendler_price": "", "icon_status": "",
            "verf_icon_raw": "", "phasing_out": False,
        }
        if "Software error" in text:
            out["raw_status"] = "ERROR"; return out
        if not re.search(r"Bezeichnung", text):
            out["raw_status"] = "NOT_FOUND"; return out
        out["found"] = True

        def cell_after(label):
            """Find label, return next non-empty <td> in same row.
            Labels can be wrapped in <b>/<strong>/<font> inside the <td>."""
            for el in soup.find_all(string=re.compile(rf"^\s*{re.escape(label)}\s*:?\s*$")):
                # Walk up to find the containing td/th
                node = el.parent
                td = None
                for _ in range(6):
                    if node is None: break
                    if node.name in ("td", "th"):
                        td = node; break
                    node = node.parent
                if not td: continue
                sib = td.find_next_sibling("td")
                while sib and not (sib.get_text(strip=True) or sib.find("img")):
                    sib = sib.find_next_sibling("td")
                if sib:
                    return sib
            return None

        def text_after(label):
            cell = cell_after(label)
            return cell.get_text(" ", strip=True) if cell else ""

        out["title"] = text_after("Bezeichnung") or text_after("Langbezeichnung")
        out["ean"] = re.sub(r"\D", "", text_after("EAN-Nummer"))[:14]

        klass_txt = text_after("Klassifizierung")
        if klass_txt:
            m = re.match(r"([A-Z])\s*(.*)", klass_txt)
            if m:
                out["klassifizierung"] = m.group(1)
                extra = (m.group(2) or "").strip()
                if extra:
                    out["klassifizierung_text"] = extra
                    if re.search(r"l[aä]uft\s+aus|nicht\s+mehr|auslauf|aus\s+dem\s+programm",
                                 extra, re.I):
                        out["phasing_out"] = True

        # Verfuegbarkeit: cell may contain image + text
        verf_cell = cell_after("Verfuegbarkeit") or cell_after("Verfügbarkeit")
        if verf_cell:
            out["verfuegbarkeit"] = verf_cell.get_text(" ", strip=True)
            img = verf_cell.find("img")
            if img:
                out["verf_icon_raw"] = (img.get("alt") or "") + "|" + (img.get("src") or "")

        # Backorder / last delivery
        bo = text_after("Rückstandmenge:") or text_after("Rückstandmenge")
        if bo: out["backorder"] = bo
        ld = text_after("letzte Lieferung:") or text_after("letzte Lieferung")
        if ld: out["last_delivery"] = ld
        ldd = text_after("letztes Lieferdatum:") or text_after("letztes Lieferdatum")
        if ldd and not out["last_delivery"]: out["last_delivery"] = ldd

        # Händlerpreis
        hp = text_after("Händlerpreis")
        if hp:
            m = re.search(r"([0-9.,]+)", hp)
            if m: out["haendler_price"] = m.group(1)

        # Date in verfuegbarkeit
        date_re = r"(\d{1,2}\.\d{1,2}\.\d{2,4})"
        for src in (out["verfuegbarkeit"], text):
            for pat in [
                r"voraussichtlich\s+(?:lieferbar|am)\s+" + date_re,
                r"verf.gbar\s+(?:ab|am)\s+" + date_re,
                r"erwartet\s+(?:am\s+)?" + date_re,
                r"Lieferung\s+(?:am\s+)?" + date_re,
            ]:
                m = re.search(pat, src, re.I)
                if m:
                    out["expected_date"] = m.group(1); break
            if out["expected_date"]: break
        if out["expected_date"]:
            out["expected_days"] = self._days_from(out["expected_date"])

        # Icon status (Reimo: VERFUEG-NEIN/GRUEN/GELB/ROT)
        icon_low = (out["verf_icon_raw"] or "").lower()
        if "verfueg-nein" in icon_low or "verfueg_nein" in icon_low:
            out["icon_status"] = "DISCONTINUED"
        elif "verfueg-gruen" in icon_low:
            out["icon_status"] = "AVAILABLE"
        elif "verfueg-gelb" in icon_low or "verfueg-teil" in icon_low:
            out["icon_status"] = "PARTIAL"
        elif "verfueg-rot" in icon_low or "verfueg-lief" in icon_low:
            out["icon_status"] = "BACKORDER"

        # Icon priority: green/yellow → never discontinued; black → discontinued
        if out["icon_status"] in ("AVAILABLE", "PARTIAL", "BACKORDER"):
            out["discontinued"] = False
        elif out["icon_status"] == "DISCONTINUED":
            out["discontinued"] = True
        else:
            # Fallback: phasing_out + no stock text + no date → discontinued
            verf_low = out["verfuegbarkeit"].lower()
            has_stock = any(s in verf_low for s in ["sofort verf", "mehr als", "verfuegbar", "verfügbar"])
            if out["phasing_out"] and not has_stock and not out["expected_date"]:
                out["discontinued"] = True

        # raw_status priority
        verf_low = out["verfuegbarkeit"].lower()
        if out["discontinued"]:
            out["raw_status"] = "DISCONTINUED"
        elif out["expected_date"]:
            out["raw_status"] = "BACKORDER"
        elif any(s in verf_low for s in ["sofort verf", "mehr als", "verfuegbar", "verfügbar"]):
            out["raw_status"] = "AVAILABLE"
        elif out["icon_status"]:
            out["raw_status"] = out["icon_status"]
        else:
            out["raw_status"] = "UNKNOWN"
        return out

    @staticmethod
    def _days_from(dstr):
        for fmt in ("%d.%m.%y", "%d.%m.%Y"):
            try:
                d = datetime.strptime(dstr, fmt).date()
                return (d - date.today()).days
            except ValueError:
                continue
        return None


# ============================================================================
# Decision rules + aggregation (same logic as GUI version)
# ============================================================================
DEFAULT_RULES = {
    "auslauf_action": "block",
    "backorder_action": "warning",
    "no_stock_action": "warning",
    "available_action": "no-message",
    "max_warning_days": 60,
}


def decide(rules, info):
    if not info["found"]:
        return "no-message", ""
    if info["discontinued"]:
        return rules["auslauf_action"], "Niet meer leverbaar bij Reimo."
    days = info["expected_days"]
    if info["expected_date"] and days is not None:
        if days > rules["max_warning_days"]:
            return "block", f"Niet beschikbaar - verwacht pas op {info['expected_date']} (>{rules['max_warning_days']} dagen)."
        return rules["backorder_action"], f"Tijdelijk niet op voorraad. Verwacht beschikbaar: {info['expected_date']}."
    if info["raw_status"] == "AVAILABLE":
        return rules["available_action"], ""
    if info["raw_status"] == "BACKORDER":
        return rules["backorder_action"], "Tijdelijk niet op voorraad bij Reimo."
    if info["raw_status"] == "UNKNOWN":
        return rules["no_stock_action"], "Beschikbaarheid op aanvraag."
    return "no-message", ""


def aggregate(results):
    """results: list of (code, label, info, action, msg)."""
    if not results: return "no-message", ""
    blocked, warned, available = [], [], []
    for code, label, info, action, msg in results:
        po = float(info.get("_incoming_qty") or 0)
        po_suffix = f" • {int(po)} besteld (PO)" if po > 0 else ""
        if action == "block":
            d = (info.get("expected_date") or "niet meer leverbaar") + po_suffix
            blocked.append((code, label, d))
        elif action == "warning":
            d = (info.get("expected_date") or info.get("verfuegbarkeit") or "op aanvraag") + po_suffix
            warned.append((code, label, d))
        else:
            d = info.get("verfuegbarkeit") or "op voorraad"
            available.append((code, label, d))
    if not blocked and not warned:
        return "no-message", ""

    def fmt(code, label, detail):
        tag = (label or "").strip()
        return f"  • [{code}] {tag} — {detail}" if tag else f"  • [{code}] — {detail}"

    lines = []
    if blocked:
        lines.append("Niet leverbaar:")
        lines += [fmt(c, l, d) for c, l, d in blocked]
    if warned:
        if lines: lines.append("")
        lines.append("Beperkt leverbaar:")
        lines += [fmt(c, l, d) for c, l, d in warned]
    if available:
        if lines: lines.append("")
        lines.append("Op voorraad:")
        lines += [fmt(c, l, d) for c, l, d in available]
    return ("block" if blocked else "warning"), "\n".join(lines)


# ============================================================================
# Odoo client (JSON-RPC)
# ============================================================================
class Odoo:
    def __init__(self, url, db, user, password, log=print):
        self.url = url.rstrip("/"); self.db = db; self.user = user; self.password = password
        self.log = log; self.uid = None
        self.s = requests.Session()

    def authenticate(self):
        r = self.s.post(f"{self.url}/web/session/authenticate",
                        json={"jsonrpc": "2.0", "params":
                              {"db": self.db, "login": self.user, "password": self.password}},
                        timeout=30)
        r.raise_for_status()
        d = r.json()
        if not (d.get("result") and d["result"].get("uid")):
            raise RuntimeError(f"Odoo login failed: {d}")
        self.uid = d["result"]["uid"]
        self.log(f"Odoo OK (uid={self.uid})")

    def call(self, model, method, args, kwargs=None):
        r = self.s.post(f"{self.url}/web/dataset/call_kw",
                        json={"jsonrpc":"2.0","method":"call","params":
                              {"model":model,"method":method,"args":args,"kwargs":kwargs or {}}},
                        timeout=60)
        r.raise_for_status()
        d = r.json()
        if "error" in d: raise RuntimeError(json.dumps(d["error"])[:300])
        return d["result"]

    def find_codes(self, supplier_ids, categ_ids, include_archived=True, only_with_code=True):
        domain = []
        if categ_ids:
            domain.append(["categ_id", "child_of", categ_ids])
        if include_archived:
            domain += ["|", ["active","=",True], ["active","=",False]]
        tmpls = self.call("product.template","search_read",
                          [domain, ["id","name","seller_ids","categ_id"]])
        all_seller_ids = sum((t["seller_ids"] for t in tmpls), [])
        if not all_seller_ids: return []
        sup_dom = [["id","in",all_seller_ids]]
        if supplier_ids:
            sup_dom.append(["partner_id","in",supplier_ids])
        sis = self.call("product.supplierinfo","search_read",
                        [sup_dom, ["id","product_tmpl_id","product_id","product_code","partner_id"]])
        by_tmpl = {}
        for s in sis:
            tid = s["product_tmpl_id"][0] if s["product_tmpl_id"] else None
            if tid is None: continue
            if only_with_code and not s["product_code"]: continue
            by_tmpl.setdefault(tid, []).append(s)
        # Variant labels
        variant_ids = list({s["product_id"][0] for sis_list in by_tmpl.values() for s in sis_list if s.get("product_id")})
        variant_label = {}
        if variant_ids:
            vrs = self.call("product.product","read", [variant_ids, ["display_name"]])
            for v in vrs:
                m = re.search(r"\(([^()]+)\)\s*$", v.get("display_name") or "")
                variant_label[v["id"]] = m.group(1) if m else ""
        # Free qty + incoming qty (lopende PO) per variant
        free_qty = {}; incoming_qty = {}
        if variant_ids:
            try:
                qrs = self.call("product.product","read",[variant_ids,["free_qty","incoming_qty"]])
                for q in qrs:
                    free_qty[q["id"]] = q.get("free_qty") or 0.0
                    incoming_qty[q["id"]] = q.get("incoming_qty") or 0.0
            except Exception:
                pass
        out = []
        for t in tmpls:
            for e in by_tmpl.get(t["id"], []):
                vid = e["product_id"][0] if e.get("product_id") else None
                out.append({"tmpl_id": t["id"], "tmpl_name": t["name"],
                            "code": (e["product_code"] or "").strip(),
                            "variant_label": variant_label.get(vid, ""),
                            "variant_id": vid,
                            "free_qty": free_qty.get(vid, 0.0),
                            "incoming_qty": incoming_qty.get(vid, 0.0)})
        return out

    def write_warning(self, tmpl_id, action, msg):
        if action == "no-message":
            text = False
        elif action == "block":
            text = ("🚫 NIET LEVERBAAR\n" + msg) if msg else "🚫 NIET LEVERBAAR"
        else:
            text = ("⚠️ Beperkt leverbaar\n" + msg) if msg else "⚠️ Beschikbaarheid op aanvraag"
        self.call("product.template","write", [[tmpl_id], {"sale_line_warn_msg": text}])


# ============================================================================
# Main
# ============================================================================
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.json")
    p.add_argument("--code", help="Test 1 article code (no Odoo writes)")
    p.add_argument("--dry-run", action="store_true", help="Skip Odoo writes")
    p.add_argument("--log-file", help="Write log to file in addition to stdout")
    args = p.parse_args()

    log_file = None
    if args.log_file:
        log_file = open(args.log_file, "a", encoding="utf-8")

    def log(msg):
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
        print(line, flush=True)
        if log_file:
            log_file.write(line + "\n"); log_file.flush()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        log(f"Config not found: {cfg_path}"); sys.exit(2)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    pw = Profiweb(cfg["profiweb"]["user"], cfg["profiweb"]["password"], log=log)
    pw.login()

    if args.code:
        info = pw.lookup(args.code)
        log(f"--- {args.code} ---")
        for k, v in info.items(): log(f"  {k}: {v}")
        action, msg = decide(cfg.get("rules", DEFAULT_RULES), info)
        log(f"  → action={action} | msg={msg!r}")
        return

    odoo = Odoo(cfg["odoo"]["url"], cfg["odoo"]["db"],
                cfg["odoo"]["user"], cfg["odoo"]["password"], log=log)
    odoo.authenticate()

    scope = cfg["scope"]
    codes = odoo.find_codes(scope.get("supplier_partner_ids", [66]),
                            scope.get("categ_ids", []),
                            scope.get("include_archived", True),
                            scope.get("only_with_supplier_code", True))
    log(f"Te scrapen: {len(codes)} codes.")

    rules = cfg.get("rules", DEFAULT_RULES)
    delay = cfg.get("scrape", {}).get("delay_seconds", 0.7)
    by_tmpl = {}
    ok = err = 0
    # CSV writer voor dashboard
    import csv as _csv
    csv_path = Path("results.csv")
    new_csv = not csv_path.exists()
    csv_f = open(csv_path, "a", newline="", encoding="utf-8")
    csv_w = _csv.writer(csv_f)
    if new_csv:
        csv_w.writerow(["timestamp","tmpl_id","tmpl_name","code","variant_label",
                        "raw_status","klassifizierung","verfuegbarkeit","expected_date",
                        "discontinued","action","msg","free_qty","incoming_qty"])
    for i, c in enumerate(codes, 1):
        try:
            info = pw.lookup(c["code"])
            action, msg = decide(rules, info)
            free_qty = float(c.get("free_qty") or 0)
            incoming_qty = float(c.get("incoming_qty") or 0)
            info["_free_qty"] = free_qty
            info["_incoming_qty"] = incoming_qty
            # Overrides:
            #  free_qty > 0   → geen melding
            #  incoming > 0   → block downgraded naar warning + PO note
            if free_qty > 0 and action != "no-message":
                action = "no-message"; msg = ""
                info["_local_stock_override"] = free_qty
            elif incoming_qty > 0 and action == "block":
                action = "warning"
                msg = (msg or "Niet leverbaar bij Reimo.") + f" (✓ {int(incoming_qty)} besteld - PO loopt)"
                info["_po_override"] = incoming_qty
            by_tmpl.setdefault(c["tmpl_id"], {"name": c["tmpl_name"], "results": []})\
                ["results"].append((c["code"], c["variant_label"], info, action, msg))
            csv_w.writerow([datetime.now().isoformat(timespec="seconds"),
                            c["tmpl_id"], c["tmpl_name"], c["code"], c.get("variant_label",""),
                            info["raw_status"], info["klassifizierung"], info.get("verfuegbarkeit",""),
                            info["expected_date"], info["discontinued"], action, msg,
                            free_qty, incoming_qty])
            csv_f.flush()
            ok += 1
            log(f"[{i}/{len(codes)}] {c['code']} → {action} | {info['raw_status']}")
        except Exception as e:
            err += 1
            log(f"[{i}/{len(codes)}] {c['code']} FOUT: {e}")
        time.sleep(delay)
    csv_f.close()

    log(f"Aggregeren naar {len(by_tmpl)} templates...")
    wrote = 0
    for tid, data in by_tmpl.items():
        a, m = aggregate(data["results"])
        if args.dry_run:
            log(f"  [DRY] tmpl {tid} ({data['name']}): {a}")
        else:
            try:
                odoo.write_warning(tid, a, m)
                wrote += 1
                log(f"  → tmpl {tid} ({data['name']}): {a}")
            except Exception as e:
                log(f"  → tmpl {tid} FOUT: {e}")
    log(f"KLAAR. ok={ok} err={err} templates_geschreven={wrote}")
    if log_file: log_file.close()


if __name__ == "__main__":
    main()
