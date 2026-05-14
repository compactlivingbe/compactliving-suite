"""Gedeelde auth gate voor alle pagina's.
Roep require_auth() aan bovenaan elke page.py voor de content.
"""
import os
import streamlit as st


def require_auth():
    """Toont login form en stopt page rendering als wachtwoord niet ok.
    Session state wordt gedeeld tussen alle pagina's, dus 1x login = overal toegang."""
    # Map secrets → env (idempotent)
    for key in ["ANTHROPIC_API_KEY", "ODOO_URL", "ODOO_DB", "ODOO_LOGIN",
                "ODOO_API_KEY", "ODOO_PASSWORD", "CLAUDE_MODEL",
                "PROFIWEB_USER", "PROFIWEB_PASS", "TOPSYSTEMS_XML_URL",
                "APP_PASSWORD"]:
        try:
            if key in st.secrets:
                os.environ[key] = st.secrets[key]
        except Exception:
            pass

    expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        return  # geen password = geen gate (lokaal dev)

    if st.session_state.get("pw_ok"):
        return

    # Login UI
    st.markdown("<div style='max-width:420px; margin:80px auto; text-align:center;'>",
                unsafe_allow_html=True)
    st.markdown("# ⚡ Compact Living Suite")
    st.caption("Beveiligde toegang")

    def _check():
        if st.session_state.get("pw_input") == expected:
            st.session_state["pw_ok"] = True
            del st.session_state["pw_input"]
        else:
            st.session_state["pw_ok"] = False

    st.text_input("🔑 Wachtwoord", type="password",
                  on_change=_check, key="pw_input")
    if "pw_ok" in st.session_state and not st.session_state["pw_ok"]:
        st.error("❌ Verkeerd wachtwoord")
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()
