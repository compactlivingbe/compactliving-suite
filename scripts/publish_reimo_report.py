"""
Genereert het Reimo-rapport (HTML) en zet het als vaste ir.attachment in Odoo,
zodat de website-pagina /inttools/rapporten/reimo het in een iframe toont.

Bron:
  - Nieuwe producten: data/reimo_newcomers.json (door de wekelijkse scraper gevuld)
  - Niet meer verkocht: live Odoo (product.template.sale_line_warn_msg ~ NIET LEVERBAAR)

Credentials via env: ODOO_URL, ODOO_DB, ODOO_LOGIN, ODOO_API_KEY of ODOO_PASSWORD.

Gebruik:
  python scripts/publish_reimo_report.py                 # upload naar Odoo
  python scripts/publish_reimo_report.py --out rap.html  # ook lokaal wegschrijven
  python scripts/publish_reimo_report.py --dry-run       # enkel lokaal, geen Odoo-write
"""
import argparse
import base64
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

from odoo_client import OdooClient  # noqa: E402
import reimo_report_html as rh  # noqa: E402

NEWCOMERS_PATH = REPO_ROOT / "data" / "reimo_newcomers.json"
ATT_NAME = "Reimo rapport (website).html"
REIMO_PARTNER_ID = 66
DISCONTINUED_NEEDLE = "NIET LEVERBAAR"


def _odoo() -> OdooClient:
    return OdooClient(
        url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
        login=os.environ["ODOO_LOGIN"],
        api_key=os.environ.get("ODOO_API_KEY", ""),
        password=os.environ.get("ODOO_PASSWORD", ""))


def fetch_reimo_code_map(c: OdooClient) -> dict:
    """{Reimo-leverancierscode: product_tmpl_id} voor alle Reimo-producten in Odoo."""
    sis = c.search_read("product.supplierinfo", [("partner_id", "=", REIMO_PARTNER_ID)],
                        ["product_tmpl_id", "product_code"], limit=100000)
    m = {}
    for s in sis:
        tid = s["product_tmpl_id"][0] if s.get("product_tmpl_id") else None
        code = (s.get("product_code") or "").strip()
        if tid and code and code not in m:
            m[code] = tid
    return m


_DATE_RE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")


def classify_warning(msg: str) -> tuple[bool, str]:
    """Onderscheid echt vervallen (Auslauf) van tijdelijke lange backorder.

    De sync schrijft geblokkeerde varianten onder 'Niet leverbaar:'. Een regel
    met een datum = tijdelijk uit voorraad (verwacht op ...); een regel zónder
    datum / met 'niet meer leverbaar' = echt vervallen.

    Returns (echt_vervallen, detail) waarbij detail de vervallen variant(en) is.
    """
    blocked = []
    in_block = False
    for ln in (msg or "").splitlines():
        s = ln.strip().lstrip("•").strip()
        low = s.lower()
        if low.startswith("niet leverbaar"):
            in_block = True
            continue
        if in_block:
            if not s or low.startswith(("beperkt leverbaar", "op voorraad")):
                break
            blocked.append(s)
    if not blocked:
        return True, ""  # geen detail → conservatief als vervallen behandelen
    auslauf = [b for b in blocked
               if "niet meer leverbaar" in b.lower() or not _DATE_RE.search(b)]
    detail = "; ".join(auslauf if auslauf else blocked)
    return (len(auslauf) > 0), detail


def fetch_discontinued(c: OdooClient) -> list[dict]:
    base = os.environ["ODOO_URL"].rstrip("/")
    sis = c.search_read("product.supplierinfo", [("partner_id", "=", REIMO_PARTNER_ID)],
                        ["product_tmpl_id", "product_code"], limit=100000)
    code_by_tmpl = {}
    for s in sis:
        tid = s["product_tmpl_id"][0] if s.get("product_tmpl_id") else None
        if tid and s.get("product_code") and tid not in code_by_tmpl:
            code_by_tmpl[tid] = s["product_code"]
    if not code_by_tmpl:
        return []
    # active_test=False zodat reeds gearchiveerde producten óók getoond worden
    # (met status 'Gearchiveerd'); anders zouden ze uit de lijst verdwijnen.
    tmpls = c.call(
        "product.template", "search_read",
        [[("id", "in", list(code_by_tmpl)), ("sale_line_warn_msg", "ilike", DISCONTINUED_NEEDLE)],
         ["name", "default_code", "list_price", "sale_line_warn_msg", "image_128",
          "qty_available", "active", "is_published", "purchase_ok"]],
        {"limit": 100000, "order": "name", "context": {"active_test": False}})
    out = []
    for t in tmpls:
        raw = (t.get("sale_line_warn_msg") or "")
        echt_vervallen, detail = classify_warning(raw)
        if not echt_vervallen:
            # tijdelijk lang uit voorraad (backorder), geen Auslauf → niet tonen
            continue
        heeft_foto = bool(t.get("image_128"))
        out.append({
            "tmpl_id": t["id"],
            "naam": t.get("name", ""),
            "reimo_code": code_by_tmpl.get(t["id"], ""),
            "odoo_code": t.get("default_code") or "",
            "prijs": t.get("list_price") or 0.0,
            "voorraad": t.get("qty_available") or 0.0,
            "actief": bool(t.get("active")),
            "op_website": bool(t.get("is_published")),
            "bestelbaar": bool(t.get("purchase_ok")),
            "detail": detail,
            "foto": f"{base}/web/image/product.template/{t['id']}/image_128" if heeft_foto else "",
            "foto_groot": f"{base}/web/image/product.template/{t['id']}/image_512" if heeft_foto else "",
            "odoo_url": f"{base}/odoo/inventory/products/{t['id']}",
        })
    return out


def upsert_attachment(c: OdooClient, html_text: str) -> int:
    datas = base64.b64encode(html_text.encode("utf-8")).decode("ascii")
    found = c.search_read("ir.attachment",
                          [("name", "=", ATT_NAME), ("res_model", "=", False)], ["id"], 1)
    vals = {"name": ATT_NAME, "mimetype": "text/html", "datas": datas,
            "res_model": False, "res_id": False, "public": True}
    if found:
        aid = found[0]["id"]
        c.write("ir.attachment", [aid], {"datas": datas, "mimetype": "text/html", "public": True})
        return aid
    return c.create("ir.attachment", vals)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="Schrijf de HTML ook lokaal weg")
    ap.add_argument("--dry-run", action="store_true", help="Geen Odoo-write")
    args = ap.parse_args()

    store = {}
    if NEWCOMERS_PATH.exists():
        store = json.loads(NEWCOMERS_PATH.read_text(encoding="utf-8"))
    else:
        print(f"WAARSCHUWING: {NEWCOMERS_PATH} bestaat niet — tab 'Nieuwe producten' is leeg.")

    gen_date = date.today().isoformat()

    discontinued = []
    code_map = {}
    odoo_base = os.environ.get("ODOO_URL", "").rstrip("/")
    if not args.dry_run:
        c = _odoo()
        discontinued = fetch_discontinued(c)
        code_map = fetch_reimo_code_map(c)
        # Ook matchen op interne referentie (default_code) — vangt producten die
        # in Odoo staan zonder Reimo-leverancierscode.
        codes = list((store.get("products") or {}).keys())
        for i in range(0, len(codes), 500):
            chunk = codes[i:i + 500]
            for t in c.search_read("product.template", [("default_code", "in", chunk)],
                                   ["id", "default_code"], limit=100000):
                dc = (t.get("default_code") or "").strip()
                if dc and dc not in code_map:
                    code_map[dc] = t["id"]
        print(f"Odoo: {len(discontinued)} niet-leverbare Reimo-producten; "
              f"{len(code_map)} codes gekoppeld.")

    html_text = rh.build_html(store, discontinued, gen_date,
                              odoo_codes=code_map, odoo_base=odoo_base)

    if args.out:
        Path(args.out).write_text(html_text, encoding="utf-8")
        print(f"Lokaal weggeschreven: {args.out} ({len(html_text)} tekens)")

    if args.dry_run:
        print("Dry-run: geen Odoo-write.")
        return 0

    aid = upsert_attachment(c, html_text)
    base = os.environ["ODOO_URL"].rstrip("/")
    print(f"Attachment id={aid} bijgewerkt. Inhoud: {base}/web/content/{aid}/reimo.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
