"""Sync-dashboard — overzicht van leverancier-syncs en Victron-dekking.

Read-only. Brengt samen wat de andere pagina's produceren:
- Victron master-catalogus (welke producten bestaan)
- Odoo-aanwezigheid + leverancier-supplierinfos (Top Systems, All-Spark)
- laatste All-Spark scrape

Geeft per categorie de dekking, per leverancier de status, en een eerste
"waar koop ik goedkoopst"-vergelijking voor producten die bij beide
leveranciers beschikbaar zijn.
"""
import os
import sys
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient
import gh_storage as ghs
import odoo_products as op
from suppliers import get as get_supplier

try:
    st.set_page_config(page_title="Sync-dashboard", page_icon="📊", layout="wide")
except Exception:
    pass

from auth import require_auth
require_auth()

st.title("📊 Sync-dashboard")
st.caption("Overzicht van leverancier-syncs en de Victron-dekking in Odoo.")

MASTER_FILE = "master_catalog.json"
ALLSPARK_SNAPSHOT = "allspark_snapshot.json"
EXCLUSIONS_FILE = "supplier_exclusions.json"
TS_PARTNER_ID = 690


def get_odoo():
    return OdooClient(url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
                      login=os.environ["ODOO_LOGIN"],
                      api_key=os.environ.get("ODOO_API_KEY", ""))


def load_exclusions() -> tuple[set[str], set[str]]:
    """Lees de Victron-uitsluitingen (gedeeld supplier_exclusions.json)."""
    raw = ghs.load_json(EXCLUSIONS_FILE, default={})
    v = raw.get("victron", {}) if isinstance(raw, dict) else {}
    prods = {str(c).strip() for c in v.get("products", []) if str(c).strip()}
    cats = {str(c).strip() for c in v.get("categories", []) if str(c).strip()}
    return prods, cats


def odoo_present_codes(odoo, codes: list[str]) -> set[str]:
    present: set[str] = set()
    for i in range(0, len(codes), 200):
        chunk = codes[i:i + 200]
        for r in odoo.search_read("product.template",
                                  [["default_code", "in", chunk]],
                                  ["default_code"], limit=len(chunk)):
            if r.get("default_code"):
                present.add(r["default_code"].strip())
    return present


# ============ DATA LADEN ============
master_raw = ghs.load_json(MASTER_FILE, default={})
master = master_raw.get("products", {}) if isinstance(master_raw, dict) else {}
as_snap = ghs.load_json(ALLSPARK_SNAPSHOT, default={})
as_products = as_snap.get("products", {}) or {}
excl_prod, excl_cat = load_exclusions()


def _is_excluded(code: str) -> bool:
    p = master.get(code, {})
    cat = (p.get("category", "") or "").strip()
    return code in excl_prod or cat in excl_cat

odoo = get_odoo()
with st.spinner("Odoo-leveranciersdata laden..."):
    ts_index = op.build_supplier_index(odoo, TS_PARTNER_ID)
    as_supplier = get_supplier("allspark")
    as_partner = as_supplier.partner_id(odoo)
    as_index = op.build_supplier_index(odoo, as_partner) if as_partner else {}
    master_codes = sorted(master.keys())
    in_odoo = odoo_present_codes(odoo, master_codes) if master_codes else set()

in_ts = set(ts_index.keys())
in_as_odoo = set(as_index.keys())
in_as_scrape = set(as_products.keys())

# Actieve (niet-uitgesloten) Victron-codes vormen de basis voor de dekking.
active_codes = [c for c in master_codes if not _is_excluded(c)]
n_excluded = len(master_codes) - len(active_codes)
n_new = sum(1 for c, p in master.items() if p.get("is_new") and not _is_excluded(c))

# ============ KPI's ============
m = st.columns(6)
m[0].metric("Victron-master", len(master),
            help=f"{n_excluded} uitgesloten" if n_excluded else None)
m[1].metric("Nieuw 🆕", n_new)
m[2].metric("In Odoo", len([c for c in active_codes if c in in_odoo]))
m[3].metric("Top Systems (Odoo)", len(in_ts))
m[4].metric("All-Spark (Odoo)", len(in_as_odoo))
if master:
    nowhere = sum(1 for c in active_codes if c not in in_ts and c not in in_as_scrape)
    m[5].metric("Victron nergens te koop", nowhere)
else:
    m[5].metric("All-Spark scrape", len(in_as_scrape))

st.divider()
tab_dekking, tab_nieuw, tab_lev, tab_goedkoop = st.tabs(
    ["🔋 Victron-dekking", "🆕 Nieuwe producten", "🏷️ Per leverancier",
     "💸 Goedkoopste bron"])

# ---- Victron-dekking per categorie ----
with tab_dekking:
    if not master:
        st.info("Nog geen Victron master-catalogus. Lees eerst de prijslijst in "
                "op de pagina **Victron catalogus**.")
    else:
        if n_excluded:
            st.caption(f"Dekking berekend over {len(active_codes)} actieve producten "
                       f"({n_excluded} uitgesloten genegeerd).")
        cats: dict[str, dict] = {}
        for code in active_codes:
            cat = master[code].get("category", "") or "(geen categorie)"
            d = cats.setdefault(cat, {"totaal": 0, "nieuw": 0, "in_odoo": 0,
                                      "top_systems": 0, "all_spark": 0,
                                      "geen_leverancier": 0})
            d["totaal"] += 1
            d["nieuw"] += int(bool(master[code].get("is_new")))
            d["in_odoo"] += int(code in in_odoo)
            d["top_systems"] += int(code in in_ts)
            d["all_spark"] += int(code in in_as_scrape)
            d["geen_leverancier"] += int(code not in in_ts and code not in in_as_scrape)
        rows = [{"categorie": cat, **d} for cat, d in sorted(cats.items())]
        ddf = pd.DataFrame(rows)
        ov = st.columns(4)
        ov[0].metric("Dekking Odoo",
                     f"{len([c for c in active_codes if c in in_odoo])}/{len(active_codes)}")
        ov[1].metric("Via Top Systems", f"{len([c for c in active_codes if c in in_ts])}")
        ov[2].metric("Via All-Spark", f"{len([c for c in active_codes if c in in_as_scrape])}")
        ov[3].metric("Categorieën", len(cats))
        st.dataframe(ddf, hide_index=True, use_container_width=True,
                     column_config={
                         "categorie": st.column_config.TextColumn(width="large"),
                         "totaal": "Totaal", "nieuw": "Nieuw 🆕", "in_odoo": "In Odoo",
                         "top_systems": "Top Systems", "all_spark": "All-Spark",
                         "geen_leverancier": "Geen leverancier"})
        st.download_button("⬇️ Exporteer dekking (CSV)",
                           ddf.to_csv(index=False).encode("utf-8"),
                           "victron_dekking_per_categorie.csv", "text/csv")

# ---- Nieuwe Victron-producten ----
with tab_nieuw:
    new_codes = [c for c in active_codes if master[c].get("is_new")]
    if not master:
        st.info("Nog geen Victron master-catalogus ingelezen.")
    elif not new_codes:
        st.info("Geen producten als nieuw gemarkeerd in de laatst ingelezen prijslijst.")
    else:
        st.caption("Producten die in de Victron-prijslijst als nieuw (🆕) zijn "
                   "gemarkeerd. Handig om proactief in te kopen / aan te maken.")
        nc = st.columns(4)
        nc[0].metric("Nieuw totaal", len(new_codes))
        nc[1].metric("Al in Odoo", len([c for c in new_codes if c in in_odoo]))
        nc[2].metric("Bij Top Systems", len([c for c in new_codes if c in in_ts]))
        nc[3].metric("Bij All-Spark", len([c for c in new_codes if c in in_as_scrape]))
        nrows = [{
            "code": c,
            "naam": master[c].get("name", ""),
            "categorie": master[c].get("category", "") or "(geen categorie)",
            "adviesprijs": master[c].get("advice_price"),
            "in_odoo": c in in_odoo,
            "top_systems": c in in_ts,
            "all_spark": c in in_as_scrape,
        } for c in sorted(new_codes)]
        ndf = pd.DataFrame(nrows)
        st.dataframe(ndf, hide_index=True, use_container_width=True,
                     column_config={
                         "naam": st.column_config.TextColumn(width="large"),
                         "adviesprijs": st.column_config.NumberColumn(
                             "Adviesprijs", format="€ %.2f"),
                         "in_odoo": st.column_config.CheckboxColumn("In Odoo"),
                         "top_systems": st.column_config.CheckboxColumn("Top Systems"),
                         "all_spark": st.column_config.CheckboxColumn("All-Spark")})
        st.download_button("⬇️ Exporteer nieuwe producten (CSV)",
                           ndf.to_csv(index=False).encode("utf-8"),
                           "victron_nieuwe_producten.csv", "text/csv")

# ---- Per leverancier ----
with tab_lev:
    lc1, lc2 = st.columns(2)
    with lc1:
        with st.container(border=True):
            st.markdown("#### 💰 Top Systems")
            st.metric("Producten in Odoo (supplierinfo)", len(in_ts))
            if master:
                st.caption(f"Waarvan Victron-master: "
                           f"{len([c for c in in_ts if c in master])}")
            st.page_link("pages/4_TopSystems_Prijzen.py", label="Open Top Systems sync",
                         icon="➡️")
    with lc2:
        with st.container(border=True):
            st.markdown("#### 🔌 All-Spark")
            st.metric("Laatste scrape", len(in_as_scrape))
            st.caption(f"Gescrapet: {as_snap.get('scraped_at', '—')}")
            st.metric("In Odoo (supplierinfo)", len(in_as_odoo))
            if not as_partner:
                st.warning("Geen All-Spark res.partner — stel `ALLSPARK_PARTNER_ID` in.")
            new_not_odoo = len(in_as_scrape - in_as_odoo)
            st.caption(f"Gescrapet maar niet in Odoo: {new_not_odoo}")
            st.page_link("pages/9_AllSpark_Sync.py", label="Open All-Spark sync", icon="➡️")

    if as_products:
        st.markdown("##### All-Spark — merkverdeling (laatste scrape)")
        brand_count: dict[str, int] = {}
        for p in as_products.values():
            b = p.get("brand") or "(onbekend)"
            brand_count[b] = brand_count.get(b, 0) + 1
        bdf = pd.DataFrame(sorted(brand_count.items(), key=lambda x: -x[1]),
                           columns=["merk", "aantal"])
        st.dataframe(bdf, hide_index=True, use_container_width=True)

# ---- Goedkoopste bron ----
with tab_goedkoop:
    st.caption("Voor producten die bij **beide** leveranciers beschikbaar zijn, "
               "vergelijken we de inkoopprijs. Top Systems = pricenett (excl BTW); "
               "All-Spark = berekend met de korting-config van de laatste scrape.")
    # TS-kostprijs uit Odoo supplierinfo; All-Spark-kostprijs uit scrape-snapshot.
    overlap = sorted(set(ts_index.keys()) & set(as_products.keys()))
    rows = []
    for code in overlap:
        ts_cost = ts_index.get(code, {}).get("supplier_price")
        as_cost = as_products.get(code, {}).get("cost_price")
        if ts_cost is None or as_cost is None:
            continue
        ts_cost = float(ts_cost)
        as_cost = float(as_cost)
        cheaper = "Top Systems" if ts_cost <= as_cost else "All-Spark"
        rows.append({
            "code": code,
            "naam": as_products[code].get("name", "")
                    or ts_index[code].get("name", ""),
            "top_systems": ts_cost,
            "all_spark": as_cost,
            "goedkoopste": cheaper,
            "besparing": round(abs(ts_cost - as_cost), 2),
        })
    if not rows:
        st.info("Geen producten gevonden die bij beide leveranciers beschikbaar zijn "
                "met een bekende inkoopprijs. Scrape All-Spark (met korting-config) en "
                "zorg dat Top Systems-supplierinfos een prijs hebben.")
    else:
        gdf = pd.DataFrame(rows)
        gc = st.columns(3)
        gc[0].metric("Overlappende producten", len(gdf))
        gc[1].metric("Goedkoopst bij Top Systems", int((gdf["goedkoopste"] == "Top Systems").sum()))
        gc[2].metric("Goedkoopst bij All-Spark", int((gdf["goedkoopste"] == "All-Spark").sum()))
        st.dataframe(gdf, hide_index=True, use_container_width=True,
                     column_config={
                         "top_systems": st.column_config.NumberColumn("Top Systems", format="€ %.2f"),
                         "all_spark": st.column_config.NumberColumn("All-Spark", format="€ %.2f"),
                         "besparing": st.column_config.NumberColumn("Verschil", format="€ %.2f")})
        st.download_button("⬇️ Exporteer vergelijking (CSV)",
                           gdf.to_csv(index=False).encode("utf-8"),
                           "goedkoopste_bron.csv", "text/csv")
