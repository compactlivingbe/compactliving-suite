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

if not st.session_state.get("pw_ok"):
    st.warning("Login eerst via Home.")
    st.stop()

st.title("🛒 Reimo automatische bestelling")
st.caption("Selecteer Odoo PO('s) met leverancier Reimo → plaats in Profiweb winkelmandje")

st.info("""
**🔬 Beta status — vereist eenmalige flow capture**

De checkout-flow van Profiweb (HTTP POST naar Warenkorb + Bestellen endpoints)
moet eerst gemapt worden. Zonder die mapping kan deze pagina:

- ✓ Lijst van Reimo PO's tonen die nog niet besteld zijn
- ✓ Per PO de regels + Reimo codes tonen
- ✓ De **Profiweb URL openen** met code voor manuele winkelmandje
- ✗ Niet automatisch checken-out (nog niet)

**Voor full automation** stuur me een HAR-export van een test bestelling:
1. Login Profiweb in Chrome
2. F12 → Network tab → "Preserve log" aan
3. Doe een test order van 1 stuk goedkoop artikel
4. Rechtsklik in Network tab → "Save all as HAR with content"
5. Stuur het bestand
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
col1, col2 = st.columns(2)
with col1:
    if ReimoOrderer is None:
        st.button("🚀 Plaats bestelling bij Reimo (auto)", disabled=True,
                  help="Niet beschikbaar — vereist HAR capture (zie info hierboven)")
    else:
        if st.button("🚀 Plaats bestelling bij Reimo (auto)", type="primary"):
            try:
                orderer = ReimoOrderer(
                    user=os.environ["PROFIWEB_USER"],
                    password=os.environ["PROFIWEB_PASS"],
                )
                orderer.login()
                for pid in selected_ids:
                    p = next(x for x in pos if x["id"] == pid)
                    lines = odoo.search_read(
                        "purchase.order.line", [("order_id", "=", pid)],
                        ["product_id", "product_qty"], 100
                    )
                    items = []
                    for l in lines:
                        pv = l["product_id"][0] if l["product_id"] else None
                        if pv in code_map:
                            items.append((code_map[pv], int(l["product_qty"])))
                    ref = orderer.place_order(items, kommission=p["name"])
                    odoo.write("purchase.order", [pid], {"partner_ref": ref})
                    st.success(f"✓ {p['name']} → Reimo ref: {ref}")
            except Exception as e:
                st.error(f"FOUT: {e}")
with col2:
    if st.button("📋 Kopieer codes naar klembord (handmatig)"):
        all_items = []
        for pid in selected_ids:
            p = next(x for x in pos if x["id"] == pid)
            lines = odoo.search_read("purchase.order.line", [("order_id", "=", pid)],
                                     ["product_id", "product_qty"], 100)
            for l in lines:
                pv = l["product_id"][0] if l["product_id"] else None
                if pv in code_map:
                    all_items.append(f"{code_map[pv]}\t{int(l['product_qty'])}")
        text = "\n".join(all_items)
        st.code(text)
        st.caption("Selecteer + copy bovenstaande lijst, plak in Profiweb Schnellbestellung.")
