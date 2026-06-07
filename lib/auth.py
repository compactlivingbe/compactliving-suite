"""Gedeelde auth gate voor alle pagina's — login met Odoo-account.

Roep require_auth() aan bovenaan elke page.py voor de content.
De gebruiker logt in met zijn Odoo e-mail + wachtwoord OF API-key. De
credentials worden live gevalideerd tegen Odoo (JSON-RPC authenticate).
Enkel interne Odoo-gebruikers (geen portal/klant-accounts) krijgen toegang.
"""
import os
import requests
import streamlit as st

_SECRET_KEYS = [
    "ANTHROPIC_API_KEY", "ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY",
    "ODOO_PASSWORD", "CLAUDE_MODEL", "PROFIWEB_USER", "PROFIWEB_PASS",
    "TOPSYSTEMS_XML_URL", "APP_PASSWORD", "GH_TOKEN", "SCRAPER_API_KEY",
]


def _map_secrets():
    """Streamlit secrets → environment variables (idempotent)."""
    for key in _SECRET_KEYS:
        try:
            if key in st.secrets:
                os.environ[key] = st.secrets[key]
        except Exception:
            pass


def _jsonrpc(url, service, method, args, timeout=30):
    r = requests.post(
        f"{url.rstrip('/')}/jsonrpc",
        json={"jsonrpc": "2.0", "method": "call",
              "params": {"service": service, "method": method, "args": args}},
        timeout=timeout,
    )
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        msg = (d["error"].get("data", {}) or {}).get("message") or "Odoo fout"
        raise RuntimeError(msg)
    return d.get("result")


def _odoo_login(url, db, login, secret):
    """Valideer credentials tegen Odoo. -> dict(uid,name,login,share) of None."""
    uid = _jsonrpc(url, "common", "authenticate", [db, login, secret, {}])
    if not uid:
        return None
    res = _jsonrpc(url, "object", "execute_kw",
                   [db, uid, secret, "res.users", "read",
                    [[uid], ["name", "login", "share"]]])
    if not res:
        return {"uid": uid, "name": login, "login": login, "share": False}
    u = res[0]
    return {"uid": uid, "name": u.get("name") or login,
            "login": u.get("login") or login, "share": bool(u.get("share"))}


def logout():
    st.session_state.pop("auth_user", None)


def current_user():
    return st.session_state.get("auth_user")


def require_auth():
    """Toont Odoo-login en stopt rendering tot er een geldige interne login is.
    Session state wordt gedeeld over alle pagina's: 1x inloggen = overal toegang."""
    _map_secrets()

    if st.session_state.get("auth_user"):
        return

    url = os.environ.get("ODOO_URL", "")
    db = os.environ.get("ODOO_DB", "")

    # Lokale dev zonder Odoo-config: geen gate
    if not (url and db):
        return

    # ---- Login UI ----
    st.markdown("<div style='max-width:440px; margin:60px auto;'>",
                unsafe_allow_html=True)
    st.markdown("# ⚡ Compact Living Suite")
    st.caption("Log in met je Odoo-account")

    with st.form("odoo_login"):
        email = st.text_input("Odoo e-mail", autocomplete="username")
        secret = st.text_input("Wachtwoord of API-key", type="password",
                               autocomplete="current-password")
        submit = st.form_submit_button("🔐 Inloggen", use_container_width=True,
                                       type="primary")

    st.caption("ℹ️ Heb je tweestapsverificatie (2FA) aan? Gebruik dan een **API-key** "
               "(Odoo → Voorkeuren → Account Security → Developer API Keys).")

    if submit:
        if not (email and secret):
            st.error("Vul je e-mail en wachtwoord/API-key in.")
        else:
            try:
                with st.spinner("Inloggen bij Odoo..."):
                    user = _odoo_login(url, db, email.strip(), secret)
            except Exception as e:
                user = None
                st.error(f"Inloggen mislukt: {e}")
                st.markdown("</div>", unsafe_allow_html=True)
                st.stop()
            if not user:
                st.error("❌ Ongeldige e-mail of wachtwoord/API-key.")
            elif user["share"]:
                st.error("❌ Geen toegang: enkel interne gebruikers zijn toegelaten "
                         "(geen portal-/klantaccounts).")
            else:
                st.session_state["auth_user"] = user
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()
