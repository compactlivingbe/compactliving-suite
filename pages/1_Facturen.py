"""
Cloud-versie voor Streamlit Community Cloud.
- Geen file-based opslag (filesystem ephemeral)
- Password-gate voor toegang
- Secrets via st.secrets (niet .env)
- Upload → verwerk → resultaat → klaar (alle archief in Odoo)
"""
import os
import sys
import json
import csv
import base64
import tempfile
from pathlib import Path
from datetime import datetime

import requests
import streamlit as st
import pandas as pd

# ============ STREAMLIT CONFIG ============
st.set_page_config(
    page_title="Factuur Automatisering — Compact Living",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="auto"
)
from auth import require_auth
require_auth()


st.markdown("""
<style>
    .stApp { background-color: #FAFAFA; }
    h1 { color: #1F4E79 !important; font-weight: 700 !important; }
    h2 { color: #2E75B6 !important; font-weight: 600 !important; }
    h3 { color: #404040 !important; font-weight: 600 !important; }
    [data-testid="stMetricValue"] {
        font-size: 28px !important; font-weight: 700 !important; color: #1F4E79 !important;
    }
    [data-testid="stMetricLabel"] {
        font-weight: 500 !important; color: #606060 !important; font-size: 13px !important;
    }
    .stButton > button[kind="primary"] {
        background: #1F4E79; border: none; font-weight: 600;
    }
    .stButton > button[kind="primary"]:hover { background: #2E75B6; }
    [data-testid="stSidebar"] { background: #F0F4F8; }
    [data-testid="stDataFrame"] { border: 1px solid #E0E0E0; border-radius: 8px; }
    [data-baseweb="tab-list"] { gap: 8px; padding-bottom: 4px; }
    [data-baseweb="tab"] {
        font-size: 16px !important; font-weight: 600 !important;
        padding: 12px 24px !important; background: white !important;
        border: 1px solid #E0E0E0 !important; border-radius: 8px 8px 0 0 !important;
    }
    [data-baseweb="tab"][aria-selected="true"] {
        background: #1F4E79 !important; color: white !important;
    }
    .empty-state {
        text-align: center; padding: 80px 20px; color: #909090; background: white;
        border-radius: 12px; border: 2px dashed #E0E0E0;
    }
    footer { visibility: hidden; }
    .stDeployButton { display: none; }
</style>
""", unsafe_allow_html=True)


# Password gate handled in main streamlit_app.py


# ============ ODOO + ENV via st.secrets ============
# Map secrets naar os.environ zodat eigen modules werken
for key in ["ANTHROPIC_API_KEY", "ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY",
            "CLAUDE_MODEL"]:
    if key in st.secrets:
        os.environ[key] = st.secrets[key]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient
from extractor import extract_from_pdf
from matcher import find_partner, find_product_candidates
from bill_matcher import match_invoice_to_pos, create_bills_from_matches


def get_odoo():
    return OdooClient(
        url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
        login=os.environ["ODOO_LOGIN"], api_key=os.environ["ODOO_API_KEY"]
    )


def odoo_url(action: str, rec_id: int = None) -> str:
    base = os.environ.get("ODOO_URL", "")
    return f"{base}/odoo/{action}/{rec_id}" if rec_id else f"{base}/odoo/{action}"


def fmt_eur(n):
    if n is None or n == "":
        return "—"
    try:
        return f"€ {float(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(n)


def show_pdf(pdf_bytes: bytes, height: int = 500):
    b64 = base64.b64encode(pdf_bytes).decode()
    st.markdown(
        f'<iframe src="data:application/pdf;base64,{b64}#zoom=80" '
        f'style="width:100%; height:{height}px; border:1px solid #DDD; border-radius:6px;"></iframe>',
        unsafe_allow_html=True
    )


# ============ HEADER ============
col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.title("📄 Factuur Automatisering")
    st.caption("**Compact Living** — webshop-facturen → Odoo")
with col_h2:
    st.markdown(f"""
    <div style="text-align:right; padding-top:24px;">
        <a href="{os.environ.get('ODOO_URL', '#')}" target="_blank"
           style="background:#1F4E79; color:white; padding:10px 20px; border-radius:6px;
                  text-decoration:none; font-weight:600;">🔗 Open Odoo</a>
    </div>
    """, unsafe_allow_html=True)


# ============ SIDEBAR ============
with st.sidebar:
    st.markdown("### ⚙️ Verwerkingsinstellingen")
    sb_hint = st.text_input("Leverancier-hint", value="Reimo")

    try:
        odoo = get_odoo()
        projects = odoo.search_read(
            "account.analytic.account", [("active", "=", True)],
            ["id", "name"], 50, "name"
        )
        proj_options = {"(geen)": None}
        proj_options.update({p["name"]: p["id"] for p in projects})
        sb_proj = st.selectbox("📌 Project", list(proj_options.keys()))
        sb_analytic = proj_options[sb_proj]
    except Exception as e:
        st.error("Odoo offline")
        sb_analytic = None

    st.markdown("### 📦 Levering")
    sb_goods_received = st.toggle(
        "Goederen al geleverd?",
        value=False,
        help="Aan = bevestigt PO('s) + valideert Receipts (voorraad in). "
             "Uit = vooruitbetaling, geen voorraadboeking."
    )
    sb_review_unmatched = st.toggle(
        "🔍 Review onbekende producten",
        value=True,
        help="Aan = pauzeer voor onbekende lijnen, jij kiest product. "
             "Uit = auto-create nieuw product (storable)."
    )

    with st.expander("🔧 Geavanceerd"):
        sb_mode = st.radio("Modus", ["auto", "po", "bill"], index=0,
                           help="auto = check bestaande POs (default)", horizontal=True)
        sb_autoconfirm = st.checkbox("🔒 Auto-confirm PO (zonder Receipt-validatie)")

    st.divider()
    st.caption(f"Cloud · `{os.environ.get('ODOO_URL', '?').replace('https://','')}`")

    if st.button("🚪 Uitloggen"):
        st.session_state["pw_correct"] = False
        st.rerun()


# ============ TABS (cloud: vluchtig) ============
tab_verwerk, tab_ontvangst, tab_peppol, tab_overzicht = st.tabs([
    "📥 PDF → PO/Bill",
    "📦 Ontvangst & Bill",
    "📨 Peppol Bill → PO",
    "📋 Overzicht",
])


# ============ TAB 1: VERWERKEN ============
with tab_verwerk:
    st.markdown("### Upload + verwerk factuur")
    st.caption("Cloud-modus: upload → extractie → Odoo. Geen archief — alles zit in Odoo.")

    uploaded = st.file_uploader("📎 Upload factuur PDF", type="pdf", key="up_cloud")

    if uploaded:
        # Toon preview
        col_pdf, col_info = st.columns([1, 1])
        with col_pdf:
            show_pdf(uploaded.getvalue(), height=400)
        with col_info:
            st.markdown(f"**📄 {uploaded.name}**")
            st.caption(f"Grootte: {len(uploaded.getvalue()) // 1024} KB")

            if st.button("🚀 Extractie + analyse", type="primary", use_container_width=True):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded.getvalue())
                    tmp_path = Path(tmp.name)
                try:
                    with st.spinner("📤 Extractie via Claude API..."):
                        factuur = extract_from_pdf(
                            str(tmp_path), leverancier_hint=sb_hint,
                            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
                        )
                    odoo = get_odoo()
                    lev = factuur.get("leverancier", {})
                    partner = find_partner(odoo, lev.get("naam"), lev.get("vat"))
                    mr = match_invoice_to_pos(odoo, factuur, partner["id"]) if partner else None
                    # Bouw kandidaten voor unmatched lijnen
                    candidates_per_idx = {}
                    if mr:
                        for idx in mr["unmatched_idx"]:
                            ln = factuur["lijnen"][idx]
                            cands = find_product_candidates(
                                odoo, ln.get("beschrijving"), ln.get("artikelnummer"),
                                ln.get("eenheidsprijs_excl_btw"), top_n=5
                            )
                            candidates_per_idx[idx] = cands
                    st.session_state["pending"] = {
                        "factuur": factuur,
                        "partner": partner,
                        "match_result": mr,
                        "candidates": candidates_per_idx,
                        "pdf_bytes": uploaded.getvalue(),
                        "pdf_name": uploaded.name,
                    }
                except Exception as e:
                    st.error(f"❌ Extractie faalde: {e}")
                finally:
                    tmp_path.unlink(missing_ok=True)

    # ============ REVIEW + CONFIRM STEP ============
    pending = st.session_state.get("pending")
    if pending:
        st.divider()
        factuur = pending["factuur"]
        partner = pending["partner"]
        mr = pending["match_result"]
        lev = factuur.get("leverancier", {})
        n_lijnen = len(factuur.get("lijnen", []))
        tot = (factuur.get("factuur") or {}).get("totaal_excl_btw")

        st.markdown(f"### 📋 Review: **{lev.get('naam')}** — {n_lijnen} lijnen, {fmt_eur(tot)} excl BTW")

        if not partner:
            st.error(f"❌ Leverancier '{lev.get('naam')}' (BTW {lev.get('vat')}) niet in Odoo. "
                     "Maak partner aan en probeer opnieuw.")
            if st.button("🗑️ Reset"):
                del st.session_state["pending"]
                st.rerun()
        else:
            st.info(f"🏢 Leverancier: **{partner['name']}** (id {partner['id']})")
            n_open = len(mr["open_pos"])
            n_match = len(mr["matches"])
            match_pct = (n_match / n_lijnen * 100) if n_lijnen else 0
            st.markdown(f"📑 **{n_open} open PO(s)** · **{n_match}/{n_lijnen}** lijnen gematcht ({match_pct:.0f}%)")

            # Toon matched lijnen
            if mr["matches"]:
                with st.expander(f"✓ {n_match} gematchte lijnen (gelinkt aan bestaande PO)", expanded=False):
                    rows = [{
                        "Factuurlijn": (factuur["lijnen"][m["invoice_line_idx"]].get("beschrijving") or "")[:50],
                        "→ PO": m["po_name"],
                        "PO product": m["po_product"],
                        "Qty": m["qty_to_invoice"],
                        "Score": m["score"],
                    } for m in mr["matches"]]
                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

            # Product-review voor unmatched lijnen
            product_assignments = {}  # idx → product_id
            new_products_to_create = {}  # idx → {name, sku, price, is_storable}

            if mr["unmatched_idx"]:
                st.markdown(f"#### 🔍 Onbekende producten ({len(mr['unmatched_idx'])})")
                if not sb_review_unmatched:
                    st.caption("Review uit → wordt auto-aangemaakt als nieuw storable product.")
                else:
                    for idx in mr["unmatched_idx"]:
                        ln = factuur["lijnen"][idx]
                        cands = pending["candidates"].get(idx, [])
                        with st.container(border=True):
                            st.markdown(f"**Lijn {idx + 1}:** {ln.get('beschrijving', '')[:80]}")
                            cols = st.columns([2, 1, 1])
                            cols[0].caption(f"SKU factuur: `{ln.get('artikelnummer') or '—'}`")
                            cols[1].caption(f"Qty: {ln.get('hoeveelheid')}")
                            cols[2].caption(f"Prijs: {fmt_eur(ln.get('eenheidsprijs_excl_btw'))}")

                            options = ["🆕 Nieuw product aanmaken (storable)"]
                            for c in cands:
                                code = f"[{c.get('default_code') or '—'}]"
                                options.append(f"{code} {c['name'][:60]} (score {c['score']})")
                            choice = st.selectbox(
                                "Kies product:", options,
                                key=f"choice_{idx}",
                                index=1 if cands else 0
                            )
                            if choice == options[0]:
                                # Nieuw aanmaken
                                cnew = st.columns([2, 1])
                                new_name = cnew[0].text_input(
                                    "Productnaam", value=ln.get("beschrijving") or "",
                                    key=f"newname_{idx}"
                                )
                                new_sku = cnew[1].text_input(
                                    "SKU (optioneel)", value=ln.get("artikelnummer") or "",
                                    key=f"newsku_{idx}"
                                )
                                new_products_to_create[idx] = {
                                    "name": new_name, "sku": new_sku,
                                    "price": ln.get("eenheidsprijs_excl_btw") or 0,
                                    "is_dienst": ln.get("is_dienst", False),
                                }
                            else:
                                # Bestaand kiezen
                                chosen = cands[options.index(choice) - 1]
                                product_assignments[idx] = chosen["id"]
                                # Optie: SKU toevoegen aan bestaand product
                                if ln.get("artikelnummer") and not chosen.get("default_code"):
                                    if st.checkbox(
                                        f"➕ Voeg SKU `{ln.get('artikelnummer')}` toe aan dit product",
                                        key=f"addsku_{idx}"
                                    ):
                                        new_products_to_create[f"upd_{idx}"] = {
                                            "update_id": chosen["id"],
                                            "default_code": ln.get("artikelnummer"),
                                        }

            st.divider()
            use_bill = (sb_mode == "bill") or (sb_mode == "auto" and n_match > 0 and match_pct >= 50)
            mode_label = "🧾 BILL-modus (link aan bestaande PO's)" if use_bill else "📑 PO-modus (nieuwe PO)"
            st.markdown(f"**Modus:** {mode_label}")
            if sb_goods_received:
                st.markdown("📦 **Goederen geleverd** → PO('s) worden bevestigd + Receipts gevalideerd (voorraad in)")
            else:
                st.markdown("⏳ **Goederen NIET geleverd** → enkel Bill/PO, geen Receipt-validatie")

            cbtn = st.columns([1, 1])
            if cbtn[0].button("✅ Bevestig & verwerk in Odoo", type="primary", use_container_width=True):
                from verwerk import maak_purchase_order, confirm_pos, validate_receipts_for_pos, auto_create_product
                odoo = get_odoo()
                try:
                    # Stap 1: pas updates toe (SKU toevoegen)
                    for k, upd in new_products_to_create.items():
                        if isinstance(k, str) and k.startswith("upd_"):
                            odoo.write("product.product", [upd["update_id"]],
                                       {"default_code": upd["default_code"]})
                            st.caption(f"✓ SKU `{upd['default_code']}` toegevoegd aan product {upd['update_id']}")

                    # Stap 2: maak nieuwe producten aan voor unmatched
                    for idx, np in new_products_to_create.items():
                        if isinstance(idx, str):
                            continue
                        new_p = auto_create_product(
                            odoo, np["name"], np["sku"], np["price"], np["is_dienst"]
                        )
                        product_assignments[idx] = new_p["id"]
                        st.caption(f"✓ Nieuw product '{new_p['name'][:40]}' aangemaakt (id {new_p['id']})")

                    # Stap 3: maak Bill of PO
                    if use_bill and n_match > 0:
                        with st.spinner("Bill aanmaken..."):
                            res = create_bills_from_matches(
                                odoo, factuur, partner["id"],
                                mr["matches"], mr["unmatched_idx"],
                                product_assignments=product_assignments
                            )
                        if not res.get("bill_id"):
                            st.error(f"Bill faalde: {res.get('error')}")
                        else:
                            linked = ", ".join(res.get("linked_pos") or []) or "(geen)"
                            st.success(
                                f"✓ Bill aangemaakt (id {res['bill_id']}) — "
                                f"{res['n_total_lines']} lijnen, gelinkt aan PO('s): {linked}"
                            )
                            # PDF aanhangen
                            pdf_b64 = base64.b64encode(pending["pdf_bytes"]).decode()
                            odoo.create("ir.attachment", {
                                "name": pending["pdf_name"], "datas": pdf_b64,
                                "res_model": "account.move", "res_id": res["bill_id"],
                                "mimetype": "application/pdf"
                            })
                            st.info(f"📎 PDF aangehangen aan Bill")
                            # End-to-end: confirm POs + validate receipts
                            linked_po_ids = sorted({m["po_id"] for m in mr["matches"]})
                            if linked_po_ids:
                                cs = confirm_pos(odoo, linked_po_ids)
                                st.caption(f"PO confirm: {cs}")
                                if sb_goods_received:
                                    rs = validate_receipts_for_pos(odoo, linked_po_ids)
                                    st.caption(f"Receipts: {rs}")
                            st.link_button(
                                "🔗 Open Bill in Odoo",
                                odoo_url("action-account.action_move_in_invoice_type", res["bill_id"]),
                                type="primary"
                            )
                            st.balloons()
                            del st.session_state["pending"]
                    else:
                        # PO-modus: nieuwe PO maken (gebruik product_assignments via auto-create)
                        with st.spinner("PO aanmaken..."):
                            po_res = maak_purchase_order(
                                odoo, factuur, partner["id"],
                                analytic_id=sb_analytic,
                                auto_confirm=sb_autoconfirm or sb_goods_received,
                                auto_create_unmatched=not sb_review_unmatched  # bij review zijn ze al gemaakt
                            )
                        if po_res.get("po_id"):
                            st.success(f"✓ PO **{po_res['po_name']}** (id {po_res['po_id']}, state={po_res['po_state']})")
                            pdf_b64 = base64.b64encode(pending["pdf_bytes"]).decode()
                            odoo.create("ir.attachment", {
                                "name": pending["pdf_name"], "datas": pdf_b64,
                                "res_model": "purchase.order", "res_id": po_res["po_id"],
                                "mimetype": "application/pdf"
                            })
                            if sb_goods_received and po_res.get("po_state") == "purchase":
                                rs = validate_receipts_for_pos(odoo, [po_res["po_id"]])
                                st.caption(f"Receipts: {rs}")
                            st.link_button("🔗 Open PO in Odoo",
                                          odoo_url("purchase", po_res["po_id"]), type="primary")
                            st.balloons()
                            del st.session_state["pending"]
                        else:
                            st.error("Geen producten gematcht")
                except Exception as e:
                    st.error(f"❌ Fout: {e}")

            if cbtn[1].button("🗑️ Annuleer", use_container_width=True):
                del st.session_state["pending"]
                st.rerun()


# ============ TAB: ONTVANGST & BILL (PO → Receipt + Bill) ============
with tab_ontvangst:
    st.markdown("### 📦 Ontvangst valideren + Bill maken")
    st.caption("Voor confirmed PO's die wachten op levering en/of facturatie. "
               "Klik per PO: Ontvang (voorraad +) → Bill (PDF wordt gekopieerd).")

    try:
        odoo = get_odoo()
        # POs die nog Bill nodig hebben OF nog niet ontvangen zijn
        pos_pending = odoo.search_read(
            "purchase.order",
            [("state", "in", ["purchase", "done"]),
             "|", ("invoice_status", "=", "to invoice"),
                  ("invoice_status", "=", "no")],
            ["id", "name", "date_order", "partner_id", "amount_total",
             "invoice_status", "picking_ids", "invoice_ids", "partner_ref"],
            30, "id desc"
        )
        if not pos_pending:
            st.info("🎉 Geen openstaande PO's — alles is gefactureerd.")
        else:
            st.caption(f"{len(pos_pending)} PO('s) wachten op levering of factuur")
            for po in pos_pending:
                with st.container(border=True):
                    cols = st.columns([3, 2, 2, 2, 2, 2])
                    cols[0].markdown(f"**📑 {po['name']}** · {(po['partner_id'] or [None,'?'])[1]}")
                    cols[0].caption(f"Ref: {po.get('partner_ref') or '—'} · "
                                   f"Datum: {(po.get('date_order') or '')[:10]}")
                    cols[1].metric("Totaal", fmt_eur(po["amount_total"]))
                    cols[2].metric("Bills", len(po.get("invoice_ids") or []))
                    cols[3].caption(f"Status: **{po.get('invoice_status', '?')}**")

                    # Ontvang knop
                    if po.get("picking_ids"):
                        if cols[4].button("📦 Ontvang", key=f"recv_{po['id']}"):
                            try:
                                from verwerk import validate_receipts_for_pos
                                rs = validate_receipts_for_pos(odoo, [po["id"]])
                                st.success(f"✓ Receipt: {rs[po['id']]}")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Receipt faalde: {e}")
                    else:
                        cols[4].caption("(geen pickings)")

                    # Maak Bill knop
                    if cols[5].button("🧾 Maak Bill", key=f"bill_{po['id']}",
                                      type="primary"):
                        try:
                            from verwerk import create_bill_from_po
                            with st.spinner(f"Bill aanmaken voor {po['name']}..."):
                                res = create_bill_from_po(odoo, po["id"], copy_attachments=True)
                            if res.get("bill_id"):
                                st.success(
                                    f"✓ Bill **{res['bill_name']}** (id {res['bill_id']}) — "
                                    f"€{res['bill_total']:.2f} · "
                                    f"📎 {res['attachments_copied']} PDF(s) gekopieerd"
                                )
                                st.link_button(
                                    "🔗 Open Bill in Odoo",
                                    odoo_url("action-account.action_move_in_invoice_type", res["bill_id"]),
                                    type="primary"
                                )
                                st.balloons()
                            else:
                                st.error(f"Bill faalde: {res.get('error')}")
                        except Exception as e:
                            st.error(f"Bill creatie faalde: {e}")

                    cols[0].link_button("Open PO ↗", odoo_url("purchase", po["id"]))
    except Exception as e:
        st.error(f"Odoo verbinding faalde: {e}")


# ============ TAB: PEPPOL BILL → PO ============
PEPPOL_EXCLUDE_PATH = Path(__file__).resolve().parent.parent / "peppol_exclude.csv"
_GH_TOKEN = os.environ.get("GH_TOKEN", "")
_GH_REPO = os.environ.get("GH_REPO", "compactlivingbe/compactliving-suite")
_GH_BRANCH = os.environ.get("GH_BRANCH", "main")
_GH_PEPPOL_PATH = "peppol_exclude.csv"


def _peppol_gh_pull():
    if not _GH_TOKEN: return None
    try:
        r = requests.get(
            f"https://api.github.com/repos/{_GH_REPO}/contents/{_GH_PEPPOL_PATH}",
            headers={"Authorization": f"Bearer {_GH_TOKEN}",
                      "Accept": "application/vnd.github.raw"},
            params={"ref": _GH_BRANCH}, timeout=15)
        if r.status_code == 200:
            PEPPOL_EXCLUDE_PATH.write_text(r.text, encoding="utf-8")
            return True
    except Exception: pass
    return False


def _peppol_gh_push(msg):
    if not _GH_TOKEN: return False, "Geen GH_TOKEN"
    try:
        r = requests.get(
            f"https://api.github.com/repos/{_GH_REPO}/contents/{_GH_PEPPOL_PATH}",
            headers={"Authorization": f"Bearer {_GH_TOKEN}",
                      "Accept": "application/vnd.github+json"},
            params={"ref": _GH_BRANCH}, timeout=15)
        sha = r.json().get("sha") if r.status_code == 200 else None
        content = PEPPOL_EXCLUDE_PATH.read_text(encoding="utf-8")
        body = {"message": msg,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": _GH_BRANCH}
        if sha: body["sha"] = sha
        r = requests.put(
            f"https://api.github.com/repos/{_GH_REPO}/contents/{_GH_PEPPOL_PATH}",
            headers={"Authorization": f"Bearer {_GH_TOKEN}",
                      "Accept": "application/vnd.github+json"},
            json=body, timeout=20)
        return (r.status_code in (200, 201)), r.json().get("commit", {}).get("sha", "")[:7] or r.text[:120]
    except Exception as e:
        return False, str(e)


def _peppol_read():
    if _GH_TOKEN and "_peppol_excl_pulled" not in st.session_state:
        _peppol_gh_pull()
        st.session_state["_peppol_excl_pulled"] = True
    if not PEPPOL_EXCLUDE_PATH.exists(): return [], []
    rows, headers = [], []
    for ln in PEPPOL_EXCLUDE_PATH.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("partner_id,"):
            headers.append(ln); continue
        parts = next(csv.reader([ln]))
        if len(parts) >= 4:
            rows.append({"partner_id": int(parts[0]) if parts[0].isdigit() else 0,
                          "name": parts[1], "reason": parts[2], "date_added": parts[3]})
    return rows, headers


def _peppol_write(rows, headers):
    lines = [ln for ln in (headers or []) if not ln.startswith("partner_id,")]
    lines.insert(0, "partner_id,name,reason,date_added")
    out = "\n".join(lines) + "\n"
    for r in rows:
        out += f'{r["partner_id"]},{r["name"]},{r["reason"]},{r["date_added"]}\n'
    PEPPOL_EXCLUDE_PATH.write_text(out, encoding="utf-8")


def _peppol_add(partner_id, name, reason=""):
    rows, headers = _peppol_read()
    if any(r["partner_id"] == int(partner_id) for r in rows):
        return False
    rows.append({"partner_id": int(partner_id), "name": name,
                  "reason": reason, "date_added": datetime.now().strftime("%Y-%m-%d")})
    _peppol_write(rows, headers)
    return True


with tab_peppol:
    st.markdown("### 📨 Peppol Bills → Purchase Order")
    st.caption("Voor Bills die binnen kwamen via Peppol/email zonder gekoppelde PO. "
               "Match elke lijn aan een product (bestaand / nieuw / bewerken), maak dan PO.")

    # ============ EXCLUDE LIST ============
    excl_rows, excl_headers = _peppol_read()
    excluded_ids = {r["partner_id"] for r in excl_rows}
    with st.expander(f"🚫 Exclude-list leveranciers ({len(excl_rows)})", expanded=False):
        st.caption("Leveranciers in deze lijst worden NIET getoond in de Peppol → PO flow.")
        if _GH_TOKEN:
            st.success(f"☁ Persistent — commits naar `{_GH_REPO}`")
        else:
            st.warning("⚠ Geen `GH_TOKEN` — wijzigingen verloren bij container restart.")
        if excl_rows:
            edf = pd.DataFrame(excl_rows)
            edf.insert(0, "Verwijder", False)
            edited_excl = st.data_editor(
                edf, hide_index=True, use_container_width=True,
                disabled=["partner_id", "name", "date_added"],
                column_config={
                    "Verwijder": st.column_config.CheckboxColumn(width="small"),
                    "partner_id": st.column_config.NumberColumn("Partner ID", width="small"),
                    "name": st.column_config.TextColumn("Naam"),
                    "reason": st.column_config.TextColumn("Reden"),
                    "date_added": st.column_config.TextColumn("Toegevoegd", width="small"),
                },
                key="peppol_excl_table",
            )
            if st.button("💾 Wijzigingen opslaan", key="peppol_excl_save"):
                kept = [{"partner_id": int(r["partner_id"]), "name": r["name"],
                          "reason": r["reason"], "date_added": r["date_added"]}
                         for _, r in edited_excl.iterrows() if not r["Verwijder"]]
                _peppol_write(kept, excl_headers)
                if _GH_TOKEN:
                    ok, info = _peppol_gh_push(f"Peppol exclude-list: {len(kept)} suppliers via Streamlit")
                    if ok: st.success(f"✓ Opgeslagen + GitHub push ({info})")
                    else: st.error(f"Lokaal opgeslagen, push faalde: {info}")
                else:
                    st.success(f"✓ Opgeslagen ({len(kept)})")
                st.rerun()

        # Add new supplier
        st.markdown("**Leverancier toevoegen aan exclude-list:**")
        ac1, ac2, ac3 = st.columns([3, 2, 1])
        search_term = ac1.text_input("Zoek leverancier op naam", key="peppol_excl_search",
                                       placeholder="bijv. Reimo")
        excl_reason = ac2.text_input("Reden", value="geen PO via Peppol",
                                       key="peppol_excl_reason")
        if search_term and len(search_term) >= 2:
            try:
                odoo_tmp = get_odoo()
                cands = odoo_tmp.search_read(
                    "res.partner",
                    [("supplier_rank", ">", 0), ("name", "ilike", search_term)],
                    ["id", "name", "vat"], 15, "name")
                if cands:
                    opts = [f"[{c['id']}] {c['name']}" + (f" ({c['vat']})" if c.get('vat') else "")
                             for c in cands]
                    sel = ac1.selectbox("Kandidaat", ["(kies)"] + opts, key="peppol_excl_pick",
                                          label_visibility="collapsed")
                    if sel != "(kies)":
                        chosen = cands[opts.index(sel)]
                        if ac3.button("➕ Toevoegen", key="peppol_excl_add"):
                            if _peppol_add(chosen["id"], chosen["name"], excl_reason):
                                if _GH_TOKEN:
                                    ok, info = _peppol_gh_push(
                                        f"Peppol exclude: +{chosen['name']} ({chosen['id']})")
                                    st.success(f"✓ Toegevoegd + GitHub ({info})" if ok
                                                else f"Toegevoegd, push faalde: {info}")
                                else:
                                    st.success("✓ Toegevoegd (niet persistent)")
                                st.rerun()
                            else:
                                st.warning(f"{chosen['name']} stond al in exclude-list.")
                else:
                    ac1.caption("Geen kandidaten gevonden.")
            except Exception as e:
                st.error(f"Zoekfout: {e}")

    try:
        odoo = get_odoo()
        from verwerk import get_unlinked_draft_bills, create_po_from_bill, validate_receipts_for_pos, auto_create_product, create_product_with_supplier

        with st.spinner("Bills zonder PO-link zoeken..."):
            unlinked_all = get_unlinked_draft_bills(odoo, limit=30)

        # Filter via exclude-list
        unlinked = [b for b in unlinked_all
                    if not (b.get("partner_id") and b["partner_id"][0] in excluded_ids)]
        n_excl = len(unlinked_all) - len(unlinked)

        if not unlinked:
            if n_excl:
                st.info(f"🎉 Geen Bills te tonen ({n_excl} verborgen door exclude-list).")
            else:
                st.info("🎉 Geen losse draft Bills — alles is gelinkt aan een PO.")
        else:
            cap = f"{len(unlinked)} draft Bill(s) zonder PO-link"
            if n_excl:
                cap += f" · {n_excl} verborgen door exclude-list"
            st.caption(cap)
            for b in unlinked:
                with st.container(border=True):
                    cols = st.columns([3, 2, 2, 2])
                    cols[0].markdown(
                        f"**🧾 {b.get('name') or '(draft)'}** · "
                        f"{(b['partner_id'] or [None,'?'])[1]}"
                    )
                    cols[0].caption(
                        f"Ref: {b.get('ref') or '—'} · "
                        f"Datum: {(b.get('invoice_date') or '')[:10] or '—'} · "
                        f"{len(b.get('_lines', []))} lijnen"
                    )
                    cols[1].metric("Totaal", fmt_eur(b["amount_total"]))
                    cols[0].link_button("Open Bill ↗",
                                       odoo_url("action-account.action_move_in_invoice_type", b["id"]))
                    pep_received = cols[2].toggle("📦 Geleverd", value=False, key=f"pep_recv_{b['id']}")

                    bill_lines = b.get("_lines", [])
                    no_prod_lines = [ln for ln in bill_lines if not ln.get("product_id")]

                    # Per-lijn matcher UI in expander
                    if no_prod_lines:
                        with st.expander(f"⚠ {len(no_prod_lines)} lijn(en) zonder product — koppel hier", expanded=True):
                            st.caption("Selecteer per regel een bestaand product of maak een nieuw aan.")
                            for ln in bill_lines:
                                if ln.get("product_id"):
                                    cc = st.columns([3, 2, 1])
                                    cc[0].markdown(f"✓ **{(ln['product_id'] or [None,'?'])[1]}**")
                                    cc[1].caption((ln.get("name") or "")[:60])
                                    cc[2].caption(f"qty {ln.get('quantity')}")
                                    continue
                                lid = ln["id"]
                                key = f"pep_match_{b['id']}_{lid}"
                                cc = st.columns([2, 3, 2, 1.5, 1.5])
                                cc[0].caption("Beschrijving")
                                cc[0].markdown(f"_{(ln.get('name') or '')[:50]}_")
                                cc[1].caption("Match (Odoo zoeken)")
                                # Lazy load candidates per line, cached in session
                                cand_key = f"_cands_{lid}"
                                if cand_key not in st.session_state:
                                    cands = find_product_candidates(
                                        odoo,
                                        beschrijving=ln.get("name") or "",
                                        artikelnr=None,
                                        prijs_hint=ln.get("price_unit"),
                                        top_n=10,
                                    )
                                    st.session_state[cand_key] = cands
                                cands = st.session_state[cand_key]
                                opts = ["(kies / nieuw / open zoekveld)"] + \
                                       [f"[{c.get('default_code') or '—'}] {c['name']}  (€{c.get('standard_price', 0):.2f}, score {c.get('score', 0):.2f})"
                                        for c in cands]
                                sel_label = cc[1].selectbox("kandidaat", opts, key=f"{key}_sel",
                                                            label_visibility="collapsed")
                                idx = opts.index(sel_label) if sel_label in opts else 0
                                # Free-text search
                                search = cc[2].text_input("of typ naam", key=f"{key}_search",
                                                          label_visibility="collapsed",
                                                          placeholder="zoeken...")
                                if search and len(search) >= 2:
                                    extra = odoo.search_read(
                                        "product.product",
                                        ['|', ("name", "ilike", search), ("default_code", "ilike", search)],
                                        ["id", "name", "default_code", "list_price", "standard_price"], 10
                                    )
                                    if extra:
                                        opts2 = [f"[{e.get('default_code') or '—'}] {e['name']}" for e in extra]
                                        sel2 = cc[2].selectbox("zoekresultaten", ["(geen)"] + opts2,
                                                                key=f"{key}_search_sel",
                                                                label_visibility="collapsed")
                                        if sel2 != "(geen)":
                                            chosen = extra[opts2.index(sel2)]
                                            if cc[3].button("Koppel", key=f"{key}_link2"):
                                                odoo.call("account.move.line", "write",
                                                          [[lid], {"product_id": chosen["id"]}])
                                                st.success(f"✓ Gekoppeld aan [{chosen.get('default_code') or '—'}] {chosen['name']}")
                                                st.session_state.pop(cand_key, None)
                                                st.rerun()

                                # Action buttons
                                if idx > 0 and cc[3].button("Koppel", key=f"{key}_link"):
                                    chosen = cands[idx - 1]
                                    odoo.call("account.move.line", "write",
                                              [[lid], {"product_id": chosen["id"]}])
                                    st.success(f"✓ Gekoppeld aan {chosen['name']}")
                                    st.session_state.pop(cand_key, None)
                                    st.rerun()
                                # ➕ Nieuw product — popover met volledige form
                                with cc[4].popover("➕ Nieuw", use_container_width=True):
                                    bill_partner = b.get("partner_id") or [None, "?"]
                                    bill_partner_id = bill_partner[0]
                                    cost_default = float(ln.get("price_unit") or 0)
                                    qty_default = float(ln.get("quantity") or 1)
                                    name_default = (ln.get("name") or "")[:200]

                                    st.markdown(f"**Nieuw product voor:** _{name_default[:80]}_")
                                    st.caption(f"Bron: Bill van **{bill_partner[1]}** · {qty_default}× @ € {cost_default:.2f}")

                                    # Cache UoM + categorieën (probeer meerdere models)
                                    if "_uoms" not in st.session_state:
                                        uoms_loaded = []
                                        uom_err = None
                                        for model in ("uom.uom", "product.uom"):
                                            try:
                                                uoms_loaded = odoo.search_read(
                                                    model, [], ["id", "name"], 100, "name")
                                                if uoms_loaded:
                                                    break
                                            except Exception as e:
                                                uom_err = f"{model}: {e}"
                                        st.session_state["_uoms"] = uoms_loaded
                                        st.session_state["_uoms_err"] = uom_err
                                    if "_pcats" not in st.session_state:
                                        try:
                                            st.session_state["_pcats"] = odoo.search_read(
                                                "product.category", [], ["id", "complete_name"],
                                                200, "complete_name")
                                        except Exception as e:
                                            st.session_state["_pcats"] = []
                                            st.warning(f"Categorieën laden faalde: {e}")
                                    uoms = st.session_state["_uoms"]
                                    pcats = st.session_state["_pcats"]
                                    if not uoms:
                                        err = st.session_state.get("_uoms_err") or ""
                                        st.warning(
                                            "⚠ Geen UoM's gevonden in Odoo. "
                                            "Schakel 'Units of Measure' in via Instellingen → Inventory → "
                                            "Operations → Units of Measure." + (f"\n\n_Detail: {err}_" if err else ""))

                                    # ----- product velden -----
                                    new_name = st.text_input("Naam", value=name_default,
                                                              key=f"{key}_new_name")
                                    new_code = st.text_input("Interne referentie (optioneel)",
                                                              value="", key=f"{key}_new_code")
                                    fc1, fc2 = st.columns(2)
                                    with fc1:
                                        uom_labels = [f"{u['name']}" for u in uoms]
                                        # Default Units
                                        default_uom_idx = next(
                                            (i for i, u in enumerate(uoms) if u["name"].lower() in ("units", "stuks", "stuk", "pieces", "piece")),
                                            0 if uoms else 0)
                                        uom_sel = st.selectbox("Eenheid (UoM)",
                                                                ["(geen)"] + uom_labels,
                                                                index=default_uom_idx + 1 if uoms else 0,
                                                                key=f"{key}_new_uom")
                                        uom_id = uoms[uom_labels.index(uom_sel)]["id"] if uom_sel != "(geen)" else None
                                    with fc2:
                                        cat_labels = [c["complete_name"] for c in pcats]
                                        cat_sel = st.selectbox("Productcategorie",
                                                                ["(geen)"] + cat_labels,
                                                                key=f"{key}_new_cat")
                                        cat_id = pcats[cat_labels.index(cat_sel)]["id"] if cat_sel != "(geen)" else None

                                    fc3, fc4 = st.columns(2)
                                    with fc3:
                                        new_cost = st.number_input("Kostprijs (excl BTW)",
                                                                     min_value=0.0, value=cost_default,
                                                                     step=0.5, key=f"{key}_new_cost")
                                    with fc4:
                                        margin = st.number_input("Marge ×", min_value=1.0,
                                                                   value=1.32, step=0.05,
                                                                   key=f"{key}_new_margin")
                                    new_sale = st.number_input("Verkoopprijs (auto = kost × marge)",
                                                                 min_value=0.0,
                                                                 value=round(new_cost * margin, 2),
                                                                 step=0.5, key=f"{key}_new_sale")

                                    # ----- inkoop / supplier sectie -----
                                    st.markdown("**📥 Inkoop info (auto-ingevuld uit Bill):**")
                                    sc1, sc2 = st.columns(2)
                                    with sc1:
                                        st.text(f"Leverancier: {bill_partner[1]}")
                                        sup_code = st.text_input("Leveranciers­productcode",
                                                                   value="", key=f"{key}_new_supcode",
                                                                   help="Optioneel: artikelnr bij leverancier")
                                    with sc2:
                                        sup_qty = st.number_input("Min. bestel-aantal",
                                                                    min_value=0.0, value=qty_default,
                                                                    step=1.0, key=f"{key}_new_supqty")
                                        sup_price = st.number_input("Inkoopprijs leverancier",
                                                                      min_value=0.0, value=cost_default,
                                                                      step=0.5, key=f"{key}_new_supprice")
                                    sup_name_full = st.text_input(
                                        "Leveranciers­productnaam",
                                        value=name_default, key=f"{key}_new_supname",
                                        help="Naam zoals op leveranciersfactuur")

                                    if st.button("✓ Aanmaken + koppelen",
                                                  type="primary", key=f"{key}_new_create",
                                                  use_container_width=True):
                                        try:
                                            new = create_product_with_supplier(
                                                odoo,
                                                name=new_name, default_code=new_code or None,
                                                cost=new_cost, sale_price=new_sale,
                                                uom_id=uom_id, categ_id=cat_id,
                                                partner_id=bill_partner_id,
                                                supplier_code=sup_code or None,
                                                supplier_name=sup_name_full or None,
                                                supplier_qty=sup_qty,
                                                supplier_price=sup_price,
                                            )
                                            odoo.call("account.move.line", "write",
                                                       [[lid], {"product_id": new["id"]}])
                                            si_msg = f" + supplierinfo voor {bill_partner[1]}" if new.get("supplier_info_id") else ""
                                            st.success(f"✓ **{new['name']}** aangemaakt{si_msg}, gekoppeld aan lijn.")
                                            st.session_state.pop(cand_key, None)
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"Fout: {e}")
                    else:
                        # Toon read-only preview
                        with st.expander(f"📋 {len(bill_lines)} lijnen (alle gematcht)", expanded=False):
                            line_rows = [{
                                "Product": (ln["product_id"] or [None, "(geen)"])[1],
                                "Naam": (ln.get("name") or "")[:60],
                                "Qty": ln.get("quantity"),
                                "Prijs": ln.get("price_unit"),
                            } for ln in bill_lines]
                            if line_rows:
                                st.dataframe(pd.DataFrame(line_rows), hide_index=True,
                                           use_container_width=True)

                    # Maak PO knop
                    if cols[3].button("➕ Maak PO", key=f"pep_po_{b['id']}", type="primary",
                                      disabled=bool(no_prod_lines),
                                      help="Eerst alle lijnen koppelen" if no_prod_lines else None):
                        try:
                            with st.spinner("PO aanmaken vanuit Bill..."):
                                res = create_po_from_bill(
                                    odoo, b["id"],
                                    link_back=True, confirm=True,
                                    validate_receipt=pep_received,
                                )
                            if res.get("po_id"):
                                st.success(
                                    f"✓ PO **{res['po_name']}** (id {res['po_id']}, "
                                    f"state={res['po_state']}) · {res['n_lines']} lijnen · "
                                    f"{res['n_linked_back']} bill-lijnen terug-gelinkt"
                                )
                                if pep_received:
                                    st.caption("📦 Receipt gevalideerd → voorraad +")
                                st.link_button("🔗 Open PO in Odoo",
                                              odoo_url("purchase", res["po_id"]),
                                              type="primary")
                                st.balloons()
                            else:
                                st.error(f"PO faalde: {res.get('error')}")
                        except Exception as e:
                            st.error(f"PO creatie faalde: {e}")
    except Exception as e:
        st.error(f"Odoo verbinding faalde: {e}")


# ============ TAB 2: OVERZICHT (read-only uit Odoo) ============
with tab_overzicht:
    st.caption("Live data uit Odoo. Klik op een PO of Bill om in Odoo te openen.")
    try:
        odoo = get_odoo()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("📑 PO Draft", odoo.call("purchase.order", "search_count", [[("state", "=", "draft")]]))
        c2.metric("✅ PO Confirmed", odoo.call("purchase.order", "search_count", [[("state", "=", "purchase")]]))
        c3.metric("🧾 Bills draft", odoo.call("account.move", "search_count",
                                              [[("move_type", "=", "in_invoice"), ("state", "=", "draft")]]))
        c4.metric("✓ Bills posted", odoo.call("account.move", "search_count",
                                              [[("move_type", "=", "in_invoice"), ("state", "=", "posted")]]))

        st.divider()
        view = st.radio("Toon:", ["📑 Recente Purchase Orders", "🧾 Recente Vendor Bills"],
                       horizontal=True)

        if view == "📑 Recente Purchase Orders":
            pos = odoo.search_read("purchase.order", [],
                                  ["id", "name", "date_order", "partner_id", "amount_total",
                                   "state", "partner_ref", "invoice_status"], 30, "id desc")
            if pos:
                rows = [{
                    "📑 PO": p["name"],
                    "📅": (p.get("date_order") or "")[:10],
                    "🏢 Leverancier": (p["partner_id"] or [None, "—"])[1],
                    "📋 Ref": p.get("partner_ref") or "—",
                    "💰 Totaal": p["amount_total"],
                    "🔄 State": p["state"],
                    "🧾 Bill": p.get("invoice_status", "—"),
                    "Open": odoo_url("purchase", p["id"]),
                } for p in pos]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                           column_config={
                               "💰 Totaal": st.column_config.NumberColumn(format="€ %.2f"),
                               "Open": st.column_config.LinkColumn(display_text="↗"),
                           })
        else:
            bills = odoo.search_read("account.move",
                                    [("move_type", "=", "in_invoice")],
                                    ["id", "name", "invoice_date", "partner_id",
                                     "amount_total", "state", "payment_state", "ref"],
                                    30, "id desc")
            if bills:
                rows = [{
                    "🧾 Bill": b.get("name") or "(draft)",
                    "📅": (b.get("invoice_date") or "")[:10] or "—",
                    "🏢 Leverancier": (b["partner_id"] or [None, "—"])[1],
                    "📋 Ref": b.get("ref") or "—",
                    "💰 Totaal": b["amount_total"],
                    "🔄 State": b["state"],
                    "💸 Betaling": b.get("payment_state") or "—",
                    "Open": odoo_url("action-account.action_move_in_invoice_type", b["id"]),
                } for b in bills]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                           column_config={
                               "💰 Totaal": st.column_config.NumberColumn(format="€ %.2f"),
                               "Open": st.column_config.LinkColumn(display_text="↗"),
                           })
    except Exception as e:
        st.error(f"Odoo verbinding faalde: {e}")


st.markdown(
    f'<div style="text-align:center; color:#909090; font-size:11px; padding:8px;">'
    f'Cloud-versie · 100% in Odoo opgeslagen'
    f'</div>', unsafe_allow_html=True
)
