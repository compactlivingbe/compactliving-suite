"""VBD Services (Autoterm standkachels) prijssync."""
import os, sys
from pathlib import Path
import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient
from vbd_scraper import fetch_all, compare_with_odoo, DEFAULT_CATEGORIES, BASE

st.set_page_config(page_title="VBD Standkachels", page_icon="🔥", layout="wide")

from auth import require_auth
require_auth()

st.title("🔥 VBD Services — Autoterm standkachels sync")
st.caption("Scrape vbdservices.nl (openbare prijzen incl BTW) → vergelijk met Odoo → import/update.")

VBD_PARTNER_ID = 56
DEFAULT_MARGIN = 1.32


def get_odoo():
    return OdooClient(url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
                       login=os.environ["ODOO_LOGIN"], api_key=os.environ.get("ODOO_API_KEY", ""))


# ============ CONFIG ============
with st.expander("⚙ Categorieën om te scrapen", expanded=False):
    st.caption("Standaard alle Autoterm-categorieën. Voeg URL-paden toe (één per regel).")
    cats_text = st.text_area("Categorie-paden", value="\n".join(DEFAULT_CATEGORIES),
                              height=200, key="vbd_cats")
    selected_cats = [c.strip() for c in cats_text.splitlines() if c.strip()]
    st.caption(f"{len(selected_cats)} categorieën geselecteerd.")


col1, col2 = st.columns([3, 1])
with col1:
    delay = st.slider("Delay tussen pagina's (sec)", 0.0, 2.0, 0.4, 0.1)
with col2:
    run_btn = st.button("▶ Scrape + analyseren", type="primary", use_container_width=True)


# ============ SCRAPE ============
if run_btn:
    log_box = st.empty()
    log_lines = []

    def log(msg):
        log_lines.append(str(msg))
        log_box.code("\n".join(log_lines[-30:]), language=None)

    with st.spinner("VBD productlijst ophalen..."):
        products = fetch_all(selected_cats, log=log, delay=delay)
    st.success(f"✓ {len(products)} unieke producten gevonden op VBD")

    with st.spinner("Vergelijken met Odoo..."):
        odoo = get_odoo()
        result = compare_with_odoo(odoo, VBD_PARTNER_ID, products, log=log)

    st.session_state["_vbd_result"] = result
    st.session_state["_vbd_products"] = products


# ============ RESULTAAT ============
if "_vbd_result" in st.session_state:
    result = st.session_state["_vbd_result"]
    st.divider()
    st.markdown("## 📊 Resultaten")

    cm1, cm2, cm3, cm4 = st.columns(4)
    cm1.metric("VBD producten", result["total_vbd"])
    cm2.metric("Match in Odoo", result["total_matched"])
    cm3.metric("Ontbrekend", len(result["missing"]))
    cm4.metric("Kostprijs Δ", len(result["cost_diffs"]))

    miss_df = pd.DataFrame(result["missing"])
    cost_df = pd.DataFrame(result["cost_diffs"])
    sale_df = pd.DataFrame(result["sale_diffs"])

    tabs = st.tabs([
        f"❓ Ontbrekend ({len(miss_df)})",
        f"📥 Kostprijs ({len(cost_df)})",
        f"📤 Verkoopprijs ({len(sale_df)})",
    ])

    odoo = get_odoo()

    # ----- TAB 1: MISSING -----
    with tabs[0]:
        if miss_df.empty:
            st.success("Alle VBD producten zitten al in Odoo 🎉")
        else:
            st.caption("Selecteer producten om toe te voegen aan Odoo (template + supplierinfo voor VBD).")
            search = st.text_input("🔍 Filter", key="vbd_miss_search")
            df_show = miss_df.copy()
            if search:
                mask = df_show.apply(
                    lambda r: search.lower() in " ".join(str(v) for v in r.values).lower(),
                    axis=1)
                df_show = df_show[mask]
            df_show.insert(0, "Selecteer", False)
            edited = st.data_editor(
                df_show, hide_index=True, use_container_width=True,
                disabled=[c for c in df_show.columns if c != "Selecteer"],
                column_config={
                    "price_incl": st.column_config.NumberColumn("Incl BTW", format="€ %.2f"),
                    "price_excl": st.column_config.NumberColumn("Excl BTW (kost)", format="€ %.2f"),
                    "url": st.column_config.LinkColumn("Bekijk"),
                    "image_url": None, "category": None, "description": None,
                },
                key="vbd_miss_editor",
            )
            sel = edited[edited["Selecteer"]]
            st.markdown(f"**{len(sel)} geselecteerd**")
            if not sel.empty:
                margin = st.slider("Marge voor verkoopprijs (×)", 1.0, 3.0, DEFAULT_MARGIN, 0.05,
                                    key="vbd_miss_margin",
                                    help="Verkoopprijs = kost (excl BTW) × marge. Excl BTW.")
                use_vbd_sale = st.checkbox(
                    "Gebruik VBD incl-BTW prijs als verkoopprijs (i.p.v. kost × marge)",
                    value=False, key="vbd_use_incl",
                    help="Aanvinken als jullie dezelfde verkoopprijs willen aanhouden als VBD.")
                if st.button(f"➕ Voeg {len(sel)} toe in Odoo", key="vbd_add_miss", type="primary"):
                    added = errs = 0
                    for _, r in sel.iterrows():
                        try:
                            cost = float(r["price_excl"])
                            if use_vbd_sale and r.get("price_incl"):
                                sale = float(r["price_incl"])
                            else:
                                sale = round(cost * margin, 2)
                            tid = odoo.create("product.template", {
                                "name": str(r["name"]).strip(),
                                "default_code": str(r["sku"]).strip(),
                                "type": "consu",
                                "is_storable": True,
                                "standard_price": cost,
                                "list_price": sale,
                                "description_sale": str(r.get("description") or "")[:1000],
                            })
                            odoo.create("product.supplierinfo", {
                                "partner_id": VBD_PARTNER_ID,
                                "product_tmpl_id": tid,
                                "product_code": str(r["sku"]).strip(),
                                "price": cost, "min_qty": 1, "delay": 3,
                            })
                            added += 1
                        except Exception as e:
                            errs += 1
                            st.error(f"  {r['sku']}: {e}")
                    st.success(f"✓ {added} toegevoegd · {errs} fout")
                    # Force re-analyze
                    st.session_state.pop("_vbd_result", None)

    # ----- TAB 2: COST DIFFS -----
    with tabs[1]:
        if cost_df.empty:
            st.success("Geen kostprijs verschillen 🎉")
        else:
            st.caption("Vink aan en pas toe om supplierinfo.price (kostprijs excl BTW) bij te werken.")
            df_show = cost_df.copy()
            df_show["Δ"] = df_show["new_pricenett"] - df_show["current_supplier_price"]
            df_show.insert(0, "Selecteer", True)
            edited = st.data_editor(
                df_show, hide_index=True, use_container_width=True,
                disabled=[c for c in df_show.columns if c != "Selecteer"],
                column_config={
                    "current_supplier_price": st.column_config.NumberColumn("Huidig", format="€ %.2f"),
                    "new_pricenett": st.column_config.NumberColumn("VBD excl", format="€ %.2f"),
                    "Δ": st.column_config.NumberColumn("Δ", format="€ %.2f"),
                    "supplierinfo_id": None, "template_id": None,
                },
                key="vbd_cost_editor",
            )
            sel = edited[edited["Selecteer"]]
            if not sel.empty and st.button(f"✓ Pas {len(sel)} kostprijs updates toe",
                                            type="primary", key="vbd_apply_cost"):
                ok = err = 0
                for _, r in sel.iterrows():
                    try:
                        odoo.write("product.supplierinfo", [int(r["supplierinfo_id"])],
                                    {"price": float(r["new_pricenett"])})
                        ok += 1
                    except Exception as e:
                        err += 1
                        st.error(f"{r['sku']}: {e}")
                st.success(f"✓ {ok} updates toegepast, {err} fout")

    # ----- TAB 3: SALE DIFFS -----
    with tabs[2]:
        if sale_df.empty:
            st.success("Geen verkoopprijs verschillen 🎉")
        else:
            st.caption("Pas marge aan of gebruik VBD incl-BTW als referentie.")
            global_margin = st.slider("Globale marge (× kostprijs)", 1.0, 3.0, DEFAULT_MARGIN, 0.05,
                                       key="vbd_sale_margin")
            df_show = sale_df.copy()
            tmpl_ids = df_show["template_id"].astype(int).tolist()
            tmpls = odoo.read("product.template", tmpl_ids,
                                ["standard_price"]) if tmpl_ids else []
            cost_by_tmpl = {t["id"]: t["standard_price"] for t in tmpls}
            df_show["kostprijs"] = df_show["template_id"].astype(int).map(cost_by_tmpl)
            df_show["voorgesteld"] = (df_show["kostprijs"] * global_margin).round(2)
            df_show["Δ huidige"] = df_show["vbd_incl_btw"] - df_show["current_list_price"]
            df_show["Toepassen"] = "Voorgesteld"
            df_show.insert(0, "Selecteer", True)
            edited = st.data_editor(
                df_show, hide_index=True, use_container_width=True,
                disabled=[c for c in df_show.columns if c not in ("Selecteer", "Toepassen")],
                column_config={
                    "current_list_price": st.column_config.NumberColumn("Huidig", format="€ %.2f"),
                    "vbd_incl_btw": st.column_config.NumberColumn("VBD incl", format="€ %.2f"),
                    "kostprijs": st.column_config.NumberColumn("Kost", format="€ %.2f"),
                    "voorgesteld": st.column_config.NumberColumn("Kost×marge", format="€ %.2f"),
                    "Δ huidige": st.column_config.NumberColumn("Δ", format="€ %.2f"),
                    "Toepassen": st.column_config.SelectboxColumn(
                        options=["Voorgesteld", "VBD incl", "Skip"]),
                    "template_id": None,
                },
                key="vbd_sale_editor",
            )
            sel = edited[edited["Selecteer"]]
            if not sel.empty and st.button(f"✓ Pas {len(sel)} verkoopprijs updates toe",
                                            type="primary", key="vbd_apply_sale"):
                ok = err = 0
                for _, r in sel.iterrows():
                    if r["Toepassen"] == "Skip": continue
                    new_price = float(r["voorgesteld"]) if r["Toepassen"] == "Voorgesteld" else float(r["vbd_incl_btw"])
                    try:
                        odoo.write("product.template", [int(r["template_id"])],
                                    {"list_price": new_price})
                        ok += 1
                    except Exception as e:
                        err += 1
                        st.error(f"{r['sku']}: {e}")
                st.success(f"✓ {ok} updates toegepast, {err} fout")
