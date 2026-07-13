"""
Reimo product beheren — acties op niet meer verkochte producten.

Bereikbaar via de 'Beheren'-knop in het Reimo-rapport (?tmpl_id=<id>) of manueel
via een artikelnummer. Toont voorraad + status en biedt (omkeerbare) acties:
  - Archiveren (active) — aanbevolen bij 0 voorraad
  - Uit de webshop halen (is_published) — aanbevolen bij voorraad > 0
  - Niet meer bestellen (purchase_ok)
"""
import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient   # noqa: E402

try:
    st.set_page_config(page_title="Reimo product beheren", page_icon="🛠️", layout="wide")
except Exception:
    pass

from auth import require_auth        # noqa: E402
require_auth()

REIMO_PARTNER_ID = 66
ODOO_URL = os.environ.get("ODOO_URL", "https://compactliving.odoo.com").rstrip("/")

FIELDS = ["name", "default_code", "list_price", "active", "is_published",
          "purchase_ok", "sale_ok", "qty_available", "virtual_available",
          "sale_line_warn_msg", "x_reimo_beschikbaarheid", "image_128"]


@st.cache_resource(show_spinner=False)
def get_odoo():
    return OdooClient(url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
                      login=os.environ["ODOO_LOGIN"],
                      api_key=os.environ.get("ODOO_API_KEY", ""),
                      password=os.environ.get("ODOO_PASSWORD", ""))


def load_by_id(tmpl_id):
    o = get_odoo()
    try:
        r = o.read("product.template", [int(tmpl_id)], FIELDS)
    except Exception:
        return None
    return r[0] if r else None


def resolve_artikelnr(code):
    """Zoek tmpl_id via Reimo-leverancierscode of interne referentie."""
    o = get_odoo()
    si = o.search_read("product.supplierinfo",
                       [("partner_id", "=", REIMO_PARTNER_ID), ("product_code", "=", code)],
                       ["product_tmpl_id"], 1)
    if si and si[0].get("product_tmpl_id"):
        return si[0]["product_tmpl_id"][0]
    t = o.search_read("product.template", [("default_code", "=", code)], ["id"], 1)
    return t[0]["id"] if t else None


def reimo_code_for(tmpl_id):
    o = get_odoo()
    si = o.search_read("product.supplierinfo",
                       [("partner_id", "=", REIMO_PARTNER_ID), ("product_tmpl_id", "=", int(tmpl_id))],
                       ["product_code"], 1)
    return si[0]["product_code"] if si else ""


def set_field(tmpl_id, field, value, msg):
    o = get_odoo()
    o.write("product.template", [int(tmpl_id)], {field: value})
    st.session_state["flash"] = msg


st.title("🛠️ Reimo product beheren")
st.caption("Acties op producten die niet meer verkocht worden. Alle acties zijn omkeerbaar.")

# ---- product bepalen (uit rapport-knop of manueel) ----
qp_tmpl = st.query_params.get("tmpl_id", "")
if qp_tmpl:
    st.session_state["pending_tmpl_id"] = qp_tmpl
pending_tmpl = st.session_state.get("pending_tmpl_id", "")

c1, c2 = st.columns([3, 1])
with c1:
    art = st.text_input("Reimo artikelnummer (of laat leeg als je via het rapport kwam)", value="")
with c2:
    st.write("")
    zoek = st.button("🔎 Zoek", use_container_width=True)

tmpl_id = None
if zoek and art.strip():
    tmpl_id = resolve_artikelnr(art.strip())
    if tmpl_id:
        st.session_state["pending_tmpl_id"] = str(tmpl_id)
    else:
        st.warning("Geen product gevonden voor dat artikelnummer.")
elif pending_tmpl:
    tmpl_id = pending_tmpl

if st.session_state.get("flash"):
    st.success(st.session_state.pop("flash"))

if not tmpl_id:
    st.info("Open dit scherm via de **Beheren**-knop bij een niet-verkocht product in het rapport, "
            "of zoek hierboven op artikelnummer.")
    st.stop()

prod = load_by_id(tmpl_id)
if not prod:
    st.error("Product niet gevonden in Odoo.")
    st.stop()

qty = prod.get("qty_available") or 0.0
virt = prod.get("virtual_available") or 0.0
odoo_link = f"{ODOO_URL}/odoo/inventory/products/{prod['id']}"

st.divider()
left, right = st.columns([1, 2])
with left:
    if prod.get("image_128"):
        st.image(f"{ODOO_URL}/web/image/product.template/{prod['id']}/image_256",
                 use_container_width=True)
    st.metric("Voorraad (op hand)", f"{qty:g}")
    st.caption(f"Verwacht beschikbaar (incl. inkomend): {virt:g}")
with right:
    st.subheader(prod.get("name", ""))
    st.caption(f"Reimo-code: **{reimo_code_for(prod['id']) or '—'}**  ·  "
               f"Interne ref: **{prod.get('default_code') or '—'}**  ·  "
               f"[Open in Odoo]({odoo_link})")
    # statusbadges
    actief = prod.get("active")
    online = prod.get("is_published")
    koopbaar = prod.get("purchase_ok")
    st.write(
        f"{'🟢 Actief' if actief else '⚪ Gearchiveerd'} · "
        f"{'🌐 Op webshop' if online else '🚫 Niet op webshop'} · "
        f"{'🛒 Bestelbaar' if koopbaar else '⛔ Niet meer bestellen'}")
    if prod.get("x_reimo_beschikbaarheid"):
        st.info(prod["x_reimo_beschikbaarheid"])
    if prod.get("sale_line_warn_msg"):
        st.caption("Waarschuwing op verkooporders (handmatige notitie + beschikbaarheid):")
        st.code(prod["sale_line_warn_msg"], language=None)

    # aanbeveling
    if qty <= 0:
        st.markdown("**Aanbeveling:** geen voorraad → **archiveren**.")
    else:
        st.markdown(f"**Aanbeveling:** nog **{qty:g}** op voorraad → **uit de webshop halen** "
                    "en de restvoorraad manueel verkopen.")

    st.divider()
    a1, a2, a3 = st.columns(3)
    with a1:
        if actief:
            if st.button("📦 Archiveren", use_container_width=True,
                         type="primary" if qty <= 0 else "secondary"):
                set_field(tmpl_id, "active", False, "Product gearchiveerd.")
                st.rerun()
        else:
            if st.button("♻️ Terug activeren", use_container_width=True):
                set_field(tmpl_id, "active", True, "Product terug geactiveerd.")
                st.rerun()
    with a2:
        if online:
            if st.button("🌐 Uit webshop halen", use_container_width=True,
                         type="primary" if qty > 0 else "secondary"):
                set_field(tmpl_id, "is_published", False, "Van de webshop gehaald.")
                st.rerun()
        else:
            if st.button("🌐 Terug op webshop", use_container_width=True):
                set_field(tmpl_id, "is_published", True, "Terug op de webshop gezet.")
                st.rerun()
    with a3:
        if koopbaar:
            if st.button("⛔ Niet meer bestellen", use_container_width=True):
                set_field(tmpl_id, "purchase_ok", False, "Aankoop uitgeschakeld.")
                st.rerun()
        else:
            if st.button("🛒 Terug bestelbaar", use_container_width=True):
                set_field(tmpl_id, "purchase_ok", True, "Aankoop terug ingeschakeld.")
                st.rerun()
