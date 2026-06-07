"""
Compact Living Suite — entry point, auth gate + gegroepeerde navigatie.

Multi-page Streamlit app combineert:
- Factuurverwerking (PDF → Odoo PO/Bill)
- Reimo Profiweb beschikbaarheid sync
- Reimo automatische bestellingen (PO → Profiweb)
- Top Systems Victron prijssync
- VBD Autoterm standkachels sync
- SO opvolging + product groepen
- Doorklik naar Odoo-tools (project, rapporten, productbeheer)

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
    initial_sidebar_state="expanded",
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


# ============ Externe Odoo-tools ============
odoo_url = os.environ.get("ODOO_URL", "https://compactliving.odoo.com").rstrip("/")
PROJECT_URL = f"{odoo_url}/inttools/project-verwerken"
RAPPORTEN_URL = f"{odoo_url}/inttools/rapporten"
PRODUCTBEHEER_URL = f"{odoo_url}/inttools/productbeheer"


# ============ Pagina-definities (st.navigation) ============
PAGES = {
    "facturen":        st.Page("pages/1_Facturen.py",        title="Facturen",              icon="📄"),
    "reimo_bestellen": st.Page("pages/3_Reimo_Bestellen.py", title="Reimo bestellen",       icon="🛒"),
    "reimo_sync":      st.Page("pages/2_Reimo_Sync.py",      title="Reimo beschikbaarheid", icon="📦"),
    "topsystems":      st.Page("pages/4_TopSystems_Prijzen.py", title="Top Systems prijzen", icon="💰"),
    "vbd":             st.Page("pages/6_VBD_Standkachels.py", title="VBD Standkachels",     icon="🔥"),
    "so_opvolging":    st.Page("pages/7_SO_Opvolging.py",    title="SO Opvolging",          icon="📊"),
    "product_groepen": st.Page("pages/5_Product_Groepen.py", title="Product groepen",       icon="🔗"),
}


# ============ LANDING ============
def render_home():
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
            st.link_button("Openen in Odoo ↗", url, use_container_width=False)

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
                      "Reimo, All-Spark, Bauhaus, Top Systems, …", PAGES["facturen"])
    with i2:
        card_internal("🛒", "Reimo bestellen", "Bevestigde Reimo-inkooporders automatisch in het "
                      "Profiweb-winkelmandje plaatsen.",
                      "Max 10 lijnen per order; winkelmand-beheer ingebouwd.", PAGES["reimo_bestellen"])
    with i3:
        card_external("🏗️", "Project verwerken", "Camperbouw-projecten verwerken en opvolgen in Odoo.",
                      "Odoo-tool", PROJECT_URL)

    # ---- 2. Leverancier-sync (prijzen & beschikbaarheid) ----
    st.markdown("### 🔄 Leverancier-sync — prijzen & beschikbaarheid")
    s1, s2, s3 = st.columns(3)
    with s1:
        card_internal("📦", "Reimo beschikbaarheid", "Scrape Reimo Profiweb en schrijf een "
                      "beschikbaarheids-waarschuwing per Odoo-product.",
                      "Wekelijks automatisch of handmatig.", PAGES["reimo_sync"])
    with s2:
        card_internal("💰", "Top Systems prijzen", "Vergelijk de Top Systems XML-lijst met Odoo en "
                      "update Victron kost- en verkoopprijzen.",
                      "Maandelijks automatisch of handmatig.", PAGES["topsystems"])
    with s3:
        card_internal("🔥", "VBD Standkachels", "Scrape vbdservices.nl (Autoterm) → importeer "
                      "ontbrekende producten, update kost- en verkoopprijzen.",
                      "Prijzen incl BTW; kost = excl BTW (NL 21%).", PAGES["vbd"])

    # ---- 3. Producten & orders ----
    st.markdown("### 📦 Producten & orders")
    p1, p2, p3 = st.columns(3)
    with p1:
        card_internal("📊", "SO Opvolging", "Per sales order: op voorraad, gekoppelde PO + status, "
                      "en verwachte levertijd bij Reimo.",
                      "Enkel fysieke producten; live Reimo-check.", PAGES["so_opvolging"])
    with p2:
        card_internal("🔗", "Product groepen", "Gelijkaardige producten van verschillende leveranciers "
                      "groeperen en prijzen vergelijken.",
                      "AI suggereert mogelijke groepen.", PAGES["product_groepen"])
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


home_page = st.Page(render_home, title="Home", icon="🏠", default=True)


# ============ Navigatie (gegroepeerde sidebar) ============
nav = st.navigation({
    "": [home_page],
    "Inkoop & facturen": [PAGES["facturen"], PAGES["reimo_bestellen"]],
    "Leverancier-sync": [PAGES["reimo_sync"], PAGES["topsystems"], PAGES["vbd"]],
    "Producten & orders": [PAGES["so_opvolging"], PAGES["product_groepen"]],
})


# ============ Sidebar: gebruiker, Odoo-tools, uitloggen ============
with st.sidebar:
    _u = current_user()
    if _u:
        st.markdown(f"👤 **{_u['name']}**")

    st.markdown("---")
    st.caption("Odoo-tools")
    st.link_button("🏗️ Project verwerken", PROJECT_URL, use_container_width=True)
    st.link_button("🛠️ Product beheer", PRODUCTBEHEER_URL, use_container_width=True)
    st.link_button("📈 Rapporten", RAPPORTEN_URL, use_container_width=True)

    st.markdown("---")
    if st.button("🚪 Uitloggen", use_container_width=True):
        logout()
        st.rerun()


nav.run()
