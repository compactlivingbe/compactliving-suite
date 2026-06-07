"""
Compact Living Suite — landing page + auth gate.

Multi-page Streamlit app combineert:
- Factuurverwerking (PDF → Odoo PO/Bill)
- Reimo Profiweb beschikbaarheid sync
- Reimo automatische bestellingen (PO → Profiweb)
- Top Systems Victron prijssync

Deploy: Streamlit Community Cloud, secrets in st.secrets.
Schedules: GitHub Actions in .github/workflows/
"""
import os
import sys
import streamlit as st
from pathlib import Path

# Make lib/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

st.set_page_config(
    page_title="Compact Living Suite",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="auto",
)

# Map secrets → env (zodat alle modules werken zonder st.secrets dependency)
for key in ["ANTHROPIC_API_KEY", "ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY",
            "ODOO_PASSWORD", "CLAUDE_MODEL", "PROFIWEB_USER", "PROFIWEB_PASS",
            "TOPSYSTEMS_XML_URL", "APP_PASSWORD", "GH_TOKEN", "SCRAPER_API_KEY"]:
    try:
        if key in st.secrets:
            os.environ[key] = st.secrets[key]
    except Exception:
        pass


# ============ AUTH (Odoo-account) ============
from auth import require_auth, current_user, logout
require_auth()


# ============ LANDING ============
odoo_url = os.environ.get("ODOO_URL", "https://compactliving.odoo.com").rstrip("/")

# Externe Odoo-tools (onder /inttools/) — openen de volledige pagina (target=_top)
PROJECT_URL = f"{odoo_url}/inttools/project-verwerken"
RAPPORTEN_URL = f"{odoo_url}/inttools/rapporten"
PRODUCTBEHEER_URL = f"{odoo_url}/inttools/productbeheer"


def card_internal(icon, title, desc, caption, page):
    with st.container(border=True):
        st.markdown(f"#### {icon} {title}")
        st.write(desc)
        if caption:
            st.caption(caption)
        st.page_link(page, label="Openen", icon="➡️")


def card_external(icon, title, desc, caption, url):
    with st.container(border=True):
        st.markdown(f"#### {icon} {title}")
        st.write(desc)
        if caption:
            st.caption(caption)
        st.markdown(
            f'<a href="{url}" target="_top" style="display:inline-block;'
            f'padding:.30rem .9rem;background:#ff4b4b;color:#fff;border-radius:.5rem;'
            f'text-decoration:none;font-weight:600;font-size:.9rem;">Openen in Odoo ↗</a>',
            unsafe_allow_html=True,
        )


# ---- Header ----
hcol, bcol = st.columns([4, 1])
with hcol:
    st.title("⚡ Compact Living Suite")
    _u = current_user()
    if _u:
        st.caption(f"Eén centrale werkplek voor inkoop, producten, orders en projecten · "
                   f"ingelogd als **{_u['name']}**")
    else:
        st.caption("Eén centrale werkplek voor inkoop, producten, orders en projecten")
with bcol:
    st.write("")
    if st.button("🚪 Uitloggen", use_container_width=True):
        logout()
        st.rerun()

st.divider()

# ---- 1. Inkoop & facturen ----
st.markdown("### 📥 Inkoop & facturen")
i1, i2, i3 = st.columns(3)
with i1:
    card_internal("📄", "Facturen", "Upload een leveranciersfactuur (PDF) → AI-extractie → "
                  "Odoo inkooporder + factuur, mét productmatching.",
                  "Reimo, All-Spark, Bauhaus, Top Systems, …", "pages/1_Facturen.py")
with i2:
    card_internal("🛒", "Reimo bestellen", "Bevestigde Reimo-inkooporders automatisch in het "
                  "Profiweb-winkelmandje plaatsen.",
                  "Max 10 lijnen per order; winkelmand-beheer ingebouwd.", "pages/3_Reimo_Bestellen.py")
with i3:
    card_external("🏗️", "Project verwerken", "Camperbouw-projecten verwerken en opvolgen in Odoo.",
                  "Odoo-tool", PROJECT_URL)

# ---- 2. Leverancier-sync (prijzen & beschikbaarheid) ----
st.markdown("### 🔄 Leverancier-sync — prijzen & beschikbaarheid")
s1, s2, s3 = st.columns(3)
with s1:
    card_internal("📦", "Reimo beschikbaarheid", "Scrape Reimo Profiweb en schrijf een "
                  "beschikbaarheids-waarschuwing per Odoo-product.",
                  "Wekelijks automatisch of handmatig.", "pages/2_Reimo_Sync.py")
with s2:
    card_internal("💰", "Top Systems prijzen", "Vergelijk de Top Systems XML-lijst met Odoo en "
                  "update Victron kost- en verkoopprijzen.",
                  "Maandelijks automatisch of handmatig.", "pages/4_TopSystems_Prijzen.py")
with s3:
    card_internal("🔥", "VBD Standkachels", "Scrape vbdservices.nl (Autoterm) → importeer "
                  "ontbrekende producten, update kost- en verkoopprijzen.",
                  "Prijzen incl BTW; kost = excl BTW (NL 21%).", "pages/6_VBD_Standkachels.py")

# ---- 3. Producten & orders ----
st.markdown("### 📦 Producten & orders")
p1, p2, p3 = st.columns(3)
with p1:
    card_internal("📊", "SO Opvolging", "Per sales order: op voorraad, gekoppelde PO + status, "
                  "en verwachte levertijd bij Reimo.",
                  "Enkel fysieke producten; live Reimo-check.", "pages/7_SO_Opvolging.py")
with p2:
    card_internal("🔗", "Product groepen", "Gelijkaardige producten van verschillende leveranciers "
                  "groeperen en prijzen vergelijken.",
                  "AI suggereert mogelijke groepen.", "pages/5_Product_Groepen.py")
with p3:
    card_external("🛠️", "Product beheer", "Producten beheren en bewerken in Odoo.",
                  "Odoo-tool", PRODUCTBEHEER_URL)

# ---- 4. Rapporten ----
st.markdown("### 📈 Rapporten")
r1, r2, r3 = st.columns(3)
with r1:
    card_external("📈", "Rapporten", "Overzichts- en business-rapporten in Odoo.",
                  "Odoo-tool", RAPPORTEN_URL)

st.divider()
st.caption(f"🔗 Odoo: {odoo_url.replace('https://', '')}")
