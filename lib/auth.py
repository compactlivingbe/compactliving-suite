"""Gedeelde auth gate voor alle pagina's — login met Odoo-account.

Roep require_auth() aan bovenaan elke page.py voor de content.
De gebruiker logt in met zijn Odoo e-mail + wachtwoord OF API-key. De
credentials worden live gevalideerd tegen Odoo (JSON-RPC authenticate).
Enkel interne Odoo-gebruikers (geen portal/klant-accounts) krijgen toegang.

Login wordt ~30 dagen onthouden via een ondertekende (HMAC) cookie, zodat je
niet elke browsersessie opnieuw moet inloggen.
"""
import os
import time
import json
import hmac
import base64
import hashlib
import datetime
import requests
import streamlit as st

try:
    import extra_streamlit_components as stx
except Exception:
    stx = None

_SECRET_KEYS = [
    "ANTHROPIC_API_KEY", "ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY",
    "ODOO_PASSWORD", "CLAUDE_MODEL", "PROFIWEB_USER", "PROFIWEB_PASS",
    "TOPSYSTEMS_XML_URL", "APP_PASSWORD", "GH_TOKEN", "SCRAPER_API_KEY",
    "AUTH_SECRET",
]

COOKIE_NAME = "cl_auth"
COOKIE_DAYS = 30


def _map_secrets():
    """Streamlit secrets → environment variables (idempotent)."""
    for key in _SECRET_KEYS:
        try:
            if key in st.secrets:
                os.environ[key] = st.secrets[key]
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Cookie helpers (ondertekend token, niet vervalsbaar zonder secret)
# ---------------------------------------------------------------------------
def _signing_secret():
    return (os.environ.get("AUTH_SECRET") or os.environ.get("ODOO_API_KEY")
            or os.environ.get("APP_PASSWORD") or "compactliving-fallback-secret")


def _sign_token(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(_signing_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _verify_token(token: str):
    try:
        raw, sig = token.split(".", 1)
        expect = hmac.new(_signing_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        if float(payload.get("exp", 0)) < time.time():
            return None
        return payload
    except Exception:
        return None


def _get_cm():
    """Eén CookieManager per sessie (voorkomt DuplicateWidgetID)."""
    if stx is None:
        return None
    cm = st.session_state.get("_cookie_mgr")
    if cm is None:
        cm = stx.CookieManager(key="cl_cookie_mgr")
        st.session_state["_cookie_mgr"] = cm
    return cm


# ---------------------------------------------------------------------------
# Odoo login
# ---------------------------------------------------------------------------
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
    cm = _get_cm()
    if cm is not None:
        try:
            cm.delete(COOKIE_NAME)
        except Exception:
            pass


def current_user():
    return st.session_state.get("auth_user")


def require_auth():
    """Toont Odoo-login en stopt rendering tot er een geldige interne login is.
    Session state + cookie worden gedeeld over alle pagina's."""
    _map_secrets()

    if st.session_state.get("auth_user"):
        return

    url = os.environ.get("ODOO_URL", "")
    db = os.environ.get("ODOO_DB", "")

    # Lokale dev zonder Odoo-config: geen gate
    if not (url and db):
        return

    # ---- Probeer login uit cookie te herstellen ----
    cm = _get_cm()
    if cm is not None:
        try:
            token = cm.get(COOKIE_NAME)
        except Exception:
            token = None
        if token:
            payload = _verify_token(token)
            if payload:
                st.session_state["auth_user"] = {
                    "uid": payload.get("uid"), "name": payload.get("name"),
                    "login": payload.get("login"), "share": False,
                }
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
        remember = st.checkbox("Ingelogd blijven (30 dagen)", value=True)
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
                if remember and cm is not None:
                    try:
                        exp_ts = time.time() + COOKIE_DAYS * 86400
                        token = _sign_token({
                            "uid": user["uid"], "name": user["name"],
                            "login": user["login"], "exp": exp_ts,
                        })
                        cm.set(COOKIE_NAME, token,
                               expires_at=datetime.datetime.now()
                               + datetime.timedelta(days=COOKIE_DAYS))
                    except Exception:
                        pass
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()
