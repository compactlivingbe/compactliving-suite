"""Victron master-catalogus — dé lijst van bestaande Victron-producten.

Bron = de officiële Victron-prijslijst (PDF). Hier zie je per Victron-product
of het al in Odoo staat en bij welke leverancier(s) (Top Systems / All-Spark)
het te koop is — ook producten die geen enkele leverancier voert blijven
zichtbaar. Per product kun je foto/omschrijving/kenmerken aanvullen en naar
Odoo pushen (kenmerken worden Odoo-attributen, zoals nu het geval is).
"""
import os
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient
import gh_storage as ghs
import odoo_products as op
import victron_pricelist as vpl

try:
    st.set_page_config(page_title="Victron catalogus", page_icon="🔋", layout="wide")
except Exception:
    pass

from auth import require_auth
require_auth()

st.title("🔋 Victron master-catalogus")
st.caption("De officiële Victron-prijslijst als bron van waarheid: welke producten "
           "bestaan, of ze in Odoo staan en bij welke leverancier(s) ze te koop zijn.")

MASTER_FILE = "master_catalog.json"
ALLSPARK_SNAPSHOT = "allspark_snapshot.json"
EXCLUSIONS_FILE = "supplier_exclusions.json"
OVERRIDES_FILE = "victron_overrides.json"   # handmatige correcties, overleven her-import
TS_PARTNER_ID = 690          # Top Systems res.partner
VICTRON_CATEG_DEFAULT = 154  # Victron-categorie in Odoo (zie topsystems_sync)
DEFAULT_MARGIN = 1.32
# Velden die je handmatig kunt overschrijven bovenop de geparste catalogus.
OVERRIDE_FIELDS = ("name", "image_url", "description", "specs")


def get_odoo():
    return OdooClient(url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
                      login=os.environ["ODOO_LOGIN"],
                      api_key=os.environ.get("ODOO_API_KEY", ""))


def load_overrides() -> dict:
    """Per-code handmatige correcties ({code: {veld: waarde}})."""
    data = ghs.load_json(OVERRIDES_FILE, default={})
    return data if isinstance(data, dict) else {}


def save_override(code: str, fields: dict, msg: str):
    data = load_overrides()
    cur = data.get(code, {})
    cur.update(fields)
    # Lege waarden weer verwijderen zodat de geparste waarde terugkomt.
    cur = {k: v for k, v in cur.items() if v not in (None, "", {}, [])}
    if cur:
        data[code] = cur
    else:
        data.pop(code, None)
    return ghs.save_json(OVERRIDES_FILE, data, msg)


def apply_overrides(master: dict, overrides: dict) -> int:
    """Leg de handmatige correcties bovenop de geparste catalogus. Geeft #toegepast."""
    n = 0
    for code, ov in overrides.items():
        if code in master and isinstance(ov, dict):
            for k, v in ov.items():
                if k in OVERRIDE_FIELDS and v not in (None, "", {}, []):
                    master[code][k] = v
            n += 1
    return n


def load_master() -> dict:
    data = ghs.load_json(MASTER_FILE, default={})
    master = data.get("products", {}) if isinstance(data, dict) else {}
    if master:
        apply_overrides(master, load_overrides())
    return master


def load_exclusions() -> dict:
    data = ghs.load_json(EXCLUSIONS_FILE, default={})
    return data.get("victron", {"products": [], "categories": []})


def save_exclusions(victron_excl, msg):
    data = ghs.load_json(EXCLUSIONS_FILE, default={})
    data["victron"] = victron_excl
    return ghs.save_json(EXCLUSIONS_FILE, data, msg)


def odoo_present_codes(odoo, codes: list[str]) -> set[str]:
    """Welke codes bestaan al als product (default_code óf supplierinfo-code)?"""
    present: set[str] = set()
    for i in range(0, len(codes), 200):
        chunk = codes[i:i + 200]
        for r in odoo.search_read("product.template",
                                  [["default_code", "in", chunk]],
                                  ["default_code"], limit=len(chunk)):
            if r.get("default_code"):
                present.add(r["default_code"].strip())
        for r in odoo.search_read("product.supplierinfo",
                                  [["product_code", "in", chunk]],
                                  ["product_code"], limit=len(chunk)):
            if r.get("product_code"):
                present.add(r["product_code"].strip())
    return present


# ============ 1. PRIJSLIJST INLEZEN ============
with st.expander("📄 Victron-prijslijst (PDF) inlezen", expanded=not load_master()):
    st.caption("Upload de officiële Victron-prijslijst (EX VAT). De catalogus wordt "
               "geparset en opgeslagen in de suite-repo.")
    up = st.file_uploader("Victron Pricelist PDF", type=["pdf"], key="vpl_pdf")
    if up is not None and st.button("📥 Parse + opslaan", type="primary"):
        log_box = st.empty()
        logs: list[str] = []

        def log(msg):
            logs.append(str(msg))
            log_box.code("\n".join(logs[-15:]), language="")

        with st.spinner("PDF parsen..."):
            try:
                prods = vpl.parse_pdf_bytes(up.getvalue(), log=log)
            except Exception as e:
                st.error(f"Parsen mislukt: {e}")
                prods = None
        if prods:
            payload = {"parsed_at": datetime.now().isoformat(timespec="seconds"),
                       "source_file": up.name, "products": prods}
            pushed, info = ghs.save_json(MASTER_FILE, payload,
                                         f"Victron master-catalogus {len(prods)} producten")
            st.success(f"✓ {len(prods)} producten ingelezen · opgeslagen"
                       f"{' + GitHub ' + info if pushed else ' (lokaal)'}")
            st.rerun()


master = load_master()
if not master:
    st.info("Nog geen master-catalogus. Lees hierboven eerst de Victron-prijslijst (PDF) in.")
    st.stop()

saved = ghs.load_json(MASTER_FILE, default={})
n_new_master = sum(1 for p in master.values() if p.get("is_new"))
n_overrides = sum(1 for c in load_overrides() if c in master)
ovr_txt = f" · ✏️ {n_overrides} handmatige correctie(s)" if n_overrides else ""
st.caption(f"Master-catalogus: **{len(master)}** Victron-producten "
           f"({n_new_master} nieuw 🆕) · ingelezen {saved.get('parsed_at', '?')} "
           f"uit `{saved.get('source_file', '?')}`{ovr_txt}")

# ============ UITSLUITINGEN (categorieën) ============
with st.expander("🚫 Categorieën uitsluiten", expanded=False):
    st.caption("Sluit hele categorieën in één keer uit. Losse producten sluit je uit "
               "via de tabel op **Overzicht & dekking** (kolom *Uitgesloten*) en bekijk "
               "je op het tabblad **🚫 Uitgesloten**.")
    excl = load_exclusions()
    excl_cats = st.text_area("Uitgesloten categorieën (één per regel)",
                             value="\n".join(excl.get("categories", [])), height=140,
                             key="vpl_excl_cats")
    if st.button("💾 Categorie-uitsluitingen opslaan", key="vpl_save_excl"):
        new_excl = {
            "categories": [x.strip() for x in excl_cats.splitlines() if x.strip()],
            "products": excl.get("products", []),   # product-uitsluitingen uit de tabel behouden
        }
        pushed, info = save_exclusions(new_excl, "Victron categorie-uitsluitingen bijgewerkt")
        st.success(f"✓ Opgeslagen{' + GitHub ' + info if pushed else ' (lokaal)'}")
        st.rerun()

excl = load_exclusions()
excl_prod_set = set(excl.get("products", []))
excl_cat_set = set(excl.get("categories", []))


def _is_excluded(code: str, category: str) -> bool:
    return code in excl_prod_set or (category or "") in excl_cat_set


# ============ 2. DEKKING BEPALEN ============
codes = sorted(master.keys())
odoo = get_odoo()

with st.spinner("Dekking bepalen (Odoo + leveranciers)..."):
    in_odoo = odoo_present_codes(odoo, codes)
    ts_index = op.build_supplier_index(odoo, TS_PARTNER_ID)
    in_ts = set(ts_index.keys())
    as_snap = ghs.load_json(ALLSPARK_SNAPSHOT, default={})
    in_as = set((as_snap.get("products") or {}).keys())

rows = []
for code in codes:
    p = master[code]
    cat = p.get("category", "")
    rows.append({
        "code": code,
        "nieuw": bool(p.get("is_new")),
        "naam": p.get("name", ""),
        "categorie": cat,
        "advies_excl": p.get("advice_price"),
        "in_odoo": code in in_odoo,
        "top_systems": code in in_ts,
        "all_spark": code in in_as,
        "uitgesloten": _is_excluded(code, cat),
    })
df = pd.DataFrame(rows)
df_act = df[~df["uitgesloten"]]   # actieve (niet-uitgesloten) producten

# ============ 3. DASHBOARD ============
m = st.columns(6)
m[0].metric("Victron-producten", len(df))
m[1].metric("Nieuw 🆕", int(df["nieuw"].sum()))
m[2].metric("In Odoo", int(df_act["in_odoo"].sum()))
m[3].metric("Bij Top Systems", int(df_act["top_systems"].sum()))
m[4].metric("Bij All-Spark", int(df_act["all_spark"].sum()))
m[5].metric("Nergens te koop", int((~df_act["top_systems"] & ~df_act["all_spark"]).sum()))

st.divider()
n_excluded = int(df["uitgesloten"].sum())
tab_overzicht, tab_excluded, tab_missing, tab_detail = st.tabs(
    [f"📋 Overzicht & dekking", f"🚫 Uitgesloten ({n_excluded})",
     "➕ Ontbreekt in Odoo", "✏️ Product verrijken"])

# ---- Overzicht ----
with tab_overzicht:
    fc1, fc2, fc3 = st.columns([2, 2, 2])
    with fc1:
        cats = ["(alle)"] + sorted({r["categorie"] for r in rows if r["categorie"]})
        fcat = st.selectbox("Categorie", cats, key="ov_cat")
    with fc2:
        fstatus = st.selectbox("Odoo-status", ["(alle)", "Wel in Odoo", "Niet in Odoo"],
                               key="ov_status")
    with fc3:
        fsupp = st.selectbox("Leverancier", ["(alle)", "Top Systems", "All-Spark",
                                             "Geen leverancier"], key="ov_supp")
    fc4, fc5, fc6 = st.columns([3, 1.5, 1.5])
    with fc4:
        zoek = st.text_input("🔍 Zoek (code of naam)", key="ov_zoek",
                             placeholder="bv. MultiPlus, PMP12, SolarSense…")
    with fc5:
        fnew = st.selectbox("Nieuw", ["(alle)", "Alleen nieuw", "Niet nieuw"], key="ov_new")
    with fc6:
        fexcl = st.selectbox("Uitsluiting", ["Verberg uitgesloten", "Toon alles",
                                             "Alleen uitgesloten"], key="ov_excl")

    view = df.copy()
    if fexcl == "Verberg uitgesloten":
        view = view[~view["uitgesloten"]]
    elif fexcl == "Alleen uitgesloten":
        view = view[view["uitgesloten"]]
    if fcat != "(alle)":
        view = view[view["categorie"] == fcat]
    if fstatus == "Wel in Odoo":
        view = view[view["in_odoo"]]
    elif fstatus == "Niet in Odoo":
        view = view[~view["in_odoo"]]
    if fsupp == "Top Systems":
        view = view[view["top_systems"]]
    elif fsupp == "All-Spark":
        view = view[view["all_spark"]]
    elif fsupp == "Geen leverancier":
        view = view[~view["top_systems"] & ~view["all_spark"]]
    if fnew == "Alleen nieuw":
        view = view[view["nieuw"]]
    elif fnew == "Niet nieuw":
        view = view[~view["nieuw"]]
    if zoek:
        q = zoek.lower()
        view = view[view["code"].str.lower().str.contains(q, na=False)
                    | view["naam"].str.lower().str.contains(q, na=False)]

    st.caption(f"{len(view)} producten · vink **Uitgesloten** aan/uit en klik "
               "**Uitsluitingen opslaan** om producten uit te sluiten.")
    edited_ov = st.data_editor(
        view, hide_index=True, use_container_width=True,
        disabled=["code", "nieuw", "naam", "categorie", "advies_excl",
                  "in_odoo", "top_systems", "all_spark"],
        column_config={
            "code": st.column_config.TextColumn("code", disabled=True),
            "nieuw": st.column_config.CheckboxColumn("🆕"),
            "advies_excl": st.column_config.NumberColumn("Advies (excl)", format="€ %.2f"),
            "in_odoo": st.column_config.CheckboxColumn("Odoo"),
            "top_systems": st.column_config.CheckboxColumn("Top Systems"),
            "all_spark": st.column_config.CheckboxColumn("All-Spark"),
            "uitgesloten": st.column_config.CheckboxColumn(
                "Uitgesloten", help="Aanvinken = uitsluiten van import en dekking"),
        },
        key="ov_editor")

    # Bepaal wijzigingen t.o.v. de huidige (zichtbare) status.
    changed = edited_ov[edited_ov["uitgesloten"] != view["uitgesloten"].values]
    bc1, bc2 = st.columns([1, 3])
    with bc1:
        save_excl = st.button(
            f"💾 Uitsluitingen opslaan{f' ({len(changed)})' if len(changed) else ''}",
            type="primary", disabled=changed.empty, key="ov_save_excl")
    with bc2:
        if not changed.empty:
            st.caption(f"{len(changed)} wijziging(en) nog niet opgeslagen.")
    if save_excl and not changed.empty:
        new_prod_excl = set(excl_prod_set)
        cat_blocked = []
        for _, r in edited_ov.iterrows():
            ccode = str(r["code"])
            cat_excl = (r["categorie"] or "") in excl_cat_set
            if r["uitgesloten"] and not cat_excl:
                new_prod_excl.add(ccode)
            elif not r["uitgesloten"]:
                new_prod_excl.discard(ccode)
                if cat_excl:
                    cat_blocked.append(ccode)
        new_excl = {
            "categories": sorted(excl_cat_set),
            "products": sorted(new_prod_excl),
        }
        pushed, info = save_exclusions(new_excl, "Victron uitsluitingen via tabel bijgewerkt")
        st.success(f"✓ {len(new_prod_excl)} product(en) uitgesloten"
                   f"{' + GitHub ' + info if pushed else ' (lokaal)'}")
        if cat_blocked:
            st.warning("Deze blijven uitgesloten via hun categorie (pas de categorie-"
                       f"uitsluiting aan in de sectie hierboven): {', '.join(cat_blocked[:10])}"
                       f"{'…' if len(cat_blocked) > 10 else ''}")
        st.rerun()

    st.download_button("⬇️ Exporteer (CSV)", edited_ov.to_csv(index=False).encode("utf-8"),
                       "victron_dekking.csv", "text/csv")

# ---- Uitgesloten ----
with tab_excluded:
    exc = df[df["uitgesloten"]].copy()
    if exc.empty:
        st.success("Geen producten uitgesloten. Sluit producten uit via de kolom "
                   "*Uitgesloten* op het tabblad **Overzicht & dekking**.")
    else:
        exc["via_categorie"] = exc["categorie"].isin(excl_cat_set)
        st.caption(f"{len(exc)} uitgesloten producten. Vink **Uitgesloten** uit en klik "
                   "**Wijzigingen opslaan** om ze weer op te nemen. Producten met "
                   "*Via categorie* ✓ zijn uitgesloten via hun categorie — die neem je weer "
                   "op via de sectie **Categorieën uitsluiten** bovenaan.")
        edited_exc = st.data_editor(
            exc[["code", "nieuw", "naam", "categorie", "via_categorie", "advies_excl",
                 "in_odoo", "top_systems", "all_spark", "uitgesloten"]],
            hide_index=True, use_container_width=True,
            disabled=["code", "nieuw", "naam", "categorie", "via_categorie",
                      "advies_excl", "in_odoo", "top_systems", "all_spark"],
            column_config={
                "nieuw": st.column_config.CheckboxColumn("🆕"),
                "via_categorie": st.column_config.CheckboxColumn("Via categorie"),
                "advies_excl": st.column_config.NumberColumn("Advies (excl)", format="€ %.2f"),
                "in_odoo": st.column_config.CheckboxColumn("Odoo"),
                "top_systems": st.column_config.CheckboxColumn("Top Systems"),
                "all_spark": st.column_config.CheckboxColumn("All-Spark"),
                "uitgesloten": st.column_config.CheckboxColumn(
                    "Uitgesloten", help="Uitvinken = weer opnemen"),
            },
            key="exc_editor")
        reinclude = edited_exc[~edited_exc["uitgesloten"]]
        if st.button(f"💾 Wijzigingen opslaan{f' ({len(reinclude)})' if len(reinclude) else ''}",
                     type="primary", disabled=reinclude.empty, key="exc_save"):
            new_prod_excl = set(excl_prod_set)
            cat_blocked = []
            for _, r in reinclude.iterrows():
                new_prod_excl.discard(str(r["code"]))
                if r["via_categorie"]:
                    cat_blocked.append(str(r["code"]))
            new_excl = {"categories": sorted(excl_cat_set), "products": sorted(new_prod_excl)}
            pushed, info = save_exclusions(new_excl, "Victron uitsluitingen via tabel bijgewerkt")
            st.success(f"✓ Bijgewerkt · {len(new_prod_excl)} product(en) nog uitgesloten"
                       f"{' + GitHub ' + info if pushed else ' (lokaal)'}")
            if cat_blocked:
                st.warning("Deze blijven uitgesloten via hun categorie (pas aan via "
                           f"**Categorieën uitsluiten**): {', '.join(cat_blocked[:10])}"
                           f"{'…' if len(cat_blocked) > 10 else ''}")
            st.rerun()
        st.download_button("⬇️ Exporteer uitgesloten (CSV)",
                           exc.to_csv(index=False).encode("utf-8"),
                           "victron_uitgesloten.csv", "text/csv")

# ---- Ontbreekt in Odoo ----
with tab_missing:
    miss = df_act[~df_act["in_odoo"]].copy()   # uitgesloten producten niet importeren
    if miss.empty:
        st.success("Alle (niet-uitgesloten) Victron-producten uit de prijslijst staan al in Odoo.")
    else:
        st.caption("Importeer ontbrekende Victron-producten in Odoo (uitgesloten producten "
                   "worden hier niet getoond). Kostprijs = adviesprijs (excl BTW); "
                   "verkoopprijs = kostprijs × marge. Foto/kenmerken kun je daarna per "
                   "product aanvullen.")
        margin = st.slider("Marge (×)", 1.0, 3.0, DEFAULT_MARGIN, 0.05, key="vpl_margin")
        miss.insert(0, "Selecteer", False)
        edited = st.data_editor(
            miss[["Selecteer", "nieuw", "code", "naam", "categorie", "advies_excl"]],
            hide_index=True, use_container_width=True,
            disabled=["nieuw", "code", "naam", "categorie", "advies_excl"],
            column_config={
                "nieuw": st.column_config.CheckboxColumn("🆕"),
                "advies_excl": st.column_config.NumberColumn("Advies (excl)", format="€ %.2f")},
            key="vpl_missing")
        sel = edited[edited["Selecteer"]]
        as_cost = st.checkbox("Kostprijs = adviesprijs (anders leeg laten)", value=True,
                              key="vpl_ascost")
        if not sel.empty and st.button(f"➕ Importeer {len(sel)} in Odoo", type="primary"):
            added = errs = 0
            for _, r in sel.iterrows():
                try:
                    cost = (None if pd.isna(r["advies_excl"]) or not as_cost
                            else float(r["advies_excl"]))
                    sale = round(cost * margin, 2) if cost is not None else None
                    op.create_product(odoo, TS_PARTNER_ID, str(r["code"]),
                                      str(r["naam"]), cost, sale,
                                      categ_id=VICTRON_CATEG_DEFAULT)
                    added += 1
                except Exception as e:
                    errs += 1
                    st.error(f"{r['code']}: {e}")
            st.success(f"✓ {added} geïmporteerd · {errs} fout")
            if added:
                st.rerun()

# ---- Product verrijken ----
with tab_detail:
    st.caption("Kies een product en vul foto, omschrijving en kenmerken aan. "
               "Kenmerken worden als Odoo-attributen (no-variant) gezet.")
    pick = st.selectbox("Product (code — naam)",
                        [f"{c} — {master[c].get('name', '')[:50]}" for c in codes],
                        key="vpl_pick")
    code = pick.split(" — ")[0].strip()
    p = master.get(code, {})

    dc1, dc2 = st.columns([1, 1])
    with dc1:
        badges = []
        if p.get("is_new"):
            badges.append("🆕 Nieuw")
        if _is_excluded(code, p.get("category", "")):
            badges.append("🚫 Uitgesloten")
        if badges:
            st.markdown(" · ".join(badges))
        st.markdown(f"**{code}** · {p.get('category', '')}")
        st.write(p.get("name", ""))
        st.caption(f"Afmetingen: {p.get('dimensions') or '—'} · "
                   f"Gewicht: {p.get('weight') or '—'} kg · "
                   f"Advies (excl): € {p.get('advice_price') or '—'}")
        in_odoo_now = code in in_odoo
        st.write(("✅ Staat in Odoo" if in_odoo_now else "❌ Nog niet in Odoo"))

    with dc2:
        name_val = st.text_input("Naam (corrigeren mag)", value=p.get("name", ""),
                                 key="vpl_name",
                                 help="Bv. een uitgelekt bijschrift uit de PDF weghalen. "
                                      "Correcties blijven behouden bij een nieuwe import.")
        image_url = st.text_input("Foto-URL", value=p.get("image_url", ""), key="vpl_img")
        description = st.text_area("Omschrijving", value=p.get("description", ""),
                                   height=100, key="vpl_desc")

    st.markdown("**Kenmerken (attributen)**")
    specs = p.get("specs") or {}
    spec_rows = [{"kenmerk": k, "waarde": v} for k, v in specs.items()] or \
                [{"kenmerk": "", "waarde": ""}]
    spec_ed = st.data_editor(pd.DataFrame(spec_rows), hide_index=True, num_rows="dynamic",
                             use_container_width=True, key="vpl_specs")
    new_specs = {}
    for _, r in spec_ed.iterrows():
        k = str(r["kenmerk"]).strip()
        v = str(r["waarde"]).strip()
        if k and v:
            new_specs[k] = v

    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("💾 Opslaan (blijft bij her-import)", key="vpl_save_master"):
            fields = {
                "name": name_val.strip(),
                "image_url": image_url.strip(),
                "description": description.strip(),
                "specs": new_specs,
            }
            pushed, info = save_override(code, fields, f"Victron {code} verrijkt/gecorrigeerd")
            # In-memory direct toepassen zodat de wijziging meteen zichtbaar is.
            for k, v in fields.items():
                master[code][k] = v
            st.success(f"✓ Opgeslagen{' + GitHub ' + info if pushed else ' (lokaal)'}")
            st.rerun()

    with bc2:
        if st.button("⬆️ Naar Odoo pushen", type="primary", key="vpl_push"):
            try:
                # Bestaand template ophalen of aanmaken
                existing = odoo.search_read("product.template",
                                            [["default_code", "=", code]], ["id"], 1)
                img_b64 = op.download_image_b64(image_url) if image_url.strip() else None
                if existing:
                    tid = existing[0]["id"]
                    if description.strip():
                        op.update_description(odoo, tid, description.strip())
                    if img_b64:
                        op.update_image(odoo, tid, img_b64)
                else:
                    cost = p.get("advice_price")
                    sale = round(cost * DEFAULT_MARGIN, 2) if cost is not None else None
                    tid = op.create_product(odoo, TS_PARTNER_ID, code,
                                            name_val.strip() or p.get("name", ""),
                                            cost, sale, image_b64=img_b64,
                                            categ_id=VICTRON_CATEG_DEFAULT,
                                            description=description.strip())
                n = op.set_specs_attributes(odoo, tid, new_specs) if new_specs else 0
                st.success(f"✓ Gepusht naar Odoo (template {tid}) · {n} kenmerk(en) gezet")
            except Exception as e:
                st.error(f"Push mislukt: {e}")
