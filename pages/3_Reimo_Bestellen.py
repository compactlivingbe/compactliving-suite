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

# Toon details + Reimo codes
for pid in selected_ids:
    p = next(x for x in pos if x["id"] == pid)
    with st.expander(f"📋 {p['name']} — €{p['amount_total']:.2f}", expanded=True):
        # Get order lines
        lines = odoo.search_read(
            "purchase.order.line",
            [("order_id", "=", pid)],
            ["product_id", "product_qty", "price_unit", "name"],
            100
        )
        # Get supplier codes per product
        product_ids = [l["product_id"][0] for l in lines if l["product_id"]]
        sis = odoo.search_read(
            "product.supplierinfo",
            [("product_id", "in", product_ids), ("partner_id", "=", REIMO_PARTNER_ID)],
            ["product_id", "product_code"]
        )
        code_map = {s["product_id"][0]: s["product_code"] for s in sis if s.get("product_id")}

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

c1, c2, c3 = st.columns(3)
with c1:
    dry = st.button("🔬 Dry run (geen echte bestelling)", use_container_width=True)
with c2:
    real = st.button("🚀 Bestel nu bij Reimo", type="primary", use_container_width=True,
                     disabled=ReimoOrderer is None)
with c3:
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

if (dry or real) and ReimoOrderer is not None:
    try:
        orderer = ReimoOrderer(
            user=os.environ["PROFIWEB_USER"],
            password=os.environ["PROFIWEB_PASS"],
            log=lambda m: st.write(f"_{m}_"),
        )
        orderer.login()
        for pid in selected_ids:
            p = next(x for x in pos if x["id"] == pid)
            with st.expander(f"📦 {p['name']}", expanded=True):
                lines = odoo.search_read(
                    "purchase.order.line", [("order_id", "=", pid)],
                    ["product_id", "product_qty"], 100
                )
                items = []
                missing = []
                for l in lines:
                    pv = l["product_id"][0] if l["product_id"] else None
                    if pv in code_map:
                        items.append((code_map[pv], int(l["product_qty"])))
                    else:
                        missing.append(l["product_id"][1] if l["product_id"] else "(geen product)")
                if missing:
                    st.warning(f"⚠ {len(missing)} lijn(en) zonder Reimo code overgeslagen: {missing}")
                if not items:
                    st.error("Geen items met Reimo code gevonden.")
                    continue
                if len(items) > 10:
                    st.warning(f"⚠ {len(items)} items > 10 (Reimo limiet). Wordt gesplitst in batches van 10.")
                # Split in batches of 10
                refs = []
                for batch_idx in range(0, len(items), 10):
                    batch = items[batch_idx:batch_idx+10]
                    suffix = f" deel {batch_idx//10 + 1}" if len(items) > 10 else ""
                    aunr = orderer.place_order(
                        batch,
                        kommission=p["name"] + suffix,
                        email=email,
                        bemerkung=bemerk,
                        dry_run=dry,
                    )
                    refs.append(aunr)
                ref_str = ", ".join(refs)
                if dry:
                    st.info(f"🔬 Dry run: {p['name']} → zou {len(items)} items besteld hebben")
                else:
                    odoo.write("purchase.order", [pid], {"partner_ref": ref_str})
                    st.success(f"✓ {p['name']} → Reimo Auftrag-Nr: **{ref_str}**")
    except Exception as e:
        st.error(f"FOUT: {e}")
        import traceback
        st.code(traceback.format_exc())
