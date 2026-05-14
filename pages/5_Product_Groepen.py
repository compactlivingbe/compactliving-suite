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


@st.cache_resource
def get_odoo():
    return OdooClient(
        url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
        login=os.environ["ODOO_LOGIN"], api_key=os.environ.get("ODOO_API_KEY", ""),
    )


odoo = get_odoo()


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
    # Load all tags
    tags = odoo.search_read("product.tag", [], ["id", "name"], 500, "name")
    if not tags:
        st.info("Nog geen product groepen aangemaakt. Ga naar tabblad **Beheer** om er een te maken.")
    else:
        tag_options = {f"{t['name']}": t["id"] for t in tags}
        sel = st.selectbox("Kies groep", ["(alle groepen overzicht)"] + list(tag_options.keys()))

        if sel == "(alle groepen overzicht)":
            # Overzicht: alle tags met aantal producten + prijsbereik
            rows = []
            for t in tags:
                tmpls = odoo.search_read(
                    "product.template",
                    [("product_tag_ids", "in", [t["id"]])],
                    ["id", "name", "list_price", "standard_price"], 100
                )
                if not tmpls: continue
                prices_in = [p["standard_price"] for p in tmpls if p["standard_price"]]
                prices_out = [p["list_price"] for p in tmpls if p["list_price"]]
                rows.append({
                    "Groep": t["name"],
                    "# producten": len(tmpls),
                    "Inkoop laagst": fmt_eur(min(prices_in)) if prices_in else "—",
                    "Inkoop hoogst": fmt_eur(max(prices_in)) if prices_in else "—",
                    "Verkoop laagst": fmt_eur(min(prices_out)) if prices_out else "—",
                    "Verkoop hoogst": fmt_eur(max(prices_out)) if prices_out else "—",
                    "Marge spread": f"{((max(prices_out)-min(prices_in))/min(prices_in)*100):.0f}%"
                                     if prices_in and prices_out and min(prices_in) > 0 else "—",
                })
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
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
                        "_id": t["id"],
                    })
                df = pd.DataFrame(rows)
                # Sort by inkoop
                df_sorted = df.sort_values("Inkoop")
                st.dataframe(df_sorted, hide_index=True, use_container_width=True,
                             column_config={
                                 "_id": None,
                                 "Inkoop": st.column_config.NumberColumn(format="€ %.2f"),
                                 "Verkoop": st.column_config.NumberColumn(format="€ %.2f"),
                                 "Voorraad": st.column_config.NumberColumn(format="%d"),
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

    col1, col2 = st.columns(2)
    with col1:
        categ_filter = st.text_input("Beperk tot categorie (bv. 'Victron'):", value="")
    with col2:
        limit = st.number_input("Max producten scannen", min_value=20, max_value=2000, value=200, step=20)

    if st.button("🔍 Analyseer met Claude", type="primary"):
        domain = []
        if categ_filter:
            domain.append(("categ_id.complete_name", "ilike", categ_filter))
        with st.spinner("Producten ophalen uit Odoo..."):
            tmpls = odoo.search_read("product.template", domain,
                                       ["id", "name", "default_code", "product_tag_ids"],
                                       int(limit), "name")
        if not tmpls:
            st.warning("Geen producten gevonden voor deze filter.")
        else:
            st.info(f"{len(tmpls)} producten geladen. Claude bezig...")
            try:
                from anthropic import Anthropic
                client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
                # Bouw input
                product_list = "\n".join(f"- [{t.get('default_code') or '—'}] {t['name']}" for t in tmpls)
                prompt = f"""Hier is een lijst Odoo-producten van Compact Living (camper-accessoires).
Identificeer **groepen van gelijkaardige producten** (dezelfde functie, verschillende leveranciers/uitvoeringen).

Voor elke groep:
- Geef een korte naam (bv. "Batterij schakelaar 275A enkel polig")
- Som de productnamen op die er bij horen (minstens 2 per groep)

Negeer groepen van slechts 1 product. Focus op echte alternatieven.

Output als JSON: [{{"groep_naam": "...", "producten": ["...", "..."]}}]
Geen extra tekst, alleen valid JSON.

Producten:
{product_list}
"""
                resp = client.messages.create(
                    model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
                    max_tokens=4000,
                    messages=[{"role": "user", "content": prompt}]
                )
                txt = resp.content[0].text.strip()
                # Extract JSON
                import json, re
                m = re.search(r'\[.*\]', txt, re.S)
                if m: txt = m.group(0)
                groups = json.loads(txt)
                st.session_state["_ai_groups"] = groups
                st.session_state["_ai_tmpls"] = tmpls
                st.success(f"✓ {len(groups)} groep-suggesties")
            except Exception as e:
                st.error(f"AI analyse faalde: {e}")

    if st.session_state.get("_ai_groups"):
        groups = st.session_state["_ai_groups"]
        tmpls = st.session_state["_ai_tmpls"]
        name_to_id = {t["name"]: t["id"] for t in tmpls}
        for i, g in enumerate(groups):
            with st.expander(f"📦 {g['groep_naam']} ({len(g['producten'])} producten)", expanded=False):
                matched_ids = [name_to_id[n] for n in g["producten"] if n in name_to_id]
                st.write(g["producten"])
                if st.button(f"➕ Maak deze groep aan in Odoo", key=f"ai_create_{i}"):
                    if not matched_ids:
                        st.error("Geen producten gematcht in Odoo (namen kloppen niet exact)")
                    else:
                        tid = odoo.create("product.tag", {"name": g["groep_naam"]})
                        for pid in matched_ids:
                            cur = odoo.read("product.template", [pid], ["product_tag_ids"])[0]
                            new_tags = list(set(cur["product_tag_ids"] + [tid]))
                            odoo.write("product.template", [pid],
                                       {"product_tag_ids": [(6, 0, new_tags)]})
                        st.success(f"✓ Groep '{g['groep_naam']}' aangemaakt met {len(matched_ids)} producten")
