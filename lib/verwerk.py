"""
Hoofdscript factuur-automatisering Compact Living.

Workflow:
  1. PDF (uit inbox/) → Claude API extractie naar gestructureerde data
  2. Match leverancier in Odoo (op BTW-nummer of naam)
  3. Per factuurlijn: fuzzy match product tegen Odoo voorraad
  4. Maak DRAFT Purchase Order in Odoo
  5. Optioneel: bevestig PO (auto-confirm) → Receipt aangemaakt
  6. Schrijf log naar logs/
  7. Verschuif PDF naar verwerkt/ of manueel/

Gebruik:
    python verwerk.py path/to/factuur.pdf
    python verwerk.py inbox/                          # alle PDFs in inbox/
    python verwerk.py factuur.pdf --leverancier-hint Reimo
    python verwerk.py factuur.pdf --auto-confirm       # bevestig PO direct
    python verwerk.py factuur.pdf --analytic 4          # link aan project (id 4 = Patricia)
"""
import os
import sys
import json
import argparse
import shutil
from pathlib import Path
from datetime import datetime

# Force UTF-8 op Windows console (voorkomt cp1252 fouten op emoji's)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Load .env
try:
    from dotenv import load_dotenv
except ImportError:
    print("⚠️  python-dotenv niet geïnstalleerd. Run: pip install -r requirements.txt")
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

# Importeer eigen modules
sys.path.insert(0, str(BASE_DIR))
from odoo_client import OdooClient
from extractor import extract_from_pdf
from matcher import find_product, find_partner
from bill_matcher import match_invoice_to_pos, create_bills_from_matches


INBOX = BASE_DIR / "inbox"
VERWERKT = BASE_DIR / "verwerkt"
MANUEEL = BASE_DIR / "manueel"
LOGS = BASE_DIR / "logs"
for p in [INBOX, VERWERKT, MANUEEL, LOGS]:
    p.mkdir(exist_ok=True)


def log(msg, level="INFO"):
    print(f"[{level}] {msg}")


def get_odoo_client() -> OdooClient:
    for key in ["ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"]:
        if not os.environ.get(key):
            log(f"❌ Ontbrekende env: {key}. Zie .env.example", "ERROR")
            sys.exit(1)
    return OdooClient(
        url=os.environ["ODOO_URL"],
        db=os.environ["ODOO_DB"],
        login=os.environ["ODOO_LOGIN"],
        api_key=os.environ["ODOO_API_KEY"]
    )


def create_product_with_supplier(
    odoo: OdooClient, name: str, default_code: str = None,
    cost: float = 0.0, sale_price: float = 0.0,
    uom_id: int = None, categ_id: int = None,
    is_dienst: bool = False, sale_ok: bool = True,
    partner_id: int = None, supplier_code: str = None,
    supplier_name: str = None, supplier_qty: float = 1.0,
    supplier_price: float = None, supplier_uom_id: int = None,
) -> dict:
    """Maak nieuw product.template + (optioneel) product.supplierinfo voor leverancier.

    Voor de Peppol Bills → Nieuw product flow: vul leverancier-info automatisch in
    op basis van de bill regel (partner van Bill, ARTNR / naam / aantal / prijs)."""
    vals = {
        "name": (name or "Onbekend product")[:200],
        "purchase_ok": True,
        "sale_ok": bool(sale_ok),
        "list_price": float(sale_price or 0),
        "standard_price": float(cost or 0),
    }
    if is_dienst:
        vals["type"] = "service"
    else:
        vals["type"] = "consu"
        vals["is_storable"] = True
    if default_code:
        vals["default_code"] = default_code
    if uom_id:
        vals["uom_id"] = int(uom_id)
        # Odoo SaaS 19+: uom_po_id is verwijderd, alleen uom_id blijft
    if categ_id:
        vals["categ_id"] = int(categ_id)
    # Maak template via product.product (Odoo schaalt naar template)
    new_product_id = odoo.create("product.product", vals)
    # Haal template_id op
    pp = odoo.read("product.product", [new_product_id], ["product_tmpl_id"])[0]
    tmpl_id = pp["product_tmpl_id"][0]
    # Optionele supplierinfo
    supplier_info_id = None
    if partner_id:
        si_vals = {
            "partner_id": int(partner_id),
            "product_tmpl_id": tmpl_id,
            "min_qty": float(supplier_qty or 1),
            "price": float(supplier_price if supplier_price is not None else cost or 0),
            "delay": 3,
        }
        if supplier_code:
            si_vals["product_code"] = supplier_code
        if supplier_name:
            si_vals["product_name"] = supplier_name[:200]
        if supplier_uom_id:
            for uom_field in ("product_uom", "product_uom_id"):
                try:
                    supplier_info_id = odoo.create(
                        "product.supplierinfo", {**si_vals, uom_field: int(supplier_uom_id)})
                    break
                except Exception:
                    supplier_info_id = None
            if supplier_info_id is None:
                supplier_info_id = odoo.create("product.supplierinfo", si_vals)
        else:
            supplier_info_id = odoo.create("product.supplierinfo", si_vals)
    return {
        "id": new_product_id,
        "template_id": tmpl_id,
        "name": vals["name"],
        "default_code": default_code or "",
        "list_price": vals["list_price"],
        "standard_price": vals["standard_price"],
        "supplier_info_id": supplier_info_id,
        "match_method": "auto_created_with_supplier",
        "score": None,
    }


def add_supplierinfo_to_product(
    odoo: OdooClient, template_id: int, partner_id: int,
    product_code: str = None, product_name: str = None,
    min_qty: float = 1.0, price: float = 0.0, delay: int = 3,
    product_uom_id: int = None,
) -> int:
    """Voeg een product.supplierinfo entry toe aan een bestaand product template.

    product_uom_id: optioneel — leverancier-UoM (moet in dezelfde category zijn als product UoM).
    Bv. product per 'm', leverancier verkoopt per 'rol van 2m' → maak UoM 'Rol 2m' in
    category Length met factor 2.
    """
    vals = {
        "partner_id": int(partner_id),
        "product_tmpl_id": int(template_id),
        "min_qty": float(min_qty or 1),
        "price": float(price or 0),
        "delay": int(delay),
    }
    if product_code:
        vals["product_code"] = product_code[:64]
    if product_name:
        vals["product_name"] = product_name[:200]
    if product_uom_id:
        # Probeer leverancier-UoM te zetten (afhankelijk van Odoo versie)
        try:
            sid = odoo.create("product.supplierinfo", {**vals, "product_uom": int(product_uom_id)})
            return sid
        except Exception:
            try:
                sid = odoo.create("product.supplierinfo", {**vals, "product_uom_id": int(product_uom_id)})
                return sid
            except Exception:
                pass  # fallback: zonder eigen UoM
    return odoo.create("product.supplierinfo", vals)


def _safe_uom_fields(odoo: OdooClient) -> list:
    """Return field-list dat veilig leesbaar is op uom.uom (versie-onafhankelijk)."""
    base = ["id", "name"]
    # Probeer optionele velden — afhankelijk van Odoo versie
    try:
        all_fields = odoo.call("uom.uom", "fields_get", [], {"attributes": ["type"]})
    except Exception:
        return base
    for opt in ("category_id", "relative_uom_id", "related_uom_ids",
                 "factor", "relative_factor", "uom_type", "parent_path"):
        if opt in all_fields:
            base.append(opt)
    return base


def get_uoms_by_category(odoo: OdooClient, category_id=None, related_to_uom_id: int = None) -> list:
    """Lijst UoM's, beperkt tot één 'category' = set conversie-compatible UoM's.

    Odoo 17/18: filter op `category_id` (one2many naar uom.category).
    Odoo SaaS 19.1+: geen category_id meer; gebruik `related_uom_ids` van de basis-UoM.
    """
    fields = _safe_uom_fields(odoo)
    # Oude versies: category_id bestaat
    if "category_id" in fields and category_id:
        return odoo.search_read("uom.uom",
                                  [("category_id", "=", int(category_id))],
                                  fields, 200, "name")
    # Nieuwe versie (19.1+): related_uom_ids lookup
    if related_to_uom_id and "related_uom_ids" in fields:
        try:
            rec = odoo.call("uom.uom", "read",
                              [[int(related_to_uom_id)], ["related_uom_ids"]])
            ids = (rec[0].get("related_uom_ids") or []) + [int(related_to_uom_id)]
            if ids:
                return odoo.search_read("uom.uom", [("id", "in", ids)],
                                          fields, 200, "name")
        except Exception:
            pass
    # Fallback: alles
    return odoo.search_read("uom.uom", [], fields, 200, "name")


def list_supplierinfo_for_template(odoo: OdooClient, template_id: int) -> list:
    """Lijst van alle bestaande supplierinfo records voor een product template."""
    return odoo.search_read(
        "product.supplierinfo",
        [("product_tmpl_id", "=", int(template_id))],
        ["id", "partner_id", "product_code", "product_name",
         "min_qty", "price", "delay"],
        50, "sequence,min_qty"
    )


def auto_create_product(odoo: OdooClient, beschrijving: str, artikelnr: str,
                        prijs: float, is_dienst: bool = False) -> dict:
    """Maak een nieuw product in Odoo voor een ongematchte factuurlijn."""
    vals = {
        "name": (beschrijving or "Onbekend product")[:200],
        "purchase_ok": True,
        "sale_ok": False,
        "list_price": 0,
        "standard_price": prijs or 0,
        "description_purchase": f"AUTO-CREATED door factuur-automatisering — controleer en pas aan",
    }
    # Odoo 17+: 'is_storable' bepaalt voorraad-tracking; type='consu'/'service' blijft basis
    if is_dienst:
        vals["type"] = "service"
    else:
        vals["type"] = "consu"
        vals["is_storable"] = True  # voorraad bijhouden (storable product)
    if artikelnr:
        vals["default_code"] = artikelnr
    new_id = odoo.create("product.product", vals)
    return {
        "id": new_id,
        "name": vals["name"],
        "default_code": artikelnr or "",
        "list_price": 0,
        "standard_price": vals["standard_price"],
        "match_method": "auto_created",
        "score": None
    }


def confirm_pos(odoo: OdooClient, po_ids: list) -> dict:
    """Bevestig een lijst PO's (button_confirm). Returns per PO de nieuwe state."""
    states = {}
    for pid in po_ids:
        try:
            cur = odoo.read("purchase.order", [pid], ["state"])[0]
            if cur["state"] in ("draft", "sent"):
                odoo.call("purchase.order", "button_confirm", [[pid]])
            states[pid] = odoo.read("purchase.order", [pid], ["state"])[0]["state"]
        except Exception as e:
            states[pid] = f"error: {e}"
    return states


def validate_receipts_for_pos(odoo: OdooClient, po_ids: list) -> dict:
    """Valideer alle pickings (incoming) voor de gegeven PO's. Markeert volledige levering."""
    result = {}
    for pid in po_ids:
        try:
            po = odoo.read("purchase.order", [pid], ["picking_ids", "name"])[0]
            picking_ids = po.get("picking_ids") or []
            if not picking_ids:
                result[pid] = "no_pickings"
                continue
            pickings = odoo.read("stock.picking", picking_ids,
                                 ["id", "name", "state", "move_ids"])
            done_count = 0
            for pck in pickings:
                if pck["state"] in ("done", "cancel"):
                    continue
                # Set qty_done = product_uom_qty op alle moves
                moves = odoo.read("stock.move", pck["move_ids"],
                                  ["id", "product_uom_qty", "quantity"]) if pck["move_ids"] else []
                for mv in moves:
                    odoo.write("stock.move", [mv["id"]],
                               {"quantity": mv["product_uom_qty"]})
                try:
                    odoo.call("stock.picking", "button_validate", [[pck["id"]]])
                    done_count += 1
                except Exception as e:
                    result[pid] = f"validate_error picking {pck['id']}: {e}"
                    break
            else:
                result[pid] = f"validated {done_count}/{len(pickings)} pickings"
        except Exception as e:
            result[pid] = f"error: {e}"
    return result


def create_bill_from_po(odoo: OdooClient, po_id: int, copy_attachments: bool = True,
                        invoice_date: str = None, ref: str = None) -> dict:
    """
    Maak een Vendor Bill van een PO via Odoo's action_create_invoice.
    Kopieert PDF-attachments van PO → Bill als copy_attachments=True.
    Returns: {'bill_id': int, 'bill_name': str, 'attachments_copied': int}
    """
    # Onthoud welke bills al bestonden vóór de actie
    po_before = odoo.read("purchase.order", [po_id], ["invoice_ids", "name"])[0]
    existing_bill_ids = set(po_before.get("invoice_ids") or [])

    # Roep Odoo's standaard action aan
    odoo.call("purchase.order", "action_create_invoice", [[po_id]])

    # Lees nieuwe invoice_ids
    po_after = odoo.read("purchase.order", [po_id], ["invoice_ids"])[0]
    new_bill_ids = [b for b in (po_after.get("invoice_ids") or []) if b not in existing_bill_ids]
    if not new_bill_ids:
        return {"bill_id": None, "error": "action_create_invoice gaf geen nieuwe Bill terug"}
    bill_id = new_bill_ids[0]

    # Update Bill met datum + ref indien meegegeven
    update_vals = {}
    if invoice_date:
        update_vals["invoice_date"] = invoice_date
    if ref:
        update_vals["ref"] = ref
    if update_vals:
        odoo.write("account.move", [bill_id], update_vals)

    # Kopieer attachments van PO naar Bill
    n_copied = 0
    if copy_attachments:
        atts = odoo.search_read(
            "ir.attachment",
            [("res_model", "=", "purchase.order"), ("res_id", "=", po_id),
             ("mimetype", "=", "application/pdf")],
            ["id", "name", "datas", "mimetype"], 20
        )
        for att in atts:
            odoo.create("ir.attachment", {
                "name": att["name"],
                "datas": att["datas"],
                "res_model": "account.move",
                "res_id": bill_id,
                "mimetype": att["mimetype"],
            })
            n_copied += 1

    bill = odoo.read("account.move", [bill_id], ["name", "ref", "amount_total"])[0]
    return {
        "bill_id": bill_id,
        "bill_name": bill.get("name") or "(draft)",
        "bill_ref": bill.get("ref"),
        "bill_total": bill.get("amount_total"),
        "attachments_copied": n_copied,
        "po_name": po_before["name"],
    }


def get_unlinked_draft_bills(odoo: OdooClient, limit: int = 30) -> list:
    """
    Haal draft Vendor Bills die nog NIET aan een PO gelinkt zijn
    (typisch Peppol-binnenkomende facturen).
    """
    bills = odoo.search_read(
        "account.move",
        [("move_type", "=", "in_invoice"), ("state", "=", "draft")],
        ["id", "name", "invoice_date", "partner_id", "amount_total", "ref",
         "invoice_origin", "invoice_line_ids"],
        limit, "id desc"
    )
    # Filter: enkel waar GEEN enkele line een purchase_line_id heeft
    result = []
    for b in bills:
        line_ids = b.get("invoice_line_ids") or []
        if not line_ids:
            continue
        lines = odoo.read("account.move.line", line_ids,
                          ["id", "purchase_line_id", "product_id", "name",
                           "quantity", "price_unit"])
        has_po_link = any(ln.get("purchase_line_id") for ln in lines)
        if not has_po_link:
            b["_lines"] = lines
            result.append(b)
    return result


def create_po_from_bill(odoo: OdooClient, bill_id: int,
                        link_back: bool = True,
                        confirm: bool = False,
                        validate_receipt: bool = False,
                        analytic_id: int = None) -> dict:
    """
    Maak een Purchase Order van een bestaande (Peppol-)Bill.
    Lines worden 1-op-1 overgenomen; producten met product_id direct gebruikt.
    Lines zonder product_id krijgen geen product (manueel later).

    link_back: na PO-creatie, update bill-lines met purchase_line_id zodat
               qty_invoiced correct telt en audit-trail klopt.
    """
    bill = odoo.read("account.move", [bill_id],
                     ["partner_id", "invoice_date", "ref", "name", "invoice_line_ids"])[0]
    if not bill.get("partner_id"):
        return {"po_id": None, "error": "Bill heeft geen partner"}

    bill_lines = odoo.read("account.move.line", bill["invoice_line_ids"],
                           ["id", "product_id", "name", "quantity", "price_unit",
                            "purchase_line_id", "display_type"])
    # Filter: hou alleen ECHTE productlijnen (skip section/note/tax/payment).
    # In Odoo SaaS 19 kan display_type ook 'product' zijn (truthy) - die WEL meenemen.
    SKIP_TYPES = {"line_section", "line_note", "tax", "payment_term", "rounding"}
    bill_lines = [bl for bl in bill_lines
                  if (bl.get("display_type") or "") not in SKIP_TYPES
                  and (bl.get("quantity") or 0) > 0]

    po_lines_vals = []
    line_mapping = []  # [(bill_line_id, index_in_po_lines)]
    for bl in bill_lines:
        line_vals = {
            "name": bl.get("name") or "",
            "product_qty": bl.get("quantity") or 1,
            "price_unit": bl.get("price_unit") or 0,
        }
        if bl.get("product_id"):
            line_vals["product_id"] = bl["product_id"][0]
        if analytic_id:
            line_vals["analytic_distribution"] = {str(analytic_id): 100}
        po_lines_vals.append((0, 0, line_vals))
        line_mapping.append(bl["id"])

    if not po_lines_vals:
        return {"po_id": None, "error": "Geen lijnen op de Bill"}

    po_vals = {
        "partner_id": bill["partner_id"][0],
        "date_order": bill.get("invoice_date") or datetime.now().strftime("%Y-%m-%d"),
        "partner_ref": bill.get("ref") or bill.get("name"),
        "order_line": po_lines_vals,
    }
    po_id = odoo.create("purchase.order", po_vals)
    po = odoo.read("purchase.order", [po_id], ["name", "state", "amount_total", "order_line"])[0]

    # Confirm + receipt
    if confirm:
        try:
            odoo.call("purchase.order", "button_confirm", [[po_id]])
            po = odoo.read("purchase.order", [po_id], ["name", "state", "amount_total", "order_line"])[0]
        except Exception as e:
            log(f"⚠️ PO confirm faalde: {e}", "WARN")

    if validate_receipt and po["state"] == "purchase":
        try:
            validate_receipts_for_pos(odoo, [po_id])
        except Exception as e:
            log(f"⚠️ Receipt validatie faalde: {e}", "WARN")

    # Link bill-lines terug naar po-lines
    n_linked = 0
    if link_back and po["state"] == "purchase":
        po_line_ids = po["order_line"]
        # po-line volgorde matcht line_mapping volgorde (zelfde create-order)
        for i, bill_line_id in enumerate(line_mapping):
            if i < len(po_line_ids):
                try:
                    odoo.write("account.move.line", [bill_line_id],
                               {"purchase_line_id": po_line_ids[i]})
                    n_linked += 1
                except Exception:
                    pass

    return {
        "po_id": po_id,
        "po_name": po["name"],
        "po_state": po["state"],
        "po_total": po["amount_total"],
        "n_lines": len(po_lines_vals),
        "n_linked_back": n_linked,
        "bill_id": bill_id,
    }


def maak_purchase_order(odoo: OdooClient, factuur: dict, partner_id: int,
                       analytic_id: int = None, auto_confirm: bool = False,
                       auto_create_unmatched: bool = True) -> dict:
    """
    Bouw PO in Odoo van factuurdata.

    Returns: {'po_id': int, 'po_name': str, 'matched': [...], 'unmatched': [...], 'auto_created': [...], 'state': str}
    """
    matched_lines = []
    unmatched_lines = []
    auto_created_lines = []
    po_lines = []

    for lijn in factuur.get("lijnen", []):
        beschrijving = lijn.get("beschrijving") or ""
        artikelnr = lijn.get("artikelnummer")
        qty = lijn.get("hoeveelheid") or 1
        prijs = lijn.get("eenheidsprijs_excl_btw") or 0
        is_dienst = lijn.get("is_dienst", False)

        product = find_product(odoo, beschrijving, artikelnr, prijs)

        # Fallback: auto-create als geen match
        if not product and auto_create_unmatched:
            product = auto_create_product(odoo, beschrijving, artikelnr, prijs, is_dienst)
            auto_created_lines.append({
                "beschrijving": beschrijving,
                "artikelnr": artikelnr,
                "new_product_id": product["id"],
                "new_product_name": product["name"],
                "type": "service" if is_dienst else "consu"
            })

        if product:
            line_vals = {
                "product_id": product["id"],
                "name": beschrijving,
                "product_qty": qty,
                "price_unit": prijs,
            }
            if analytic_id:
                line_vals["analytic_distribution"] = {str(analytic_id): 100}
            po_lines.append((0, 0, line_vals))
            matched_lines.append({
                "beschrijving": beschrijving,
                "artikelnr": artikelnr,
                "qty": qty,
                "prijs": prijs,
                "matched_product": product["name"],
                "matched_id": product["id"],
                "match_method": product["match_method"],
                "score": product.get("score")
            })
        else:
            unmatched_lines.append({
                "beschrijving": beschrijving,
                "artikelnr": artikelnr,
                "qty": qty,
                "prijs": prijs,
                "totaal": (qty or 0) * (prijs or 0),
            })

    if not po_lines:
        return {"po_id": None, "matched": [], "unmatched": unmatched_lines,
                "auto_created": auto_created_lines, "state": "geen producten gematcht"}

    # Maak PO
    po_vals = {
        "partner_id": partner_id,
        "date_order": factuur.get("factuur", {}).get("datum") or datetime.now().strftime("%Y-%m-%d"),
        "partner_ref": factuur.get("factuur", {}).get("nummer"),
        "order_line": po_lines,
    }
    po_id = odoo.create("purchase.order", po_vals)
    po = odoo.read("purchase.order", [po_id], ["name", "state", "amount_total"])[0]

    state = po["state"]
    if auto_confirm and not unmatched_lines:
        try:
            odoo.call("purchase.order", "button_confirm", [[po_id]])
            po = odoo.read("purchase.order", [po_id], ["name", "state", "amount_total"])[0]
            state = po["state"]
        except Exception as e:
            log(f"⚠️ Auto-confirm faalde: {e}", "WARN")

    return {
        "po_id": po_id,
        "po_name": po["name"],
        "po_state": state,
        "po_total": po["amount_total"],
        "matched": matched_lines,
        "unmatched": unmatched_lines,
        "auto_created": auto_created_lines,
    }


def verwerk_pdf(pdf_path: Path, args) -> dict:
    log(f"📄 Verwerken: {pdf_path.name}")
    result = {
        "file": pdf_path.name,
        "timestamp": datetime.now().isoformat(),
        "status": "pending",
    }

    # 1. Extractie
    try:
        factuur = extract_from_pdf(
            str(pdf_path),
            leverancier_hint=args.leverancier_hint,
            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
        )
        result["extracted"] = factuur
        log(f"   ✓ Geëxtraheerd: {factuur.get('leverancier', {}).get('naam')} - "
            f"{len(factuur.get('lijnen', []))} lijnen, totaal €{factuur.get('factuur', {}).get('totaal_excl_btw', '?')} excl BTW")
    except Exception as e:
        result["status"] = "extractie_faalde"
        result["error"] = str(e)
        log(f"   ❌ Extractie faalde: {e}", "ERROR")
        return result

    # 2. Verbind met Odoo
    try:
        odoo = get_odoo_client()
    except Exception as e:
        result["status"] = "odoo_connectie_faalde"
        result["error"] = str(e)
        log(f"   ❌ Odoo connectie: {e}", "ERROR")
        return result

    # 3. Vind leverancier
    lev = factuur.get("leverancier", {})
    partner = find_partner(odoo, lev.get("naam"), lev.get("vat"))
    if not partner:
        result["status"] = "leverancier_onbekend"
        result["leverancier_gezocht"] = lev
        log(f"   ❌ Leverancier '{lev.get('naam')}' niet gevonden in Odoo", "ERROR")
        log(f"      Tip: maak partner aan in Odoo of voeg BTW-nummer toe en herstart", "INFO")
        return result
    result["partner"] = partner
    log(f"   ✓ Leverancier: {partner['name']} (id {partner['id']})")

    # 4. ALTIJD eerst checken of er al een open PO bestaat bij deze leverancier
    if args.mode in ("auto", "bill"):
        log(f"   🔍 Open POs zoeken bij {partner['name']}...")
        match_result = match_invoice_to_pos(odoo, factuur, partner["id"])
        n_open = len(match_result["open_pos"])
        n_match = len(match_result["matches"])
        n_lijnen = len(factuur.get("lijnen", []))
        match_pct = (n_match / n_lijnen * 100) if n_lijnen else 0
        log(f"      → {n_open} open PO(s) gevonden, {n_match}/{n_lijnen} lijnen gematcht ({match_pct:.0f}%)")

        # Beslissingsregels voor mode 'auto'
        use_bill_mode = (args.mode == "bill") or (args.mode == "auto" and n_match > 0 and match_pct >= 50)

        if use_bill_mode and n_match > 0:
            log(f"   ✓ Gebruik BILL-modus (matched op bestaande PO's)")
            result["mode_used"] = "bill"
            result["match_result"] = {
                "n_open_pos": n_open,
                "n_matched_lines": n_match,
                "n_unmatched_lines": len(match_result["unmatched_idx"]),
                "matches": match_result["matches"],
            }
            try:
                bill = create_bills_from_matches(
                    odoo, factuur, partner["id"],
                    match_result["matches"], match_result["unmatched_idx"]
                )
                if not bill.get("bill_id"):
                    raise RuntimeError(bill.get("error", "Bill niet aangemaakt"))
                # End-to-end automation: bevestig gelinkte PO's, valideer Receipts indien geleverd
                linked_po_ids = sorted({m["po_id"] for m in match_result["matches"]})
                confirm_states = confirm_pos(odoo, linked_po_ids) if linked_po_ids else {}
                receipt_results = (
                    validate_receipts_for_pos(odoo, linked_po_ids)
                    if (args.goods_received and linked_po_ids) else {}
                )
                result["confirm_states"] = confirm_states
                result["receipts"] = receipt_results
                # Bewaar als list voor backwards compat met UI-rendering (1 entry)
                result["bills"] = [{
                    "bill_id": bill["bill_id"],
                    "po_id": None,
                    "po_name": ", ".join(bill.get("linked_pos", [])) or "(geen PO)",
                    "n_lines": bill["n_total_lines"],
                    "linked_pos": bill.get("linked_pos", []),
                }]
                log(f"      ✓ ÉÉN Bill aangemaakt (id {bill['bill_id']}) voor factuur {factuur.get('factuur', {}).get('nummer')}")
                log(f"        Linked aan PO('s): {', '.join(bill.get('linked_pos', [])) or '(geen)'}")
                log(f"        {bill['n_matched_lines']} matched + {bill['n_unmatched_lines']} losse lijnen = {bill['n_total_lines']} totaal")
                result["status"] = "bill_compleet" if not match_result["unmatched_idx"] else "bill_deels"
                log(f"     URL: {os.environ['ODOO_URL']}/odoo/action-account.action_move_in_invoice_type/{bill['bill_id']}")
                return result
            except Exception as e:
                log(f"   ⚠️ Bill-creatie faalde, fallback naar PO-modus: {e}", "WARN")
                # fall through naar PO-modus

    # 5. Fallback / forced PO-modus: bouw nieuwe PO
    log(f"   ✓ Gebruik PO-modus (nieuwe PO aanmaken)")
    result["mode_used"] = "po"
    po_result = maak_purchase_order(
        odoo, factuur, partner["id"],
        analytic_id=args.analytic,
        auto_confirm=args.auto_confirm or args.goods_received,
        auto_create_unmatched=not args.no_auto_create
    )
    result.update(po_result)

    if not po_result.get("po_id"):
        result["status"] = "geen_producten_gematcht"
        log(f"   ❌ Geen producten gematcht. {len(po_result['unmatched'])} ongematchte lijnen.", "ERROR")
        return result

    n_matched = len([m for m in po_result["matched"] if m.get("match_method") != "auto_created"])
    n_auto = len(po_result.get("auto_created", []))
    n_unmatched = len(po_result["unmatched"])
    log(f"   ✓ PO {po_result['po_name']} aangemaakt (id {po_result['po_id']}, state={po_result['po_state']})")
    log(f"     • {n_matched} producten gematcht uit catalog")
    if n_auto > 0:
        log(f"     • {n_auto} NIEUW product(en) AUTO-CREATED — controleer/wijs juiste rekening toe in Odoo:")
        for ac in po_result["auto_created"]:
            log(f"        → '{ac['new_product_name'][:50]}' (id {ac['new_product_id']}, type={ac['type']})")
    if n_unmatched > 0:
        log(f"     ⚠️ {n_unmatched} producten NIET gematcht én niet aangemaakt — manueel toevoegen", "WARN")
    log(f"     URL: {os.environ['ODOO_URL']}/odoo/purchase/{po_result['po_id']}")

    # Indien goederen geleverd zijn én PO bevestigd: valideer Receipts en maak Bill
    if args.goods_received and po_result.get("po_state") == "purchase":
        try:
            rec = validate_receipts_for_pos(odoo, [po_result["po_id"]])
            result["receipts"] = rec
            log(f"     ✓ Receipts gevalideerd: {rec}")
        except Exception as e:
            log(f"     ⚠️ Receipt-validatie faalde: {e}", "WARN")

    if n_unmatched == 0 and n_auto == 0:
        result["status"] = "compleet"
    elif n_unmatched == 0:
        result["status"] = "compleet_met_auto_created"
    else:
        result["status"] = "deels_compleet"
    return result


def main():
    parser = argparse.ArgumentParser(description="Verwerk leveranciersfactuur naar Odoo PO.")
    parser.add_argument("path", help="Pad naar PDF of folder met PDFs")
    parser.add_argument("--leverancier-hint", help="Hint voor Claude (bv. 'Reimo')")
    parser.add_argument("--auto-confirm", action="store_true",
                       help="Bevestig PO automatisch als alles gematcht is (creëert Receipt)")
    parser.add_argument("--analytic", type=int,
                       help="Analytische rekening id (bv. 4 voor Patricia Goes - Movano)")
    parser.add_argument("--no-auto-create", action="store_true",
                       help="Disable auto-create van nieuwe producten voor unmatched lijnen (default: aan)")
    parser.add_argument("--goods-received", action="store_true",
                       help="Markeer dat goederen fysiek geleverd zijn → bevestigt PO('s) "
                            "én valideert Receipts (voorraad +). Default: uit (vooruitbetaling).")
    parser.add_argument("--mode", choices=["auto", "po", "bill"], default="auto",
                       help="auto = check eerst open PO's (default), po = forceer nieuwe PO, "
                            "bill = forceer match tegen bestaande POs")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        log(f"❌ Pad bestaat niet: {path}", "ERROR")
        sys.exit(1)

    if path.is_dir():
        pdfs = sorted(path.glob("*.pdf"))
        if not pdfs:
            log(f"Geen PDFs in {path}", "INFO")
            return
        log(f"🔍 {len(pdfs)} PDF(s) gevonden in {path}")
    else:
        pdfs = [path]

    summary = {"compleet": 0, "compleet_met_auto_created": 0, "deels_compleet": 0, "manueel": 0, "fout": 0}

    for pdf in pdfs:
        print()
        result = verwerk_pdf(pdf, args)

        # Schrijf log
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_file = LOGS / f"{ts}-{pdf.stem}.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        # Verplaats PDF
        status = result.get("status", "fout")
        if status in ["compleet", "compleet_met_auto_created", "bill_compleet"]:
            shutil.move(str(pdf), VERWERKT / pdf.name)
            summary[status if status in summary else "compleet"] = summary.get(status if status in summary else "compleet", 0) + 1
        elif status in ["deels_compleet", "bill_deels"]:
            shutil.move(str(pdf), VERWERKT / pdf.name)
            summary["deels_compleet"] += 1
        elif status in ["leverancier_onbekend", "geen_producten_gematcht"]:
            shutil.move(str(pdf), MANUEEL / pdf.name)
            summary["manueel"] += 1
        else:
            shutil.move(str(pdf), MANUEEL / pdf.name)
            summary["fout"] += 1

    print()
    log("📊 Samenvatting:")
    log(f"   ✅ Compleet verwerkt:               {summary['compleet']}")
    log(f"   🆕 Compleet (met auto-created):     {summary['compleet_met_auto_created']}")
    log(f"   🟡 Deels (unmatched + niet auto):   {summary['deels_compleet']}")
    log(f"   ⚠️ Manueel nodig:                   {summary['manueel']}")
    log(f"   ❌ Fout:                            {summary['fout']}")


if __name__ == "__main__":
    main()
