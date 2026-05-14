"""
Slimme matching tussen binnenkomende factuur-lijnen en bestaande open
Purchase Orders bij dezelfde leverancier.

Workflow:
  1. Voor leverancier X: haal alle open POs op (state=purchase, qty nog te factureren)
  2. Per factuurlijn: zoek beste matchende PO-line (SKU/prijs/qty/naam)
  3. Groepeer factuurlijnen per matched PO
  4. Maak per PO een Bill met alleen de relevante lines (geen gehele PO als slechts deel)
  5. Voor unmatched lines: maak ofwel nieuwe stock-PO of voeg toe als extra
"""
from difflib import SequenceMatcher
from odoo_client import OdooClient

MIN_MATCH_SCORE = 600  # threshold om een match te accepteren


def _similarity(a, b):
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def get_open_pos_for_supplier(odoo: OdooClient, partner_id: int) -> list:
    """Haal alle open POs (state purchase/done, met openstaande billable qty)."""
    pos = odoo.search_read(
        "purchase.order",
        [("partner_id", "=", partner_id),
         ("state", "in", ["purchase", "done"]),
         ("invoice_status", "in", ["to invoice", "no"])],
        ["id", "name", "date_order", "amount_total", "order_line", "partner_ref"],
        50, "date_order desc"
    )
    if not pos:
        return []

    # Lees alle PO-lines in 1 batch
    all_line_ids = []
    for po in pos:
        all_line_ids.extend(po["order_line"])
    if not all_line_ids:
        return []

    lines = odoo.read(
        "purchase.order.line", all_line_ids,
        ["id", "order_id", "name", "product_id", "product_qty",
         "qty_invoiced", "qty_received", "price_unit", "price_subtotal"]
    )
    lines_by_po = {}
    for line in lines:
        po_id = line["order_id"][0]
        line["qty_remaining"] = max(0, line["product_qty"] - line["qty_invoiced"])
        if line["qty_remaining"] > 0:
            lines_by_po.setdefault(po_id, []).append(line)

    # Behoud enkel POs die nog billable lines hebben
    result = []
    for po in pos:
        if po["id"] in lines_by_po:
            po["lines"] = lines_by_po[po["id"]]
            result.append(po)
    return result


def score_match(invoice_line: dict, po_line: dict) -> int:
    """Score 0-1000 voor hoe goed factuurlijn matcht met PO-line."""
    score = 0

    # SKU exact match — sterkste signaal
    inv_sku = (invoice_line.get("artikelnummer") or "").strip()
    po_product = po_line.get("product_id")
    if po_product and inv_sku:
        # Haal default_code via product (al niet gecached, doen we apart)
        # We vergelijken hier puur op product naam-overlap met SKU
        if inv_sku.lower() in (po_product[1] or "").lower():
            score += 500

    # Naam similarity (fuzzy)
    name_sim = _similarity(invoice_line.get("beschrijving"),
                           po_line.get("name") or (po_product[1] if po_product else ""))
    score += int(name_sim * 300)  # max 300

    # Prijs match
    inv_prijs = invoice_line.get("eenheidsprijs_excl_btw") or 0
    po_prijs = po_line.get("price_unit") or 0
    if inv_prijs > 0 and po_prijs > 0:
        diff_pct = abs(inv_prijs - po_prijs) / po_prijs
        if diff_pct < 0.02:
            score += 200
        elif diff_pct < 0.05:
            score += 150
        elif diff_pct < 0.10:
            score += 80
        elif diff_pct < 0.20:
            score += 30

    # Qty match (kan factureren wat beschikbaar is)
    inv_qty = invoice_line.get("hoeveelheid") or 0
    qty_remain = po_line.get("qty_remaining") or 0
    if inv_qty > 0 and qty_remain > 0:
        if inv_qty <= qty_remain:
            score += 100  # alles kan in 1x
        else:
            score += 30   # gedeeltelijk billable

    return score


def get_product_skus(odoo: OdooClient, product_ids: list) -> dict:
    """Haal default_code per product."""
    if not product_ids:
        return {}
    products = odoo.read("product.product", product_ids, ["id", "default_code"])
    return {p["id"]: (p.get("default_code") or "") for p in products}


def match_invoice_to_pos(odoo: OdooClient, factuur: dict, partner_id: int) -> dict:
    """
    Hoofdfunctie: probeer factuurlijnen te matchen met open POs.

    Returns: {
        'has_open_pos': bool,
        'open_pos': [...],
        'matches': [{'invoice_line_idx': int, 'po_id': int, 'po_line_id': int, 'score': int}, ...],
        'unmatched_idx': [...],
    }
    """
    open_pos = get_open_pos_for_supplier(odoo, partner_id)
    if not open_pos:
        # Geen open PO's → alle factuurlijnen zijn unmatched (te reviewen)
        all_idx = list(range(len(factuur.get("lijnen", []))))
        return {"has_open_pos": False, "open_pos": [], "matches": [], "unmatched_idx": all_idx}

    # Verzamel alle product_ids voor SKU-lookup
    all_prod_ids = []
    for po in open_pos:
        for line in po["lines"]:
            if line.get("product_id"):
                all_prod_ids.append(line["product_id"][0])
    sku_map = get_product_skus(odoo, list(set(all_prod_ids)))

    # Voor elke invoice lijn: zoek beste PO-line met SKU-bonus
    matches = []
    unmatched = []
    # Track gebruikte qty per PO-line zodat 1 PO-line niet 2x wordt geclaimd
    po_line_consumed = {}  # po_line_id → already_claimed_qty

    for idx, inv_line in enumerate(factuur.get("lijnen", [])):
        inv_sku = (inv_line.get("artikelnummer") or "").strip()
        best_match = None
        best_score = 0

        for po in open_pos:
            for po_line in po["lines"]:
                # SKU exacte test (via default_code op product)
                product_id = po_line["product_id"][0] if po_line.get("product_id") else None
                product_sku = sku_map.get(product_id, "")
                base_score = score_match(inv_line, po_line)
                if inv_sku and product_sku and inv_sku == product_sku:
                    base_score += 500  # bonus voor exact SKU

                # Verminder score als deze line al deels claimed
                already_claimed = po_line_consumed.get(po_line["id"], 0)
                remaining = po_line["qty_remaining"] - already_claimed
                if remaining <= 0:
                    continue

                if base_score > best_score:
                    best_score = base_score
                    best_match = (po, po_line, base_score)

        if best_match and best_score >= MIN_MATCH_SCORE:
            po, po_line, sc = best_match
            inv_qty = inv_line.get("hoeveelheid") or 0
            qty_to_invoice = min(inv_qty,
                                 po_line["qty_remaining"] - po_line_consumed.get(po_line["id"], 0))
            matches.append({
                "invoice_line_idx": idx,
                "po_id": po["id"],
                "po_name": po["name"],
                "po_line_id": po_line["id"],
                "po_line_name": po_line.get("name") or "",
                "po_product": po_line["product_id"][1] if po_line.get("product_id") else "?",
                "po_remaining_before": po_line["qty_remaining"],
                "qty_to_invoice": qty_to_invoice,
                "invoice_qty": inv_qty,
                "score": sc,
            })
            po_line_consumed[po_line["id"]] = po_line_consumed.get(po_line["id"], 0) + qty_to_invoice
        else:
            unmatched.append(idx)

    return {
        "has_open_pos": True,
        "open_pos": open_pos,
        "matches": matches,
        "unmatched_idx": unmatched,
        "best_score_per_line": {m["invoice_line_idx"]: m["score"] for m in matches},
    }


def create_bills_from_matches(odoo: OdooClient, factuur: dict, partner_id: int,
                              matches: list, unmatched_idx: list,
                              factuur_ref: str = None,
                              factuur_datum: str = None,
                              product_assignments: dict = None) -> dict:
    """
    product_assignments: optionele dict {invoice_line_idx: product_id} voor unmatched lines
    (gekozen door gebruiker via product-review UI).
    """
    """
    Maak ÉÉN Vendor Bill voor de hele factuur, met lines die linken aan
    verschillende PO's via purchase_line_id.

    Boekhoudkundig correct:
    - 1 leveranciersfactuur = 1 Bill in Odoo (BTW correct, audit-trail klopt)
    - Bill-lines met purchase_line_id updaten qty_invoiced op de juiste PO-line
    - Lines zonder purchase_line_id (unmatched) zijn losse kost-lijnen
    """
    invoice_lijnen = factuur.get("lijnen", [])

    # Verzamel alle PO-line IDs voor product_id lookup in 1 batch
    po_line_ids = [m["po_line_id"] for m in matches]
    po_line_data = {}
    if po_line_ids:
        for pl in odoo.read("purchase.order.line", po_line_ids,
                           ["product_id", "name", "order_id"]):
            po_line_data[pl["id"]] = pl

    # Verzamel unieke PO-namen voor invoice_origin (komma-gescheiden)
    po_names = sorted(set(m["po_name"] for m in matches))
    invoice_origin = ", ".join(po_names) if po_names else None

    # Bouw alle bill_lines (voor matched + unmatched)
    bill_lines = []
    line_summary = []  # voor return-info

    # Matched lines: met purchase_line_id
    for m in matches:
        inv_line = invoice_lijnen[m["invoice_line_idx"]]
        po_line = po_line_data.get(m["po_line_id"], {})
        line_vals = {
            "name": inv_line.get("beschrijving") or po_line.get("name") or "",
            "quantity": m["qty_to_invoice"],
            "price_unit": inv_line.get("eenheidsprijs_excl_btw") or 0,
            "purchase_line_id": m["po_line_id"],
        }
        if po_line.get("product_id"):
            line_vals["product_id"] = po_line["product_id"][0]
        bill_lines.append((0, 0, line_vals))
        line_summary.append({
            "linked_to_po": m["po_name"],
            "beschrijving": (inv_line.get("beschrijving") or "")[:50],
            "qty": m["qty_to_invoice"],
            "prijs": inv_line.get("eenheidsprijs_excl_btw"),
        })

    # Unmatched lines: zonder purchase_line_id (losse kost in Bill)
    product_assignments = product_assignments or {}
    for idx in unmatched_idx:
        inv_line = invoice_lijnen[idx]
        line_vals = {
            "name": inv_line.get("beschrijving") or "",
            "quantity": inv_line.get("hoeveelheid") or 1,
            "price_unit": inv_line.get("eenheidsprijs_excl_btw") or 0,
        }
        # Door user gekozen product (review-UI)
        assigned_product = product_assignments.get(idx)
        if assigned_product:
            line_vals["product_id"] = assigned_product
        bill_lines.append((0, 0, line_vals))
        line_summary.append({
            "linked_to_po": "(geen PO)",
            "beschrijving": (inv_line.get("beschrijving") or "")[:50],
            "qty": inv_line.get("hoeveelheid"),
            "prijs": inv_line.get("eenheidsprijs_excl_btw"),
        })

    if not bill_lines:
        return {"bill_id": None, "error": "Geen lijnen om Bill mee te maken"}

    # Maak ÉÉN Bill voor heel de factuur
    bill_vals = {
        "move_type": "in_invoice",
        "partner_id": partner_id,
        "invoice_date": factuur_datum or factuur.get("factuur", {}).get("datum"),
        "ref": factuur_ref or factuur.get("factuur", {}).get("nummer"),
        "invoice_line_ids": bill_lines,
    }
    if invoice_origin:
        bill_vals["invoice_origin"] = invoice_origin
    bill_id = odoo.create("account.move", bill_vals)

    return {
        "bill_id": bill_id,
        "linked_pos": po_names,
        "n_matched_lines": len(matches),
        "n_unmatched_lines": len(unmatched_idx),
        "n_total_lines": len(bill_lines),
        "lines": line_summary,
    }
