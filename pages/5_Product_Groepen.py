"""Product groepen — gelijkaardige producten markeren + prijsvergelijking.

Gebruikt Odoo's native `product.tag` model (many2many op product.template).
1 tag = 1 groep van gelijkaardige producten (bv. 'Batterij schakelaar 275A').
"""
import os, sys
from pathlib import Path
import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient

st.set_page_config(page_title="Product Groepen", page_icon="🔗", layout="wide")

from auth import require_auth
require_auth()

st.title("🔗 Product groepen — vergelijk gelijkaardige producten")
st.caption("Markeer producten met dezelfde functie als groep, zie alle prijzen naast elkaar.")


def get_odoo():
    return OdooClient(
        url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
        login=os.environ["ODOO_LOGIN"], api_key=os.environ.get("ODOO_API_KEY", ""),
    )


odoo = get_odoo()
ODOO_URL = os.environ.get("ODOO_URL", "https://compactliving.odoo.com").rstrip("/")


def odoo_product_url(tmpl_id):
    return f"{ODOO_URL}/odoo/inventory/products/{tmpl_id}"


def fmt_eur(n):
    if n is None or n == "": return "—"
    try: return f"€ {float(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(n)


# ============ TABS ============
tab_view, tab_manage, tab_ai = st.tabs([
    "📊 Bekijk groepen + prijzen",
    "✏️ Beheer groepen",
    "🤖 AI groep-suggesties",
])


# ============ TAB 1: VIEW ============
with tab_view:
    # Load all tags - probeer verschillende model namen voor verschillende Odoo versies
    tags = []
    last_err = None
    for model in ["product.tag", "product.template.tag", "product.category"]:
        try:
            tags = odoo.search_read(model, [], ["id", "name"], 500, "name")
            if model != "product.tag":
                st.warning(f"⚠ Model 'product.tag' werkt niet in deze Odoo. Gebruikt fallback: '{model}'.")
            st.session_state["_tag_model"] = model
            break
        except Exception as e:
            last_err = e
            continue
    if not tags and last_err:
        st.error(f"Kan geen product groepen ophalen. Last error: {last_err}")
        st.caption("Mogelijk: model 'product.tag' bestaat niet in deze Odoo versie, "
                   "of de API user heeft geen rechten. Open Odoo → Apps → installeer 'Product Tags' "
                   "of stel een andere user in via ODOO_LOGIN.")
        st.stop()
    if not tags:
        st.info("Nog geen product groepen aangemaakt. Ga naar tabblad **Beheer** om er een te maken.")
    else:
        tag_options = {f"{t['name']}": t["id"] for t in tags}
        sel = st.selectbox("Kies groep", ["(alle groepen overzicht)"] + list(tag_options.keys()))

        if sel == "(alle groepen overzicht)":
            view_mode = st.radio(
                "Weergave:",
                ["📋 Per groep (alle leden zichtbaar)", "📊 Samenvattende tabel", "🌐 Flat tabel (alles in 1)"],
                horizontal=True, label_visibility="collapsed",
            )

            # Eénmaal alle data ophalen
            with st.spinner("Producten + leveranciers ophalen..."):
                # Alle templates met minstens één tag
                all_tag_ids = [t["id"] for t in tags]
                all_tmpls = odoo.search_read(
                    "product.template",
                    [("product_tag_ids", "in", all_tag_ids)],
                    ["id", "name", "default_code", "list_price", "standard_price",
                     "qty_available", "categ_id", "product_tag_ids", "seller_ids"], 1000
                )
                # Supplier info per template
                all_si_ids = sum((t["seller_ids"] for t in all_tmpls), [])
                sis = odoo.search_read("product.supplierinfo",
                                        [("id", "in", all_si_ids)],
                                        ["product_tmpl_id", "partner_id",
                                         "product_code", "price", "delay"]
                                        ) if all_si_ids else []
                sup_per_tmpl = {}
                for s in sis:
                    tid = s["product_tmpl_id"][0] if s["product_tmpl_id"] else None
                    if tid:
                        sup_per_tmpl.setdefault(tid, []).append(s)

            # Group templates per tag
            tmpls_per_tag = {t["id"]: [] for t in tags}
            for tmpl in all_tmpls:
                for tid in tmpl.get("product_tag_ids", []):
                    if tid in tmpls_per_tag:
                        tmpls_per_tag[tid].append(tmpl)

            def _row_for(tmpl):
                sups = sup_per_tmpl.get(tmpl["id"], [])
                cheapest = min(sups, key=lambda s: s.get("price") or 9e9) if sups else None
                marge = ""
                if tmpl["standard_price"] and tmpl["list_price"]:
                    marge = f"{((tmpl['list_price']-tmpl['standard_price'])/tmpl['standard_price']*100):.0f}%"
                return {
                    "Code": tmpl.get("default_code") or "—",
                    "Product": tmpl["name"],
                    "Categorie": tmpl["categ_id"][1].split(" / ")[-1] if tmpl.get("categ_id") else "",
                    "Inkoop": tmpl["standard_price"],
                    "Verkoop": tmpl["list_price"],
                    "Marge": marge,
                    "Voorraad": int(tmpl.get("qty_available") or 0),
                    "Goedkoopste": (
                        f"{cheapest['partner_id'][1]}: {fmt_eur(cheapest['price'])}"
                        if cheapest else "—"
                    ),
                    "# Lev.": len(sups),
                    "Odoo": odoo_product_url(tmpl["id"]),
                }

            # Gemeenschappelijke column config met clickable Odoo link
            COL_CFG = {
                "Inkoop": st.column_config.NumberColumn(format="€ %.2f"),
                "Verkoop": st.column_config.NumberColumn(format="€ %.2f"),
                "Voorraad": st.column_config.NumberColumn(format="%d"),
                "Odoo": st.column_config.LinkColumn(
                    "Odoo", help="Open product in Odoo",
                    display_text="🔗 Open"
                ),
            }

            if view_mode == "📋 Per groep (alle leden zichtbaar)":
                # Eén tabel per groep, gestapeld, sortable
                shown = 0
                for t in tags:
                    members = tmpls_per_tag.get(t["id"], [])
                    if not members: continue
                    shown += 1
                    prices_in = [m["standard_price"] for m in members if m["standard_price"]]
                    spread = ""
                    if prices_in and len(prices_in) > 1 and min(prices_in) > 0:
                        spread = f" — spread {((max(prices_in)-min(prices_in))/min(prices_in)*100):.0f}%"
                    st.markdown(f"#### 🔗 {t['name']} ({len(members)} producten{spread})")
                    rows = sorted([_row_for(m) for m in members],
                                   key=lambda r: r["Inkoop"] or 0)
                    st.dataframe(
                        pd.DataFrame(rows), hide_index=True, use_container_width=True,
                        column_config=COL_CFG,
                    )
                if shown == 0:
                    st.info("Geen producten in groepen. Voeg toe via tab 'Beheer'.")

            elif view_mode == "📊 Samenvattende tabel":
                rows = []
                for t in tags:
                    members = tmpls_per_tag.get(t["id"], [])
                    if not members: continue
                    prices_in = [m["standard_price"] for m in members if m["standard_price"]]
                    prices_out = [m["list_price"] for m in members if m["list_price"]]
                    rows.append({
                        "Groep": t["name"],
                        "# producten": len(members),
                        "Inkoop laag": fmt_eur(min(prices_in)) if prices_in else "—",
                        "Inkoop hoog": fmt_eur(max(prices_in)) if prices_in else "—",
                        "Verkoop laag": fmt_eur(min(prices_out)) if prices_out else "—",
                        "Verkoop hoog": fmt_eur(max(prices_out)) if prices_out else "—",
                        "Marge spread": f"{((max(prices_out)-min(prices_in))/min(prices_in)*100):.0f}%"
                                         if prices_in and prices_out and min(prices_in) > 0 else "—",
                    })
                if rows:
                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                else:
                    st.info("Geen producten in groepen.")

            else:  # Flat tabel
                tag_name_by_id = {t["id"]: t["name"] for t in tags}
                rows = []
                for tmpl in all_tmpls:
                    base = _row_for(tmpl)
                    tag_names = ", ".join(tag_name_by_id.get(tid, "?")
                                          for tid in tmpl.get("product_tag_ids", []))
                    rows.append({"Groep": tag_names, **base})
                if rows:
                    df = pd.DataFrame(rows)
                    # Filter
                    flt = st.text_input("🔍 Filter (groep, product, code, leverancier)",
                                         placeholder="bv. switch, victron, top systems")
                    if flt:
                        mask = df.apply(
                            lambda r: flt.lower() in " ".join(str(v) for v in r.values).lower(),
                            axis=1
                        )
                        df = df[mask]
                    st.caption(f"{len(df)} rijen")
                    st.dataframe(df.sort_values(["Groep", "Inkoop"]),
                                  hide_index=True, use_container_width=True,
                                  column_config=COL_CFG)
                else:
                    st.info("Geen producten in groepen.")
        else:
            tag_id = tag_options[sel]
            tag = next(t for t in tags if t["id"] == tag_id)
            st.markdown(f"### 🔗 {tag['name']}")

            tmpls = odoo.search_read(
                "product.template",
                [("product_tag_ids", "in", [tag_id])],
                ["id", "name", "list_price", "standard_price", "default_code",
                 "qty_available", "categ_id", "seller_ids"], 200
            )
            if not tmpls:
                st.warning("Geen producten in deze groep.")
            else:
                # Get supplier info per template
                all_seller_ids = sum((t["seller_ids"] for t in tmpls), [])
                sis = odoo.search_read(
                    "product.supplierinfo",
                    [("id", "in", all_seller_ids)],
                    ["product_tmpl_id", "partner_id", "product_code", "price", "delay"]
                ) if all_seller_ids else []
                sup_per_tmpl = {}
                for s in sis:
                    tid = s["product_tmpl_id"][0] if s["product_tmpl_id"] else None
                    if tid:
                        sup_per_tmpl.setdefault(tid, []).append(s)

                rows = []
                for t in tmpls:
                    sups = sup_per_tmpl.get(t["id"], [])
                    cheapest_sup = min(sups, key=lambda s: s.get("price") or 9e9) if sups else None
                    sup_str = ", ".join(f"{s['partner_id'][1]}: {fmt_eur(s.get('price'))}"
                                         for s in sups[:3])
                    if len(sups) > 3:
                        sup_str += f" (+{len(sups)-3} meer)"
                    marge = ""
                    if t["standard_price"] and t["list_price"]:
                        marge = f"{((t['list_price']-t['standard_price'])/t['standard_price']*100):.0f}%"
                    rows.append({
                        "Code": t.get("default_code") or "—",
                        "Product": t["name"],
                        "Categorie": t["categ_id"][1] if t.get("categ_id") else "",
                        "Inkoop": t["standard_price"],
                        "Verkoop": t["list_price"],
                        "Marge": marge,
                        "Voorraad": t.get("qty_available", 0),
                        "Goedkoopste leverancier": (
                            f"{cheapest_sup['partner_id'][1]}: {fmt_eur(cheapest_sup['price'])}"
                            if cheapest_sup else "—"
                        ),
                        "Alle leveranciers": sup_str,
                        "Odoo": odoo_product_url(t["id"]),
                        "_id": t["id"],
                    })
                df = pd.DataFrame(rows)
                # Check unieke leveranciers
                unique_sups = set()
                for sups in sup_per_tmpl.values():
                    for s in sups:
                        unique_sups.add(s["partner_id"][1] if isinstance(s, dict) else s)
                if len(unique_sups) < 2:
                    st.warning(f"⚠ Alle producten in deze groep hebben dezelfde leverancier ({list(unique_sups)}). "
                               f"Geen echt prijsvergelijking mogelijk.")
                # Sort by inkoop
                df_sorted = df.sort_values("Inkoop")
                st.dataframe(df_sorted, hide_index=True, use_container_width=True,
                             column_config={
                                 "_id": None,
                                 "Inkoop": st.column_config.NumberColumn(format="€ %.2f"),
                                 "Verkoop": st.column_config.NumberColumn(format="€ %.2f"),
                                 "Voorraad": st.column_config.NumberColumn(format="%d"),
                                 "Odoo": st.column_config.LinkColumn(
                                     "Odoo", help="Open in Odoo", display_text="🔗 Open"),
                             })
                # Visual: bar chart van inkoopprijzen
                if len(df) > 1:
                    chart_df = df_sorted[["Product", "Inkoop", "Verkoop"]].head(15)
                    st.bar_chart(chart_df.set_index("Product"))


# ============ TAB 2: MANAGE ============
with tab_manage:
    st.markdown("### ✏️ Groepen beheren")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Nieuwe groep aanmaken")
        new_tag = st.text_input("Naam nieuwe groep", placeholder="bv. Batterij schakelaar 275A")
        if st.button("➕ Maak groep"):
            if new_tag.strip():
                tid = odoo.create("product.tag", {"name": new_tag.strip()})
                st.success(f"Groep '{new_tag}' aangemaakt (id {tid})")
                st.cache_resource.clear()
                st.rerun()
    with col2:
        st.markdown("#### Bestaande groep bewerken")
        all_tags = odoo.search_read("product.tag", [], ["id", "name"], 500, "name")
        if all_tags:
            tag_sel = st.selectbox("Selecteer groep om te bewerken",
                                    [t["name"] for t in all_tags], key="manage_tag")
            sel_tag = next(t for t in all_tags if t["name"] == tag_sel)
            new_name = st.text_input("Hernoem", value=sel_tag["name"])
            cd1, cd2 = st.columns(2)
            with cd1:
                if st.button("💾 Hernoem groep") and new_name != sel_tag["name"]:
                    odoo.write("product.tag", [sel_tag["id"]], {"name": new_name})
                    st.success("Hernoemd"); st.rerun()
            with cd2:
                if st.button("🗑 Verwijder groep", type="secondary"):
                    odoo.call("product.tag", "unlink", [[sel_tag["id"]]])
                    st.success("Verwijderd"); st.rerun()

    st.divider()
    st.markdown("#### Producten toevoegen aan groep")
    if all_tags:
        tag_for_assign = st.selectbox("Doelgroep", [t["name"] for t in all_tags], key="assign_tag")
        tag_id = next(t["id"] for t in all_tags if t["name"] == tag_for_assign)

        # Toon huidige leden
        current_members = odoo.search_read(
            "product.template",
            [("product_tag_ids", "in", [tag_id])],
            ["id", "name", "default_code"], 100
        )
        if current_members:
            st.caption(f"Huidige leden ({len(current_members)}):")
            for m in current_members:
                cm1, cm2 = st.columns([4, 1])
                cm1.markdown(f"  • [{m.get('default_code') or '—'}] {m['name']}")
                if cm2.button("🗑", key=f"rm_{m['id']}", help="Verwijder uit groep"):
                    # Lees huidige tags + verwijder deze
                    cur = odoo.read("product.template", [m["id"]], ["product_tag_ids"])[0]
                    new_tags = [t for t in cur["product_tag_ids"] if t != tag_id]
                    odoo.write("product.template", [m["id"]],
                               {"product_tag_ids": [(6, 0, new_tags)]})
                    st.success(f"Verwijderd: {m['name']}"); st.rerun()

        st.markdown("**Zoek + voeg toe:**")
        search = st.text_input("Zoek product (naam of code)", key="search_add",
                                placeholder="bv. batterij schakelaar")
        if search and len(search) >= 3:
            cands = odoo.search_read(
                "product.template",
                ['|', ("name", "ilike", search), ("default_code", "ilike", search)],
                ["id", "name", "default_code", "product_tag_ids", "list_price", "standard_price"],
                30, "name"
            )
            already_in = [c for c in cands if tag_id in (c.get("product_tag_ids") or [])]
            not_in = [c for c in cands if tag_id not in (c.get("product_tag_ids") or [])]
            if not_in:
                opts = {f"[{c.get('default_code') or '—'}] {c['name']} (€{c.get('standard_price', 0):.2f})": c["id"]
                        for c in not_in}
                selected = st.multiselect("Selecteer producten om toe te voegen",
                                           list(opts.keys()), key="multi_add")
                if selected and st.button(f"➕ Voeg {len(selected)} toe aan '{tag_for_assign}'"):
                    for label in selected:
                        pid = opts[label]
                        cur = odoo.read("product.template", [pid], ["product_tag_ids"])[0]
                        new_tags = list(set(cur["product_tag_ids"] + [tag_id]))
                        odoo.write("product.template", [pid],
                                   {"product_tag_ids": [(6, 0, new_tags)]})
                    st.success(f"✓ {len(selected)} producten toegevoegd aan '{tag_for_assign}'")
                    st.rerun()
            if already_in:
                st.caption(f"Al in deze groep ({len(already_in)}): {', '.join(c['name'][:30] for c in already_in[:5])}")


# ============ TAB 3: AI SUGGEST ============
with tab_ai:
    st.markdown("### 🤖 AI suggereert mogelijke groepen")
    st.caption("Claude scant productnamen en stelt groepen voor van gelijkaardige producten.")

    # Laad alle categorieën met aantal producten erin (cache via session)
    if "_cats_with_count" not in st.session_state:
        with st.spinner("Categorieën ophalen..."):
            cats = odoo.search_read("product.category", [],
                                     ["id", "complete_name"], 500, "complete_name")
            counts = {}
            for c in cats:
                n = odoo.call("product.template", "search_count",
                               [[("categ_id", "child_of", c["id"])]])
                counts[c["id"]] = n
            st.session_state["_cats_with_count"] = [
                {**c, "count": counts.get(c["id"], 0)} for c in cats if counts.get(c["id"], 0) > 0
            ]
    cats_data = st.session_state["_cats_with_count"]

    st.markdown("##### Selecteer categorieën om te scannen")
    cat_labels = {f"{c['complete_name']} ({c['count']})": c["id"] for c in cats_data}
    selected_cat_labels = st.multiselect(
        "Categorieën (multi-select)", list(cat_labels.keys()),
        help="Lege selectie = scan alle producten. Per categorie scannen voorkomt rate limits."
    )
    selected_cat_ids = [cat_labels[l] for l in selected_cat_labels]

    col1, col2, col3 = st.columns(3)
    with col1:
        limit = st.number_input("Max producten scannen", min_value=20, max_value=2000, value=80, step=20,
                                  help="Lager = sneller, minder tokens. Default 80 past in 1 batch.")
    with col2:
        model_choice = st.selectbox("Model",
                                     ["claude-haiku-4-5", "claude-sonnet-4-6"],
                                     index=0,
                                     help="Haiku = sneller + 5x goedkoper. Sonnet = preciezer.")
    with col3:
        batch_size = st.number_input("Batch grootte", min_value=20, max_value=200, value=60, step=10,
                                       help="Producten per Claude-call. Lager = veiliger tegen rate limit.")

    st.caption("ℹ️ Tip: dit veld is `product_tag_ids` op product.template in Odoo. "
                "Je kan groepen ook direct in Odoo bewerken op de productpagina (sectie 'Algemene info' → tags).")

    skip_tagged = st.checkbox("⏭ Skip producten die al in een groep zitten", value=True,
                                help="Voorkomt dubbele groepen met dezelfde items")

    if st.button("🔍 Analyseer met Claude", type="primary"):
        domain = []
        if selected_cat_ids:
            domain.append(("categ_id", "child_of", selected_cat_ids))
        with st.spinner("Producten ophalen uit Odoo..."):
            tmpls = odoo.search_read("product.template", domain,
                                       ["id", "name", "default_code", "product_tag_ids", "seller_ids"],
                                       int(limit), "name")
        if skip_tagged:
            n_before = len(tmpls)
            tmpls = [t for t in tmpls if not t.get("product_tag_ids")]
            n_skipped = n_before - len(tmpls)
            if n_skipped:
                st.caption(f"⏭ {n_skipped} producten overgeslagen (al in een groep). "
                           f"{len(tmpls)} blijven over voor analyse.")
        if not tmpls:
            st.warning("Geen producten gevonden voor deze filter (mogelijk allen al gegroepeerd).")
        else:
            # Resolve suppliers per template
            with st.spinner("Leveranciers ophalen..."):
                all_si_ids = sum((t["seller_ids"] for t in tmpls), [])
                sis = odoo.search_read("product.supplierinfo",
                                        [("id", "in", all_si_ids)],
                                        ["product_tmpl_id", "partner_id"]) if all_si_ids else []
                sup_per_tmpl = {}
                for s in sis:
                    tid = s["product_tmpl_id"][0] if s["product_tmpl_id"] else None
                    pname = s["partner_id"][1] if s["partner_id"] else "?"
                    if tid:
                        sup_per_tmpl.setdefault(tid, set()).add(pname)
            st.info(f"{len(tmpls)} producten geladen. Claude bezig (batch {batch_size})...")
            try:
                import time as _time
                from anthropic import Anthropic
                client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
                all_groups = []
                progress = st.progress(0, text="Batch 1...")
                # Process in batches
                for batch_idx, start in enumerate(range(0, len(tmpls), batch_size)):
                    batch = tmpls[start:start + batch_size]
                    product_list = "\n".join(
                        f"- {t['name']}  [{', '.join(sorted(sup_per_tmpl.get(t['id'], {'?'})))}]"
                        for t in batch
                    )
                    prompt = f"""Lijst Odoo-producten Compact Living (camper-accessoires) met leveranciers tussen [].

Identificeer **groepen van gelijkaardige producten van VERSCHILLENDE leveranciers**.

Regels:
- Min 2 producten per groep
- Producten MOETEN VERSCHILLENDE leveranciers hebben
- Korte groep_naam (max 50 karakters)
- Gebruik EXACT de productnaam (zonder leverancier annotatie)
- Output ALLEEN valid JSON, geen commentaar

Format: [{{"groep_naam":"naam","producten":["prod1","prod2"]}}]

Producten:
{product_list}
"""
                    try:
                        resp = client.messages.create(
                            model=model_choice,
                            max_tokens=4000,
                            messages=[{"role": "user", "content": prompt}]
                        )
                    except Exception as api_err:
                        if "rate_limit" in str(api_err).lower() or "429" in str(api_err):
                            st.warning(f"Rate limit batch {batch_idx+1}, wacht 60s...")
                            _time.sleep(60)
                            resp = client.messages.create(
                                model=model_choice, max_tokens=4000,
                                messages=[{"role": "user", "content": prompt}]
                            )
                        else:
                            raise
                    txt = resp.content[0].text.strip()
                    # Parse this batch
                    import re as _re, json as _json
                    txt_clean = _re.sub(r'^```(?:json)?\s*', '', txt)
                    txt_clean = _re.sub(r'\s*```$', '', txt_clean)
                    s_idx, e_idx = txt_clean.find('['), txt_clean.rfind(']')
                    if s_idx >= 0 and e_idx > s_idx:
                        try:
                            batch_groups = _json.loads(txt_clean[s_idx:e_idx+1])
                            all_groups.extend(batch_groups)
                        except: pass
                    pct = int((batch_idx + 1) / max(1, (len(tmpls) // batch_size + 1)) * 100)
                    progress.progress(min(pct, 100), text=f"Batch {batch_idx+1} klaar ({len(all_groups)} groepen tot nu)")
                    # Pacing: 30k tokens/min ≈ 500 tokens/sec, 80 producten ≈ ~3000 tokens
                    # Wacht 6s tussen calls om binnen rate limit te blijven
                    if start + batch_size < len(tmpls):
                        _time.sleep(6)
                progress.empty()
                groups = all_groups
                if not groups:
                    st.error("AI gaf geen geldige groepen terug.")
                    raise RuntimeError("Geen groepen geparseerd")
                # Sanity filter
                groups = [g for g in groups if isinstance(g, dict)
                          and g.get("groep_naam") and len(g.get("producten", [])) >= 2]

                # Strict filter: alleen groepen met >= 2 unieke leveranciers
                name_to_id = {t["name"]: t["id"] for t in tmpls}
                filtered = []
                rejected = []
                for g in groups:
                    sups = set()
                    for pname in g["producten"]:
                        pid = name_to_id.get(pname)
                        if pid:
                            sups |= sup_per_tmpl.get(pid, set())
                    if len(sups) >= 2:
                        g["_unique_suppliers"] = sorted(sups)
                        filtered.append(g)
                    else:
                        rejected.append((g["groep_naam"], list(sups)))

                groups = filtered
                st.session_state["_ai_groups"] = groups
                st.session_state["_ai_tmpls"] = tmpls
                st.session_state["_ai_sup_per_tmpl"] = sup_per_tmpl
                st.success(f"✓ {len(groups)} groep-suggesties met ≥2 verschillende leveranciers")
                if rejected:
                    with st.expander(f"⚠ {len(rejected)} suggesties verworpen (zelfde leverancier)"):
                        for name, sups in rejected:
                            st.caption(f"  • {name} — leveranciers: {sups or '(geen)'}")
            except Exception as e:
                st.error(f"AI analyse faalde: {e}")

    if st.session_state.get("_ai_groups"):
        groups = st.session_state["_ai_groups"]
        tmpls = st.session_state["_ai_tmpls"]
        sup_per_tmpl_ai = st.session_state.get("_ai_sup_per_tmpl", {})
        name_to_id = {t["name"]: t["id"] for t in tmpls}
        id_to_name = {t["id"]: t["name"] for t in tmpls}
        # Bestaande groepen ophalen voor overlap-detectie
        existing_tags = {t["name"].lower(): t["id"]
                          for t in odoo.search_read("product.tag", [], ["id", "name"], 500)}

        def _sup_str(prod_name):
            tid = name_to_id.get(prod_name)
            if not tid: return "(niet gevonden)"
            return ", ".join(sorted(sup_per_tmpl_ai.get(tid, set()))) or "(geen)"

        for i, g in enumerate(groups):
            current_name_key = f"ai_name_{i}"
            current_prods_key = f"ai_prods_{i}"
            # Initialiseer session state per groep
            if current_name_key not in st.session_state:
                st.session_state[current_name_key] = g["groep_naam"]
            if current_prods_key not in st.session_state:
                st.session_state[current_prods_key] = list(g["producten"])

            current_name = st.session_state[current_name_key]
            current_prods = st.session_state[current_prods_key]
            already_exists = current_name.lower() in existing_tags
            badge = " ⚠ bestaat al" if already_exists else ""
            with st.expander(f"📦 {current_name} ({len(current_prods)} producten){badge}",
                              expanded=False):
                # Editable naam
                new_name = st.text_input("Groep naam", value=current_name,
                                          key=f"name_input_{i}")
                st.session_state[current_name_key] = new_name

                # Producten in groep met leverancier + verwijder
                if current_prods:
                    st.markdown("**Producten in suggestie** (vink aan om te verwijderen):")
                    rows = []
                    for pname in current_prods:
                        rows.append({
                            "Verwijder": False,
                            "Product": pname,
                            "Leverancier(s)": _sup_str(pname),
                            "_in_odoo": pname in name_to_id,
                        })
                    df = pd.DataFrame(rows)
                    edited = st.data_editor(df, hide_index=True, use_container_width=True,
                                              disabled=["Product", "Leverancier(s)", "_in_odoo"],
                                              column_config={"_in_odoo": None},
                                              key=f"prod_editor_{i}")
                    to_remove = [r["Product"] for _, r in edited.iterrows() if r["Verwijder"]]
                    if to_remove and st.button(f"🗑 Verwijder {len(to_remove)} aangevinkt",
                                                key=f"remove_btn_{i}"):
                        st.session_state[current_prods_key] = [
                            p for p in current_prods if p not in to_remove
                        ]
                        st.rerun()

                # Producten toevoegen — kies uit gescande tmpls die nog niet in groep zitten
                available_to_add = [t["name"] for t in tmpls
                                     if t["name"] not in current_prods]
                if available_to_add:
                    add_sel = st.multiselect(
                        "➕ Voeg producten toe (uit gescande lijst)",
                        available_to_add,
                        format_func=lambda n: f"{n}  [{_sup_str(n)}]",
                        key=f"add_sel_{i}",
                    )
                    if add_sel and st.button(f"➕ Voeg {len(add_sel)} toe",
                                              key=f"add_btn_{i}"):
                        st.session_state[current_prods_key] = current_prods + [
                            p for p in add_sel if p not in current_prods
                        ]
                        st.rerun()

                # Verificatie: minstens 2 verschillende leveranciers
                final_prods = st.session_state[current_prods_key]
                final_sups = set()
                for pname in final_prods:
                    tid = name_to_id.get(pname)
                    if tid:
                        final_sups |= sup_per_tmpl_ai.get(tid, set())
                if len(final_sups) < 2:
                    st.warning(f"⚠ Slechts {len(final_sups)} leverancier(s): "
                               f"{', '.join(final_sups) or '(geen)'}. Voeg producten van andere leverancier toe.")

                if already_exists:
                    st.info(f"ℹ Groep '{new_name}' bestaat al in Odoo — producten worden eraan toegevoegd.")

                # Maak/update knop
                btn_label = ("➕ Voeg toe aan bestaande groep"
                              if already_exists else "✓ Maak deze groep aan")
                if st.button(btn_label, key=f"create_btn_{i}",
                              type="primary",
                              disabled=(not final_prods or not new_name.strip())):
                    matched_ids = [name_to_id[n] for n in final_prods if n in name_to_id]
                    if not matched_ids:
                        st.error("Geen producten gematcht in Odoo")
                    else:
                        tid = (existing_tags.get(new_name.lower())
                               or odoo.create("product.tag", {"name": new_name.strip()}))
                        added = 0
                        for pid in matched_ids:
                            cur = odoo.read("product.template", [pid], ["product_tag_ids"])[0]
                            if tid in cur["product_tag_ids"]:
                                continue
                            new_tags = list(set(cur["product_tag_ids"] + [tid]))
                            odoo.write("product.template", [pid],
                                       {"product_tag_ids": [(6, 0, new_tags)]})
                            added += 1
                        st.success(f"✓ Groep '{new_name}': {added} nieuwe producten toegevoegd "
                                   f"({len(matched_ids) - added} zaten er al in)")
