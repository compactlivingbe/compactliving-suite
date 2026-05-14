"""Reimo automatische bestellingen - PO uit Odoo → Profiweb winkelmandje."""
import os, sys
from pathlib import Path
import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient
try:
    from reimo_orderer import ReimoOrderer
except ImportError:
    ReimoOrderer = None

st.set_page_config(page_title="Reimo Bestellen", page_icon="🛒", layout="wide")

from auth import require_auth
require_auth()

st.title("🛒 Reimo automatische bestelling")
st.caption("Selecteer Odoo PO('s) met leverancier Reimo → plaats in Profiweb winkelmandje")

st.success("""
**✓ Auto-bestelling actief.** Flow gemapt uit Profiweb HAR capture.

Per PO klik je "Bestel nu" → de items worden via Reimo Schnellbestellung verstuurd,
het Reimo Auftrag-Nr wordt automatisch teruggeschreven naar Odoo `Reimo ref`.

⚠️ Eerste keer: gebruik **Dry run** om te valideren zonder echte bestelling.
Max 10 lijnen per bestelling (Reimo Schnellbestellung limit) — meer wordt gesplitst.
""")


@st.cache_resource
def get_odoo():
    return OdooClient(
        url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
        login=os.environ["ODOO_LOGIN"], api_key=os.environ.get("ODOO_API_KEY", ""),
    )


# Reimo partner id
REIMO_PARTNER_ID = 66

try:
    odoo = get_odoo()
    # Custom field flag voor "al besteld bij Reimo"
    pos = odoo.search_read(
        "purchase.order",
        [("partner_id", "=", REIMO_PARTNER_ID),
         ("state", "in", ["purchase", "done"])],
        ["id", "name", "date_order", "amount_total", "state", "partner_ref",
         "order_line"],
        50, "date_order desc"
    )
except Exception as e:
    st.error(f"Odoo connectie faalt: {e}")
    st.stop()

if not pos:
    st.success("Geen openstaande Reimo PO's gevonden.")
    st.stop()

st.markdown(f"### {len(pos)} Reimo PO('s) gevonden")

df = pd.DataFrame([{
    "Selecteer": False,
    "PO": p["name"],
    "Datum": p["date_order"],
    "Bedrag": f"€ {p['amount_total']:.2f}",
    "Status": p["state"],
    "Reimo ref": p.get("partner_ref") or "—",
    "Lijnen": len(p.get("order_line", [])),
    "_id": p["id"],
} for p in pos])

edited = st.data_editor(df, hide_index=True, use_container_width=True,
                         column_config={"_id": None},
                         disabled=["PO","Datum","Bedrag","Status","Reimo ref","Lijnen"])

selected_ids = edited[edited["Selecteer"]]["_id"].tolist()

if not selected_ids:
    st.caption("Selecteer 1 of meer PO's om verder te gaan.")
    st.stop()

st.markdown(f"### {len(selected_ids)} PO('s) geselecteerd")

# Build code_map across ALL selected POs (cached resolution: variant → reimo code)
all_lines_per_po = {}
all_product_ids = set()
for pid in selected_ids:
    lines = odoo.search_read(
        "purchase.order.line", [("order_id", "=", pid)],
        ["product_id", "product_qty", "price_unit", "name"], 100
    )
    all_lines_per_po[pid] = lines
    for l in lines:
        if l.get("product_id"):
            all_product_ids.add(l["product_id"][0])

product_ids = list(all_product_ids)
prods = odoo.search_read("product.product", [("id", "in", product_ids)],
                          ["id", "product_tmpl_id", "default_code"]) if product_ids else []
var_to_tmpl = {p["id"]: p["product_tmpl_id"][0] for p in prods if p.get("product_tmpl_id")}
var_default_code = {p["id"]: p.get("default_code") for p in prods}
tmpl_ids = list({tid for tid in var_to_tmpl.values()})
sis_var = odoo.search_read(
    "product.supplierinfo",
    [("product_id", "in", product_ids), ("partner_id", "=", REIMO_PARTNER_ID)],
    ["product_id", "product_code"]
) if product_ids else []
sis_tmpl = odoo.search_read(
    "product.supplierinfo",
    [("product_tmpl_id", "in", tmpl_ids), ("partner_id", "=", REIMO_PARTNER_ID),
     ("product_id", "=", False)],
    ["product_tmpl_id", "product_code"]
) if tmpl_ids else []
code_map = {}
for s in sis_var:
    if s.get("product_id") and s.get("product_code"):
        code_map[s["product_id"][0]] = s["product_code"].strip()
tmpl_code_map = {s["product_tmpl_id"][0]: s["product_code"].strip()
                 for s in sis_tmpl if s.get("product_tmpl_id") and s.get("product_code")}
for vid, tid in var_to_tmpl.items():
    if vid not in code_map and tid in tmpl_code_map:
        code_map[vid] = tmpl_code_map[tid]
for vid, dc in var_default_code.items():
    if vid not in code_map and dc:
        code_map[vid] = dc.strip()

# Toon details + Reimo codes
for pid in selected_ids:
    p = next(x for x in pos if x["id"] == pid)
    with st.expander(f"📋 {p['name']} — €{p['amount_total']:.2f}", expanded=True):
        lines = all_lines_per_po[pid]
        rows = []
        for l in lines:
            pid_v = l["product_id"][0] if l["product_id"] else None
            code = code_map.get(pid_v, "?")
            profiweb_url = f"https://profiweb.reimo.com/cgi-bin/r40msvcas400_call.pl?ARTNR={code}"
            rows.append({
                "Reimo code": code,
                "Product": l["product_id"][1] if l["product_id"] else l["name"],
                "Aantal": l["product_qty"],
                "Prijs": f"€ {l['price_unit']:.2f}",
                "Profiweb": profiweb_url,
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                     column_config={"Profiweb": st.column_config.LinkColumn("Open in Profiweb")})

st.divider()
st.markdown("### 📤 Bestelling versturen")

col_email, col_komm = st.columns(2)
with col_email:
    email = st.text_input("Bevestigings-email",
                          value=os.environ.get("ODOO_LOGIN", "leveranciers@compactliving.be"))
with col_komm:
    bemerk = st.text_input("Opmerking (optioneel)", value="")

st.markdown("#### 1️⃣ Bouw bestelvoorbeeld")
c1, c2 = st.columns(2)
with c1:
    preview_btn = st.button("👁 Preview bestelling", use_container_width=True)
with c2:
    if st.button("📋 Kopieer codes (handmatig)", use_container_width=True):
        all_items = []
        for pid in selected_ids:
            p = next(x for x in pos if x["id"] == pid)
            lines = odoo.search_read("purchase.order.line", [("order_id", "=", pid)],
                                     ["product_id", "product_qty"], 100)
            for l in lines:
                pv = l["product_id"][0] if l["product_id"] else None
                if pv in code_map:
                    all_items.append(f"{code_map[pv]}\t{int(l['product_qty'])}")
        st.code("\n".join(all_items))
        st.caption("Plak in Profiweb Schnellbestellung.")

# Bouw preview / order data
order_payload = []  # [{po_name, po_id, items:[(code,qty,desc,price)]}]
if preview_btn or st.session_state.get("_preview_built"):
    st.session_state["_preview_built"] = True
    for pid in selected_ids:
        p = next(x for x in pos if x["id"] == pid)
        lines = odoo.search_read("purchase.order.line", [("order_id", "=", pid)],
                                 ["product_id", "product_qty", "price_unit", "name"], 100)
        items = []
        missing = []
        for l in lines:
            pv = l["product_id"][0] if l["product_id"] else None
            if pv in code_map:
                items.append((code_map[pv], int(l["product_qty"]),
                              l["product_id"][1] if l["product_id"] else l["name"],
                              l["price_unit"]))
            else:
                missing.append(l["product_id"][1] if l["product_id"] else "(geen product)")
        order_payload.append({"po_name": p["name"], "po_id": pid, "po_total": p["amount_total"],
                              "items": items, "missing": missing})

if order_payload:
    st.markdown("#### 2️⃣ Controleer wat naar Reimo gaat")
    total_items = sum(len(o["items"]) for o in order_payload)
    total_value = sum(sum(qty*price for _,qty,_,price in o["items"]) for o in order_payload)
    cm1, cm2, cm3 = st.columns(3)
    cm1.metric("PO's", len(order_payload))
    cm2.metric("Items totaal", total_items)
    cm3.metric("Geschatte waarde", f"€ {total_value:,.2f}")

    for o in order_payload:
        with st.expander(f"📦 {o['po_name']} — {len(o['items'])} items, €{o['po_total']:.2f}",
                          expanded=True):
            if o["missing"]:
                st.warning(f"⚠ {len(o['missing'])} lijn(en) zonder Reimo code worden OVERGESLAGEN: {o['missing']}")
            if o["items"]:
                st.dataframe(pd.DataFrame([
                    {"Reimo code": c, "Aantal": q, "Product": d[:60], "Prijs": f"€ {p:.2f}"}
                    for c, q, d, p in o["items"]
                ]), hide_index=True, use_container_width=True)
                if len(o["items"]) > 10:
                    st.warning(f"⚠ {len(o['items'])} items > 10 → wordt gesplitst in {-(-len(o['items'])//10)} Reimo orders")
            else:
                st.error("Geen items met Reimo code → kan niet bestellen")

    # Confirmatie
    st.markdown("#### 3️⃣ Bevestig en bestel")
    st.warning("⚠ Eens je op '🚀 Bestel definitief' klikt wordt de bestelling **echt** geplaatst bij Reimo. Geen undo.")

    cc1, cc2, cc3 = st.columns([1, 1, 2])
    with cc1:
        dry = st.button("🛒 Items in winkelmandje (dry run)", use_container_width=True,
                        help="Voegt items toe aan jouw Profiweb winkelmandje (Bestellung lijst), "
                             "MAAR plaatst geen finale order. Login op profiweb.reimo.com om te controleren. "
                             "Geen echte aankoop.")
    with cc2:
        confirm_text = st.text_input("Type **BESTELLEN** om te bevestigen",
                                      key="confirm_input", label_visibility="collapsed",
                                      placeholder="type BESTELLEN")
    with cc3:
        real = st.button("🚀 Bestel definitief bij Reimo",
                         type="primary", use_container_width=True,
                         disabled=(ReimoOrderer is None or confirm_text != "BESTELLEN"),
                         help="Vul eerst BESTELLEN in het veld" if confirm_text != "BESTELLEN" else None)
else:
    dry = False
    real = False

if (dry or real) and ReimoOrderer is not None and order_payload:
    st.markdown("#### 4️⃣ Uitvoeren")
    log_box = st.empty()
    log_lines = []
    def stlog(m):
        log_lines.append(m)
        log_box.code("\n".join(log_lines[-30:]))

    try:
        orderer = ReimoOrderer(
            user=os.environ["PROFIWEB_USER"],
            password=os.environ["PROFIWEB_PASS"],
            log=stlog,
        )
        orderer.login()
        for o in order_payload:
            if not o["items"]:
                continue
            items_only = [(c, q) for c, q, _, _ in o["items"]]
            refs = []
            for batch_idx in range(0, len(items_only), 10):
                batch = items_only[batch_idx:batch_idx+10]
                suffix = f" deel {batch_idx//10 + 1}" if len(items_only) > 10 else ""
                aunr = orderer.place_order(
                    batch,
                    kommission=o["po_name"] + suffix,
                    email=email,
                    bemerkung=bemerk,
                    dry_run=dry,
                )
                refs.append(aunr)
            ref_str = ", ".join(refs)
            if dry:
                st.info(f"🔬 Dry run **{o['po_name']}** → zou {len(items_only)} items besteld hebben")
            else:
                odoo.write("purchase.order", [o["po_id"]], {"partner_ref": ref_str})
                st.success(f"✓ **{o['po_name']}** → Reimo Auftrag-Nr: **{ref_str}** (geschreven naar Odoo)")
        if not dry:
            # Reset confirmation veld + payload
            st.session_state.pop("_preview_built", None)
            st.session_state.pop("confirm_input", None)
    except Exception as e:
        st.error(f"FOUT: {e}")
        import traceback
        st.code(traceback.format_exc())
