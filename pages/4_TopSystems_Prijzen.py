"""Top Systems Victron prijssync — interactief rapport."""
import os, sys, subprocess, tempfile, urllib.request, json, csv
from pathlib import Path
from datetime import datetime
import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient

st.set_page_config(page_title="Top Systems prijzen", page_icon="💰", layout="wide")

from auth import require_auth
require_auth()

st.title("💰 Top Systems Victron prijssync")
st.caption("Vergelijk Top Systems XML productlijst met Odoo + update Victron prijzen.")

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_LIST_PATH = REPO_ROOT / "skip_list.csv"
REPORTS_DIR = REPO_ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
TS_PARTNER_ID = 690  # Top Systems BV
DEFAULT_MARGIN = 1.32  # 32% marge default voor verkoopprijs


def get_odoo():
    return OdooClient(url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
                       login=os.environ["ODOO_LOGIN"], api_key=os.environ.get("ODOO_API_KEY", ""))


# ============ SKIP LIST ============
with st.expander(f"📋 Skip-list bekijken / bewerken ({SKIP_LIST_PATH.name})", expanded=False):
    st.caption("Codes in deze lijst worden NIET als 'missing' Victron product behandeld.")
    if SKIP_LIST_PATH.exists():
        current = SKIP_LIST_PATH.read_text(encoding="utf-8")
        n_codes = sum(1 for ln in current.splitlines()
                      if ln.strip() and not ln.strip().startswith("#") and not ln.startswith("code,"))
        st.info(f"📊 {n_codes} codes in skip-list")
    else:
        current = "code,reason,date_added\n"
    edited_skip = st.text_area("Bewerk skip-list", value=current, height=200, key="skiplist_edit")
    if st.button("💾 Opslaan skip-list", key="save_skip"):
        SKIP_LIST_PATH.write_text(edited_skip, encoding="utf-8")
        st.success(f"✓ Opgeslagen ({n_codes if SKIP_LIST_PATH.exists() else 0} codes)")
        st.rerun()


def add_to_skip_list(code, reason=""):
    cur = SKIP_LIST_PATH.read_text(encoding="utf-8") if SKIP_LIST_PATH.exists() else "code,reason,date_added\n"
    if code in cur:
        return False  # al aanwezig
    today = datetime.now().strftime("%Y-%m-%d")
    cur += f"{code},{reason},{today}\n"
    SKIP_LIST_PATH.write_text(cur, encoding="utf-8")
    return True


# ============ XML INPUT ============
xml_url_env = os.environ.get("TOPSYSTEMS_XML_URL", "")
col1, col2 = st.columns([3, 1])
with col1:
    xml_input = st.text_input("XML URL of upload bestand", value=xml_url_env,
                               placeholder="https://shop.top.systems/api/...")
with col2:
    uploaded = st.file_uploader("of upload XML", type="xml")

run_btn = st.button("▶ Analyseren", type="primary")


def latest_csv(prefix):
    files = sorted(REPORTS_DIR.glob(f"{prefix}_*.csv"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


if run_btn:
    if uploaded:
        xml_path = tempfile.NamedTemporaryFile(delete=False, suffix=".xml").name
        Path(xml_path).write_bytes(uploaded.getvalue())
        st.info(f"Upload {len(uploaded.getvalue())//1024} KB ontvangen")
    elif xml_input:
        xml_path = tempfile.NamedTemporaryFile(delete=False, suffix=".xml").name
        with st.spinner("Download XML..."):
            urllib.request.urlretrieve(xml_input, xml_path)
    else:
        st.error("Geef een URL of upload een XML.")
        st.stop()

    cfg = {"odoo": {"url": os.environ.get("ODOO_URL",""), "db": os.environ.get("ODOO_DB",""),
                     "user": os.environ.get("ODOO_LOGIN",""), "password": os.environ.get("ODOO_PASSWORD","")}}
    cfg_path = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w").name
    Path(cfg_path).write_text(json.dumps(cfg))

    # Run analyse alleen (no --apply, doen we per rij)
    args = [sys.executable, str(REPO_ROOT / "lib" / "topsystems_sync.py"),
            "--xml", xml_path, "--config", cfg_path]
    log_box = st.empty()
    log = ""
    with st.spinner("Analyseren..."):
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8")
        for line in proc.stdout:
            log += line
            log_box.code(log[-3000:], language="")
        proc.wait()
    if proc.returncode == 0:
        st.success("✓ Analyse voltooid — bekijk de resultaten hieronder")
        st.session_state["_ts_analyzed"] = True
    else:
        st.error(f"✗ Exit code {proc.returncode}")


# ============ INTERACTIVE REPORTS ============
if st.session_state.get("_ts_analyzed") or any(REPORTS_DIR.glob("missing_*.csv")):
    st.divider()
    st.markdown("## 📊 Resultaten")

    # Load latest CSVs
    miss_csv = latest_csv("missing")
    cost_csv = latest_csv("cost_diffs")
    sale_csv = latest_csv("sale_diffs")

    miss_df = pd.read_csv(miss_csv) if miss_csv else pd.DataFrame()
    cost_df = pd.read_csv(cost_csv) if cost_csv else pd.DataFrame()
    sale_df = pd.read_csv(sale_csv) if sale_csv else pd.DataFrame()

    cm1, cm2, cm3 = st.columns(3)
    cm1.metric("Ontbrekend in Odoo", len(miss_df))
    cm2.metric("Kostprijs verschillen", len(cost_df))
    cm3.metric("Verkoopprijs verschillen", len(sale_df))

    tabs = st.tabs([
        f"❓ Ontbrekend ({len(miss_df)})",
        f"📥 Kostprijs ({len(cost_df)})",
        f"📤 Verkoopprijs ({len(sale_df)})",
    ])

    odoo = get_odoo()

    # --------- TAB 1: MISSING ---------
    with tabs[0]:
        if miss_df.empty:
            st.success("Geen ontbrekende Victron producten 🎉")
        else:
            st.caption("Per rij: voeg toe aan Odoo, of zet op skip-list voor toekomstige negering.")
            search = st.text_input("🔍 Filter", key="miss_search", placeholder="zoek code/beschrijving")
            df_show = miss_df.copy()
            if search:
                mask = df_show.apply(lambda r: search.lower() in " ".join(str(v) for v in r.values).lower(), axis=1)
                df_show = df_show[mask]
            df_show.insert(0, "Acties", "")
            df_show.insert(0, "Selecteer", False)
            edited = st.data_editor(
                df_show, hide_index=True, use_container_width=True,
                disabled=[c for c in df_show.columns if c not in ("Selecteer",)],
                column_config={
                    "pricegross_incl": st.column_config.NumberColumn("Bruto", format="€ %.2f"),
                    "pricenett_excl": st.column_config.NumberColumn("Netto", format="€ %.2f"),
                    "Acties": st.column_config.TextColumn(width="small"),
                },
                key="miss_editor",
            )
            sel = edited[edited["Selecteer"]]
            st.markdown(f"**{len(sel)} geselecteerd**")
            if not sel.empty:
                margin = st.slider("Marge voor verkoopprijs (×)", 1.0, 3.0, DEFAULT_MARGIN, 0.05,
                                    key="miss_margin")
                act_col1, act_col2 = st.columns(2)
                with act_col1:
                    if st.button(f"➕ Voeg {len(sel)} toe in Odoo", key="add_miss",
                                  type="primary"):
                        added = errs = 0
                        for _, r in sel.iterrows():
                            try:
                                cost = float(r["pricenett_excl"])
                                tid = odoo.create("product.template", {
                                    "name": str(r["description"]).strip(),
                                    "default_code": str(r["code"]).strip(),
                                    "type": "consu",
                                    "is_storable": True,
                                    "standard_price": cost,
                                    "list_price": round(cost * margin, 2),
                                })
                                # Supplierinfo
                                odoo.create("product.supplierinfo", {
                                    "partner_id": TS_PARTNER_ID,
                                    "product_tmpl_id": tid,
                                    "product_code": str(r["code"]).strip(),
                                    "price": cost, "min_qty": 1, "delay": 1,
                                })
                                added += 1
                            except Exception as e:
                                errs += 1
                                st.error(f"  {r['code']}: {e}")
                        st.success(f"✓ {added} toegevoegd · {errs} fout")
                        st.rerun()
                with act_col2:
                    reason = st.text_input("Reden voor skip", value="niet nodig", key="skip_reason")
                    if st.button(f"🚫 Voeg {len(sel)} toe aan skip-list", key="skip_miss"):
                        added = 0
                        for _, r in sel.iterrows():
                            if add_to_skip_list(str(r["code"]), reason):
                                added += 1
                        st.success(f"✓ {added} codes toegevoegd aan skip-list")
                        st.rerun()

    # --------- TAB 2: COST DIFFS ---------
    with tabs[1]:
        if cost_df.empty:
            st.success("Geen kostprijs verschillen 🎉")
        else:
            st.caption("Vink aan en klik 'Toepassen' om supplierinfo.price te updaten.")
            df_show = cost_df.copy()
            df_show["Verschil"] = df_show["new_pricenett"] - df_show["current_supplier_price"]
            df_show.insert(0, "Selecteer", True)  # default alle aangevinkt
            edited = st.data_editor(
                df_show, hide_index=True, use_container_width=True,
                disabled=[c for c in df_show.columns if c != "Selecteer"],
                column_config={
                    "current_supplier_price": st.column_config.NumberColumn("Huidig", format="€ %.2f"),
                    "new_pricenett": st.column_config.NumberColumn("Nieuw", format="€ %.2f"),
                    "Verschil": st.column_config.NumberColumn("Δ", format="€ %.2f"),
                    "supplierinfo_id": None,
                },
                key="cost_editor",
            )
            sel = edited[edited["Selecteer"]]
            if not sel.empty and st.button(f"✓ Pas {len(sel)} kostprijs updates toe",
                                            type="primary", key="apply_cost"):
                ok = err = 0
                for _, r in sel.iterrows():
                    try:
                        odoo.write("product.supplierinfo", [int(r["supplierinfo_id"])],
                                    {"price": float(r["new_pricenett"])})
                        ok += 1
                    except Exception as e:
                        err += 1
                        st.error(f"{r['code']}: {e}")
                st.success(f"✓ {ok} updates toegepast, {err} fout")

    # --------- TAB 3: SALE DIFFS ---------
    with tabs[2]:
        if sale_df.empty:
            st.success("Geen verkoopprijs verschillen 🎉")
        else:
            st.caption("Pas marge aan om verkoopprijs aan te passen, of update naar de XML waarde.")
            global_margin = st.slider("Globale marge multiplier (×)", 1.0, 3.0, DEFAULT_MARGIN, 0.05,
                                       key="sale_margin",
                                       help="Hiermee bereken je 'Voorgesteld' = kostprijs × marge")
            df_show = sale_df.copy()
            # Need cost prijzen om marge toe te passen
            tmpl_ids = df_show["template_id"].astype(int).tolist()
            tmpls = odoo.read("product.template", tmpl_ids,
                                ["standard_price"]) if tmpl_ids else []
            cost_by_tmpl = {t["id"]: t["standard_price"] for t in tmpls}
            df_show["kostprijs"] = df_show["template_id"].astype(int).map(cost_by_tmpl)
            df_show["voorgesteld"] = (df_show["kostprijs"] * global_margin).round(2)
            df_show["Δ huidige"] = df_show["new_list_price"] - df_show["current_list_price"]
            df_show["Toepassen"] = "XML"  # default: gebruik XML waarde
            df_show.insert(0, "Selecteer", True)
            edited = st.data_editor(
                df_show, hide_index=True, use_container_width=True,
                disabled=[c for c in df_show.columns if c not in ("Selecteer", "Toepassen")],
                column_config={
                    "current_list_price": st.column_config.NumberColumn("Huidig", format="€ %.2f"),
                    "new_list_price": st.column_config.NumberColumn("XML waarde", format="€ %.2f"),
                    "kostprijs": st.column_config.NumberColumn("Kost", format="€ %.2f"),
                    "voorgesteld": st.column_config.NumberColumn("Voorgesteld (kost×marge)", format="€ %.2f"),
                    "Δ huidige": st.column_config.NumberColumn("Δ", format="€ %.2f"),
                    "Toepassen": st.column_config.SelectboxColumn(
                        "Toepassen", options=["XML", "Voorgesteld", "Skip"]),
                    "template_id": None,
                },
                key="sale_editor",
            )
            sel = edited[edited["Selecteer"]]
            if not sel.empty and st.button(f"✓ Pas {len(sel)} verkoopprijs updates toe",
                                            type="primary", key="apply_sale"):
                ok = err = 0
                for _, r in sel.iterrows():
                    if r["Toepassen"] == "Skip":
                        continue
                    new_price = float(r["new_list_price"]) if r["Toepassen"] == "XML" else float(r["voorgesteld"])
                    try:
                        odoo.write("product.template", [int(r["template_id"])],
                                    {"list_price": new_price})
                        ok += 1
                    except Exception as e:
                        err += 1
                        st.error(f"{r['code']}: {e}")
                st.success(f"✓ {ok} updates toegepast, {err} fout")
