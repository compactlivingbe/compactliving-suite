"""
Reimo product toevoegen — zoek een artikel op in Profiweb + de webshop en maak
het als product in Odoo aan (foto, artikelnr, barcode, verkoopprijs, en inkoop-
prijs onder de leveranciers/inkoop-tab).

Kan geopend worden vanuit het Reimo-rapport met ?artikelnr=<code>, of manueel.
"""
import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient          # noqa: E402
import odoo_products as op                  # noqa: E402
import reimo_lookup as rl                   # noqa: E402
try:
    from reimo_scraper import Profiweb
except ImportError:
    Profiweb = None

try:
    st.set_page_config(page_title="Reimo product toevoegen", page_icon="➕", layout="wide")
except Exception:
    pass

from auth import require_auth               # noqa: E402
require_auth()

REIMO_PARTNER_ID = 66
ODOO_URL = os.environ.get("ODOO_URL", "https://compactliving.odoo.com").rstrip("/")


@st.cache_resource(show_spinner=False)
def get_odoo():
    return OdooClient(url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
                      login=os.environ["ODOO_LOGIN"],
                      api_key=os.environ.get("ODOO_API_KEY", ""),
                      password=os.environ.get("ODOO_PASSWORD", ""))


@st.cache_data(ttl=600, show_spinner=False)
def get_categories():
    o = get_odoo()
    cats = o.search_read("product.category", [], ["id", "complete_name"], limit=1000)
    return sorted(cats, key=lambda c: (c.get("complete_name") or ""))


def get_profiweb():
    if Profiweb is None:
        return None
    pw = st.session_state.get("pw_session")
    if pw is None:
        pw = Profiweb(os.environ["PROFIWEB_USER"], os.environ["PROFIWEB_PASS"], log=lambda m: None)
        pw.login()
        st.session_state["pw_session"] = pw
    return pw


def find_existing(o, code: str, barcode: str = ""):
    """Bestaat het product al in Odoo? Zoek op Reimo-leverancierscode, interne
    referentie of barcode. Returns (tmpl_dict, reden) of (None, '')."""
    si = o.search_read("product.supplierinfo",
                       [("partner_id", "=", REIMO_PARTNER_ID), ("product_code", "=", code)],
                       ["product_tmpl_id"], 1)
    tid = None
    if si and si[0].get("product_tmpl_id"):
        tid = si[0]["product_tmpl_id"][0]
        reden = "zelfde Reimo-leverancierscode"
    if tid is None:
        t = o.search_read("product.template", [("default_code", "=", code)], ["id"], 1)
        if t:
            tid = t[0]["id"]; reden = "zelfde interne referentie"
    if tid is None and barcode:
        t = o.search_read("product.template", [("barcode", "=", barcode)], ["id"], 1)
        if t:
            tid = t[0]["id"]; reden = "zelfde barcode"
    if tid is None:
        return None, ""
    tmpl = o.read("product.template", [tid], ["name", "default_code", "list_price"])
    return (tmpl[0] if tmpl else {"id": tid}), reden


def do_lookup(code: str):
    """Combineer Profiweb + shop-data en bewaar in session_state."""
    code = code.strip()
    if not code:
        return
    pw_info = None
    pw_error = None
    try:
        pw = get_profiweb()
        if pw is not None:
            pw_info = pw.lookup(code)
    except Exception as e:  # noqa: BLE001
        pw_error = str(e)
    nc = rl.from_newcomers(code)
    data = rl.combine(code, pw_info, nc)
    o = get_odoo()
    existing, reden = find_existing(o, code, data.get("barcode", ""))
    st.session_state["lookup"] = {
        "data": data, "existing": existing, "existing_reden": reden,
        "pw_error": pw_error,
    }


st.title("➕ Reimo product toevoegen")
st.caption("Zoek een Reimo-artikel op (Profiweb + webshop) en maak het als product in Odoo aan. "
           "Inkoopprijs komt onder de leveranciers-/inkooptab.")

# ---- artikelnummer (uit rapport-knop of manueel) ----
qp_art = st.query_params.get("artikelnr", "")
col_in, col_btn = st.columns([3, 1])
with col_in:
    code = st.text_input("Reimo artikelnummer", value=qp_art, key="art_input").strip()
with col_btn:
    st.write("")
    zoek = st.button("🔎 Opzoeken", type="primary", use_container_width=True)

# auto-opzoeken bij openen vanuit rapport (1x)
if qp_art and st.session_state.get("auto_done") != qp_art:
    st.session_state["auto_done"] = qp_art
    with st.spinner(f"Artikel {qp_art} opzoeken in Profiweb + webshop…"):
        do_lookup(qp_art)

if zoek and code:
    with st.spinner(f"Artikel {code} opzoeken in Profiweb + webshop…"):
        do_lookup(code)

lk = st.session_state.get("lookup")
if lk:
    data = lk["data"]
    existing = lk["existing"]

    if lk.get("pw_error"):
        st.warning(f"Profiweb-opzoeking niet gelukt ({lk['pw_error']}). "
                   "Je kan de gegevens hieronder manueel aanvullen.")
    if not data["profiweb_gevonden"] and not data["in_rapport"]:
        st.warning("Artikel niet gevonden in Profiweb en niet in het rapport. Controleer het nummer.")

    if existing:
        url = f"{ODOO_URL}/odoo/inventory/products/{existing['id']}"
        st.error(f"⚠️ Dit artikel staat al in Odoo ({lk['existing_reden']}): "
                 f"**{existing.get('name','')}** — [open in Odoo]({url})")

    st.divider()
    left, right = st.columns([1, 2])
    with left:
        if data["foto"]:
            st.image(data["foto"], caption="Reimo-foto", use_container_width=True)
        else:
            st.info("Geen foto beschikbaar.")
        if data["beschikbaarheid"]:
            st.caption(f"📦 {data['beschikbaarheid']}")
        if data["categorie_pad"]:
            st.caption(f"🗂️ {data['categorie_pad']}")

    with right:
        with st.form("nieuw_product"):
            naam = st.text_input("Naam", value=data["naam"])
            c1, c2 = st.columns(2)
            with c1:
                artikelnr = st.text_input("Artikelnummer (interne ref. + leverancierscode)",
                                          value=data["artikelnr"])
                inkoop = st.number_input("Inkoopprijs (Händlerpreis, excl. btw)",
                                         value=float(data["inkoop"] or 0.0), min_value=0.0, step=0.01,
                                         format="%.2f")
            with c2:
                barcode = st.text_input("Barcode (EAN)", value=data["barcode"])
                verkoop = st.number_input("Verkoopprijs (Reimo VK)",
                                          value=float(data["verkoop"] or 0.0), min_value=0.0, step=0.01,
                                          format="%.2f")

            cats = get_categories()
            cat_opts = ["(standaard)"] + [c["complete_name"] for c in cats]
            cat_sel = st.selectbox("Productcategorie (optioneel)", cat_opts, index=0)
            foto_url = st.text_input("Foto-URL (optioneel, wordt gedownload)", value=data["foto"])
            oms = st.text_area("Verkoopomschrijving (optioneel)", value=data["omschrijving"], height=70)

            disabled = existing is not None
            submitted = st.form_submit_button(
                "✅ Aanmaken in Odoo" if not disabled else "Bestaat al — niet aanmaken",
                type="primary", use_container_width=True, disabled=disabled)

        if submitted and not disabled:
            try:
                o = get_odoo()
                categ_id = None
                if cat_sel != "(standaard)":
                    match = [c for c in cats if c["complete_name"] == cat_sel]
                    categ_id = match[0]["id"] if match else None
                img_b64 = op.download_image_b64(foto_url) if foto_url else None
                with st.spinner("Product aanmaken in Odoo…"):
                    tid = op.create_product(
                        o, REIMO_PARTNER_ID, code=artikelnr.strip(), name=naam.strip(),
                        cost=float(inkoop) if inkoop else None,
                        list_price=float(verkoop) if verkoop else None,
                        image_b64=img_b64, categ_id=categ_id, description=oms.strip())
                    if barcode.strip():
                        try:
                            o.write("product.template", [tid], {"barcode": barcode.strip()})
                        except Exception as be:  # noqa: BLE001
                            st.warning(f"Barcode niet opgeslagen (mogelijk al in gebruik): {be}")
                url = f"{ODOO_URL}/odoo/inventory/products/{tid}"
                st.success(f"✅ Product aangemaakt (id {tid}) — [open in Odoo]({url})")
                st.balloons()
                # lookup verversen zodat 'bestaat al' klopt
                do_lookup(artikelnr.strip())
            except Exception as e:  # noqa: BLE001
                st.error(f"Aanmaken mislukt: {e}")
else:
    st.info("Geef een Reimo-artikelnummer in en klik **Opzoeken**, of open dit scherm "
            "vanuit het Reimo-rapport via de knop **➕ In Odoo** bij een product.")
