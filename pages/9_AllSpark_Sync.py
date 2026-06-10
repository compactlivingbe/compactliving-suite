"""All-Spark sync — publieke scrape, korting → inkoopprijs, change-detectie."""
import os
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient
import gh_storage as ghs
import discounts as disc
import supplier_diff as sdiff
import odoo_products as op
from suppliers import get as get_supplier

try:
    st.set_page_config(page_title="All-Spark sync", page_icon="🔌", layout="wide")
except Exception:
    pass

from auth import require_auth
require_auth()

st.title("🔌 All-Spark sync")
st.caption("Scrape de publieke All-Spark webshop, bereken inkoopprijzen via korting "
           "en zie wat er veranderde t.o.v. de vorige keer + t.o.v. Odoo.")

SNAPSHOT_FILE = "allspark_snapshot.json"
DISCOUNTS_FILE = "allspark_discounts.json"
EXCLUSIONS_FILE = "supplier_exclusions.json"
SUPPLIER = get_supplier("allspark")
DEFAULT_MARGIN = 1.32


def get_odoo():
    return OdooClient(url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
                      login=os.environ["ODOO_LOGIN"],
                      api_key=os.environ.get("ODOO_API_KEY", ""))


def load_discounts():
    return disc.normalize(ghs.load_json(DISCOUNTS_FILE, default=disc.empty_config()))


def load_exclusions():
    data = ghs.load_json(EXCLUSIONS_FILE, default={})
    return data.get("allspark", {"products": [], "categories": []})


def save_exclusions(allspark_excl, msg):
    data = ghs.load_json(EXCLUSIONS_FILE, default={})
    data["allspark"] = allspark_excl
    return ghs.save_json(EXCLUSIONS_FILE, data, msg)


# ============ INSTELLINGEN ============
with st.expander("⚙️ Korting-instellingen (inkoop = publiek × (1 − korting))", expanded=False):
    st.caption("Precedentie: **product > categorie > merk > default**. "
               "Korting als percentage (30 = 30%).")
    cfg = load_discounts()
    c0, _ = st.columns([1, 3])
    with c0:
        default_pct = st.number_input("Default korting %", 0.0, 95.0,
                                      value=round(cfg["default"] * 100, 1), step=1.0)

    def _dict_editor(title, d, key):
        st.markdown(f"**{title}**")
        rows = [{"sleutel": k, "korting_%": round(v * 100, 1)} for k, v in d.items()]
        if not rows:
            rows = [{"sleutel": "", "korting_%": 0.0}]
        edited = st.data_editor(pd.DataFrame(rows), hide_index=True, num_rows="dynamic",
                                use_container_width=True, key=key,
                                column_config={
                                    "sleutel": st.column_config.TextColumn(width="large"),
                                    "korting_%": st.column_config.NumberColumn(format="%.1f")})
        out = {}
        for _, r in edited.iterrows():
            k = str(r["sleutel"]).strip()
            if k:
                try:
                    out[k] = float(r["korting_%"]) / 100.0
                except (TypeError, ValueError):
                    pass
        return out

    ec1, ec2, ec3 = st.columns(3)
    with ec1:
        by_brand = _dict_editor("Per merk", cfg["by_brand"], "disc_brand")
    with ec2:
        by_cat = _dict_editor("Per categorie", cfg["by_category"], "disc_cat")
    with ec3:
        by_prod = _dict_editor("Per product (code)", cfg["by_product"], "disc_prod")

    if st.button("💾 Korting-config opslaan", type="primary"):
        new_cfg = {"default": default_pct / 100.0, "by_brand": by_brand,
                   "by_category": by_cat, "by_product": by_prod}
        pushed, info = ghs.save_json(DISCOUNTS_FILE, new_cfg, "All-Spark korting bijgewerkt")
        st.success(f"✓ Opgeslagen{' + GitHub ' + info if pushed else ' (lokaal)'}")


with st.expander("🚫 Uitsluitingen (categorieën / producten niet syncen)", expanded=False):
    excl = load_exclusions()
    cc1, cc2 = st.columns(2)
    with cc1:
        excl_cats = st.text_area("Uitgesloten categorieën (één per regel)",
                                 value="\n".join(excl.get("categories", [])), height=140)
    with cc2:
        excl_prods = st.text_area("Uitgesloten product-codes (één per regel)",
                                  value="\n".join(excl.get("products", [])), height=140)
    if st.button("💾 Uitsluitingen opslaan"):
        new_excl = {
            "categories": [x.strip() for x in excl_cats.splitlines() if x.strip()],
            "products": [x.strip() for x in excl_prods.splitlines() if x.strip()],
        }
        pushed, info = save_exclusions(new_excl, "All-Spark uitsluitingen bijgewerkt")
        st.success(f"✓ Opgeslagen{' + GitHub ' + info if pushed else ' (lokaal)'}")


# ============ SCRAPE ============
st.divider()
sc1, sc2, sc3 = st.columns([2, 1, 1])
with sc1:
    st.markdown("### ▶ Scrapen")
with sc2:
    with_cats = st.checkbox("Categorieën + merken meenemen", value=True,
                            help="Crawlt ook alle categorie-pagina's (trager) om merk/"
                                 "categorie per product te bepalen.")
with sc3:
    test_mode = st.checkbox("Testmodus (max 5 pagina's)", value=False)

if st.button("🔄 Scrape All-Spark", type="primary"):
    log_box = st.empty()
    logs = []

    def log(msg):
        logs.append(str(msg))
        log_box.code("\n".join(logs[-25:]), language="")

    with st.spinner("Scrapen..."):
        products = SUPPLIER.fetch(log=log, with_categories=with_cats,
                                  max_pages=5 if test_mode else None)
    # inkoopprijs berekenen
    cfg = load_discounts()
    for code, p in products.items():
        d, src = disc.resolve_discount(cfg, code, p.get("brand", ""), p.get("category", ""))
        p["discount"] = d
        p["discount_source"] = src
        p["cost_price"] = disc.cost_from_public(p.get("public_price"), d)

    prev = ghs.load_json(SNAPSHOT_FILE, default={}).get("products", {})
    snap = {"scraped_at": datetime.now().isoformat(timespec="seconds"),
            "products": products}
    pushed, info = ghs.save_json(SNAPSHOT_FILE, snap,
                                 f"All-Spark snapshot {len(products)} producten")
    st.session_state["_as_products"] = products
    st.session_state["_as_prev"] = prev
    st.success(f"✓ {len(products)} producten gescrapet · snapshot opgeslagen"
               f"{' + GitHub ' + info if pushed else ' (lokaal)'}")


# ============ RESULTATEN ============
products = st.session_state.get("_as_products")
if not products:
    saved = ghs.load_json(SNAPSHOT_FILE, default={})
    if saved.get("products"):
        products = saved["products"]
        st.info(f"Toont laatst opgeslagen snapshot ({saved.get('scraped_at', '?')}). "
                "Klik **Scrape All-Spark** voor verse data.")

if products:
    st.divider()
    prev = st.session_state.get("_as_prev", {})

    # ---- vergelijking met vorige scrape ----
    snap_diff = sdiff.diff_snapshots(prev, products) if prev else None
    # ---- vergelijking met Odoo ----
    odoo = get_odoo()
    partner_id = SUPPLIER.partner_id(odoo)
    odoo_index = {}
    if partner_id:
        odoo_index = op.build_supplier_index(odoo, partner_id)
    else:
        st.warning("Geen All-Spark res.partner gevonden in Odoo. Stel "
                   "`ALLSPARK_PARTNER_ID` in als secret, of controleer de partnernaam.")
    excl = load_exclusions()
    vsodoo = sdiff.diff_vs_odoo(products, odoo_index, SUPPLIER.compute_cost, excl)

    n_pc = len(snap_diff["price_changes"]) if snap_diff else 0
    n_new = len(snap_diff["added"]) if snap_diff else 0
    n_rem = len(snap_diff["removed"]) if snap_diff else 0
    m = st.columns(5)
    m[0].metric("Producten", len(products))
    m[1].metric("Prijswijziging", n_pc)
    m[2].metric("Nieuw (scrape)", n_new)
    m[3].metric("Ontbreekt in Odoo", len(vsodoo["missing"]))
    m[4].metric("Kostprijsverschil", len(vsodoo["cost_diffs"]))

    tabs = st.tabs([
        f"💱 Prijswijzigingen ({n_pc})",
        f"🆕 Nieuw t.o.v. vorige ({n_new})",
        f"❌ Verdwenen ({n_rem})",
        "🗂️ Categorieën",
        f"➕ Ontbreekt in Odoo ({len(vsodoo['missing'])})",
        f"📥 Kostprijs vs Odoo ({len(vsodoo['cost_diffs'])})",
    ])

    # --- prijswijzigingen sinds vorige scrape ---
    with tabs[0]:
        if not snap_diff or not snap_diff["price_changes"]:
            st.success("Geen prijswijzigingen t.o.v. de vorige scrape.")
        else:
            df = pd.DataFrame(snap_diff["price_changes"])
            st.dataframe(df, hide_index=True, use_container_width=True,
                         column_config={
                             "old_price": st.column_config.NumberColumn("Oud", format="€ %.2f"),
                             "new_price": st.column_config.NumberColumn("Nieuw", format="€ %.2f"),
                             "delta": st.column_config.NumberColumn("Δ", format="€ %.2f")})

    with tabs[1]:
        if not snap_diff or not snap_diff["added"]:
            st.info("Geen nieuwe producten t.o.v. de vorige scrape (of geen vorige scrape).")
        else:
            st.dataframe(pd.DataFrame(snap_diff["added"])[["code", "name", "brand", "category", "public_price"]],
                         hide_index=True, use_container_width=True)

    with tabs[2]:
        if not snap_diff or not snap_diff["removed"]:
            st.info("Geen verdwenen producten t.o.v. de vorige scrape.")
        else:
            st.dataframe(pd.DataFrame(snap_diff["removed"])[["code", "name", "brand", "category"]],
                         hide_index=True, use_container_width=True)

    with tabs[3]:
        if snap_diff and (snap_diff["categories_added"] or snap_diff["categories_removed"]):
            cca, ccb = st.columns(2)
            with cca:
                st.markdown("**Nieuwe categorieën**")
                st.write(snap_diff["categories_added"] or "—")
            with ccb:
                st.markdown("**Verdwenen categorieën**")
                st.write(snap_diff["categories_removed"] or "—")
        else:
            st.info("Geen categorie-wijzigingen t.o.v. de vorige scrape.")
        allcats = sorted({p.get("category", "") for p in products.values() if p.get("category")})
        st.caption(f"{len(allcats)} categorieën in huidige scrape")
        st.write(allcats)

    # --- ontbreekt in Odoo -> import ---
    with tabs[4]:
        if not vsodoo["missing"]:
            st.success("Alle gescrapete producten bestaan al in Odoo (voor deze leverancier).")
        else:
            st.caption("Selecteer en importeer in Odoo (incl. foto). Verkoopprijs = "
                       "kostprijs × marge.")
            margin = st.slider("Marge (×)", 1.0, 3.0, DEFAULT_MARGIN, 0.05, key="as_margin")
            df = pd.DataFrame(vsodoo["missing"])
            df.insert(0, "Selecteer", False)
            edited = st.data_editor(
                df, hide_index=True, use_container_width=True,
                disabled=[c for c in df.columns if c != "Selecteer"],
                column_config={
                    "public_price": st.column_config.NumberColumn("Publiek", format="€ %.2f"),
                    "cost_price": st.column_config.NumberColumn("Inkoop", format="€ %.2f"),
                    "image_url": None, "url": None},
                key="as_missing")
            sel = edited[edited["Selecteer"]]
            with_photo = st.checkbox("Foto mee importeren", value=True, key="as_photo")
            if not sel.empty and st.button(f"➕ Importeer {len(sel)} in Odoo", type="primary"):
                added = errs = 0
                for _, r in sel.iterrows():
                    try:
                        cost = None if pd.isna(r["cost_price"]) else float(r["cost_price"])
                        sale = round(cost * margin, 2) if cost is not None else None
                        img = op.download_image_b64(r["image_url"]) if with_photo else None
                        op.create_product(odoo, partner_id, str(r["code"]),
                                          str(r["name"]), cost, sale, image_b64=img)
                        added += 1
                    except Exception as e:
                        errs += 1
                        st.error(f"{r['code']}: {e}")
                st.success(f"✓ {added} geïmporteerd · {errs} fout")

    # --- kostprijs vs Odoo -> apply ---
    with tabs[5]:
        if not vsodoo["cost_diffs"]:
            st.success("Geen kostprijsverschillen met Odoo.")
        else:
            st.caption("Vink aan en pas toe om supplierinfo.price in Odoo te updaten.")
            df = pd.DataFrame(vsodoo["cost_diffs"])
            df.insert(0, "Selecteer", True)
            edited = st.data_editor(
                df, hide_index=True, use_container_width=True,
                disabled=[c for c in df.columns if c != "Selecteer"],
                column_config={
                    "current_cost": st.column_config.NumberColumn("Huidig", format="€ %.2f"),
                    "new_cost": st.column_config.NumberColumn("Nieuw", format="€ %.2f"),
                    "delta": st.column_config.NumberColumn("Δ", format="€ %.2f"),
                    "supplierinfo_id": None, "tmpl_id": None},
                key="as_cost")
            sel = edited[edited["Selecteer"]]
            if not sel.empty and st.button(f"✓ Pas {len(sel)} kostprijzen toe", type="primary"):
                ok = err = 0
                for _, r in sel.iterrows():
                    try:
                        op.update_cost(odoo, int(r["supplierinfo_id"]), float(r["new_cost"]))
                        ok += 1
                    except Exception as e:
                        err += 1
                        st.error(f"{r['code']}: {e}")
                st.success(f"✓ {ok} toegepast · {err} fout")
