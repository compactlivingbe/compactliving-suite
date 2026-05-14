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
    initial_sidebar_state="expanded",
)

# Map secrets → env (zodat alle modules werken zonder st.secrets dependency)
for key in ["ANTHROPIC_API_KEY", "ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY",
            "ODOO_PASSWORD", "CLAUDE_MODEL", "PROFIWEB_USER", "PROFIWEB_PASS",
            "TOPSYSTEMS_XML_URL", "APP_PASSWORD"]:
    try:
        if key in st.secrets:
            os.environ[key] = st.secrets[key]
    except Exception:
        pass


# ============ AUTH ============
def check_password():
    expected = os.environ.get("APP_PASSWORD") or st.secrets.get("APP_PASSWORD", "") if hasattr(st, "secrets") else ""
    if not expected:
        return True   # geen password ingesteld = geen gate (lokaal dev)

    def password_entered():
        if st.session_state.get("pw_input") == expected:
            st.session_state["pw_ok"] = True
            del st.session_state["pw_input"]
        else:
            st.session_state["pw_ok"] = False

    if st.session_state.get("pw_ok"):
        return True

    st.markdown("<div style='max-width:420px; margin:80px auto; text-align:center;'>",
                unsafe_allow_html=True)
    st.markdown("# ⚡ Compact Living Suite")
    st.caption("Beveiligde toegang")
    st.text_input("🔑 Wachtwoord", type="password",
                  on_change=password_entered, key="pw_input")
    if "pw_ok" in st.session_state and not st.session_state["pw_ok"]:
        st.error("❌ Verkeerd wachtwoord")
    st.markdown("</div>", unsafe_allow_html=True)
    return False


if not check_password():
    st.stop()


# ============ LANDING ============
st.title("⚡ Compact Living Suite")
st.caption("Centrale dashboard voor leveranciers-automatiseringen")

odoo_url = os.environ.get("ODOO_URL", "https://compactliving.odoo.com")

st.markdown("### Modules")

c1, c2 = st.columns(2)

with c1:
    with st.container(border=True):
        st.markdown("#### 📄 Facturen")
        st.write("Upload PDF van leveranciersfactuur → Claude API extractie → Odoo PO + Bill.")
        st.caption("Reimo, All-Spark, Bauhaus, Top Systems, …")
        st.page_link("pages/1_Facturen.py", label="Open →", icon="📄")

    with st.container(border=True):
        st.markdown("#### 📦 Reimo beschikbaarheid")
        st.write("Scrape Reimo Profiweb voor alle Reimo-codes. Schrijft "
                 "verkooporder waarschuwing per Odoo product template.")
        st.caption("Wekelijks automatisch via GitHub Actions, of nu handmatig.")
        st.page_link("pages/2_Reimo_Sync.py", label="Open →", icon="📦")

with c2:
    with st.container(border=True):
        st.markdown("#### 🛒 Reimo bestellen")
        st.write("Selecteer bevestigde inkooporders met leverancier Reimo → "
                 "plaats bestelling automatisch in Profiweb winkelmandje.")
        st.caption("Beta — vereist HAR-capture van checkout flow voor full auto.")
        st.page_link("pages/3_Reimo_Bestellen.py", label="Open →", icon="🛒")

    with st.container(border=True):
        st.markdown("#### 💰 Top Systems prijzen")
        st.write("Vergelijk Top Systems XML productlijst met Odoo. "
                 "Update Victron kostprijzen + verkoopprijzen.")
        st.caption("Maandelijks automatisch, of nu handmatig.")
        st.page_link("pages/4_TopSystems_Prijzen.py", label="Open →", icon="💰")

    with st.container(border=True):
        st.markdown("#### 🔗 Product groepen")
        st.write("Markeer gelijkaardige producten (van verschillende leveranciers) als groep. "
                 "Vergelijk inkoop- en verkoopprijzen naast elkaar.")
        st.caption("AI suggereert automatisch mogelijke groepen.")
        st.page_link("pages/5_Product_Groepen.py", label="Open →", icon="🔗")

st.divider()

# Quick links
cl1, cl2, cl3, cl4 = st.columns(4)
with cl1:
    st.metric("Odoo", odoo_url.replace("https://", ""))
with cl2:
    st.markdown(f"[📊 Dashboard]({odoo_url})")
with cl3:
    st.markdown("[🐙 GitHub repo](https://github.com)")
with cl4:
    if st.button("🚪 Uitloggen"):
        st.session_state["pw_ok"] = False
        st.rerun()
