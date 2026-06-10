"""Top Systems Victron prijssync — interactief rapport."""
import os, sys, subprocess, tempfile, urllib.request, json, csv, base64
from pathlib import Path
from datetime import datetime
import streamlit as st
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient

try:
    st.set_page_config(page_title="Top Systems prijzen", page_icon="💰", layout="wide")
except Exception:
    pass

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

# ============ GITHUB PERSISTENT STORAGE ============
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_REPO = os.environ.get("GH_REPO", "compactlivingbe/compactliving-suite")
GH_BRANCH = os.environ.get("GH_BRANCH", "main")
GH_FILE_PATH = "skip_list.csv"


def gh_pull_skip_list():
    """Haal de actuele skip_list.csv op van GitHub (laatst gecommitte versie).
    Schrijft naar lokaal bestand zodat we altijd met de echte source-of-truth werken."""
    if not GH_TOKEN:
        return None
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE_PATH}",
            headers={"Authorization": f"Bearer {GH_TOKEN}",
                      "Accept": "application/vnd.github+json"},
            params={"ref": GH_BRANCH}, timeout=15,
        )
        if r.status_code == 200:
            d = r.json()
            content = base64.b64decode(d["content"]).decode("utf-8")
            SKIP_LIST_PATH.write_text(content, encoding="utf-8")
            return d["sha"]
    except Exception as e:
        st.warning(f"GitHub pull faalde: {e}")
    return None


def gh_push_skip_list(commit_msg):
    """Push lokale skip_list.csv naar GitHub via Contents API."""
    if not GH_TOKEN:
        return False, "Geen GH_TOKEN secret ingesteld"
    try:
        # Eerst sha ophalen
        r = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE_PATH}",
            headers={"Authorization": f"Bearer {GH_TOKEN}",
                      "Accept": "application/vnd.github+json"},
            params={"ref": GH_BRANCH}, timeout=15,
        )
        sha = r.json()["sha"] if r.status_code == 200 else None
        content = SKIP_LIST_PATH.read_text(encoding="utf-8")
        body = {"message": commit_msg,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": GH_BRANCH}
        if sha:
            body["sha"] = sha
        r = requests.put(
            f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE_PATH}",
            headers={"Authorization": f"Bearer {GH_TOKEN}",
                      "Accept": "application/vnd.github+json"},
            json=body, timeout=20,
        )
        if r.status_code in (200, 201):
            return True, r.json()["commit"]["sha"][:7]
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)


# Pull-on-load: zodat container-restarts altijd actuele lijst hebben
if GH_TOKEN and "_skip_pulled" not in st.session_state:
    gh_pull_skip_list()
    st.session_state["_skip_pulled"] = True


def get_odoo():
    return OdooClient(url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
                       login=os.environ["ODOO_LOGIN"], api_key=os.environ.get("ODOO_API_KEY", ""))


# ============ SKIP LIST ============
def _read_skip_rows():
    """Returns (rows, header_lines). rows = list of dicts with code/description/reason/date_added."""
    if not SKIP_LIST_PATH.exists():
        return [], ["code,description,reason,date_added"]
    text = SKIP_LIST_PATH.read_text(encoding="utf-8")
    rows, header_lines = [], []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("code,"):
            header_lines.append(ln)
            continue
        # CSV parse — backwards compat: oude lijst had code,reason,date_added (3 kolommen)
        parts = next(csv.reader([ln]))
        if len(parts) >= 4:
            code, desc, reason, dt = parts[0], parts[1], parts[2], parts[3]
        elif len(parts) == 3:
            # oude formaat: code,reason,date
            code, desc, reason, dt = parts[0], "", parts[1], parts[2]
        elif len(parts) == 2:
            code, desc, reason, dt = parts[0], "", parts[1], ""
        else:
            code, desc, reason, dt = parts[0], "", "", ""
        rows.append({"code": code, "description": desc, "reason": reason, "date_added": dt})
    return rows, header_lines


def _write_skip_rows(rows, header_lines=None):
    lines = list(header_lines or ["code,description,reason,date_added"])
    # Vervang oude header door nieuwe als die niet aanwezig is
    if not any(ln.startswith("code,") for ln in lines):
        lines.insert(0, "code,description,reason,date_added")
    # Verwijder eventuele oude header
    lines = [ln for ln in lines if not ln.startswith("code,reason,date_added")]
    if not any(ln.startswith("code,description") for ln in lines):
        lines.insert(0, "code,description,reason,date_added")
    out = "\n".join(lines) + "\n"
    buf = []
    w = csv.writer(_StringWriter(buf))
    for r in rows:
        w.writerow([r["code"], r["description"], r["reason"], r["date_added"]])
    out += "".join(buf)
    SKIP_LIST_PATH.write_text(out, encoding="utf-8")


class _StringWriter:
    def __init__(self, buf): self.buf = buf
    def write(self, s): self.buf.append(s)


with st.expander(f"📋 Skip-list bekijken / bewerken ({SKIP_LIST_PATH.name})", expanded=False):
    st.caption("Codes in deze lijst worden NIET als 'missing' Victron product behandeld.")
    if GH_TOKEN:
        st.success(f"☁ Persistent opslag actief — wijzigingen worden gepusht naar `{GH_REPO}` ({GH_BRANCH})")
    else:
        st.warning("⚠ Geen `GH_TOKEN` secret ingesteld — wijzigingen gaan verloren bij container restart. "
                    "Stel een GitHub fine-grained PAT in (repo Contents: write) als `GH_TOKEN` in Streamlit secrets.")
    skip_rows, skip_headers = _read_skip_rows()
    st.info(f"📊 {len(skip_rows)} codes in skip-list")
    if skip_rows:
        skip_df = pd.DataFrame(skip_rows)
        skip_df.insert(0, "Verwijder", False)
        edited_skip = st.data_editor(
            skip_df, hide_index=True, use_container_width=True,
            disabled=["code", "date_added"],
            column_config={
                "Verwijder": st.column_config.CheckboxColumn(width="small"),
                "code": st.column_config.TextColumn(width="small"),
                "description": st.column_config.TextColumn("Beschrijving", width="large"),
                "reason": st.column_config.TextColumn("Reden"),
                "date_added": st.column_config.TextColumn("Toegevoegd", width="small"),
            },
            key="skiplist_table",
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Wijzigingen opslaan", key="save_skip"):
                kept = [r for _, r in edited_skip.iterrows() if not r["Verwijder"]]
                n_removed = len(skip_rows) - len(kept)
                _write_skip_rows([{"code": r["code"], "description": r["description"],
                                    "reason": r["reason"], "date_added": r["date_added"]}
                                   for r in kept], skip_headers)
                if GH_TOKEN:
                    ok, info = gh_push_skip_list(
                        f"Skip-list update: {len(kept)} codes (-{n_removed}) via Streamlit")
                    if ok: st.success(f"✓ Opgeslagen + gepusht naar GitHub ({info})")
                    else: st.error(f"Lokaal opgeslagen maar GitHub push faalde: {info}")
                else:
                    st.success(f"✓ Lokaal opgeslagen ({len(kept)} codes) — NB: niet persistent zonder GH_TOKEN")
                st.rerun()
        with c2:
            n_del = int(edited_skip["Verwijder"].sum()) if not edited_skip.empty else 0
            st.caption(f"{n_del} aangevinkt om te verwijderen")
    # Raw editor als fallback
    with st.expander("Raw CSV bewerken", expanded=False):
        raw = SKIP_LIST_PATH.read_text(encoding="utf-8") if SKIP_LIST_PATH.exists() else ""
        new_raw = st.text_area("CSV", value=raw, height=200, key="skiplist_raw")
        if st.button("💾 Raw opslaan", key="save_skip_raw"):
            SKIP_LIST_PATH.write_text(new_raw, encoding="utf-8")
            if GH_TOKEN:
                ok, info = gh_push_skip_list("Skip-list raw edit via Streamlit")
                if ok: st.success(f"✓ Opgeslagen + gepusht naar GitHub ({info})")
                else: st.error(f"Lokaal opgeslagen maar GitHub push faalde: {info}")
            else:
                st.success("✓ Lokaal opgeslagen (niet persistent zonder GH_TOKEN)")
            st.rerun()


def add_to_skip_list(code, reason="", description=""):
    rows, headers = _read_skip_rows()
    if any(r["code"] == code for r in rows):
        return False
    rows.append({"code": code, "description": description, "reason": reason,
                  "date_added": datetime.now().strftime("%Y-%m-%d")})
    _write_skip_rows(rows, headers)
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
    extra_csv = latest_csv("extra")

    miss_df = pd.read_csv(miss_csv) if miss_csv else pd.DataFrame()
    cost_df = pd.read_csv(cost_csv) if cost_csv else pd.DataFrame()
    sale_df = pd.read_csv(sale_csv) if sale_csv else pd.DataFrame()
    extra_df = pd.read_csv(extra_csv) if extra_csv else pd.DataFrame()

    cm1, cm2, cm3, cm4 = st.columns(4)
    cm1.metric("Ontbrekend in Odoo", len(miss_df))
    cm2.metric("Kostprijs verschillen", len(cost_df))
    cm3.metric("Verkoopprijs verschillen", len(sale_df))
    cm4.metric("Niet meer in XML", len(extra_df))

    tabs = st.tabs([
        f"❓ Ontbrekend ({len(miss_df)})",
        f"📥 Kostprijs ({len(cost_df)})",
        f"📤 Verkoopprijs ({len(sale_df)})",
        f"❌ Verdwenen ({len(extra_df)})",
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
                            desc = str(r.get("description", "")).strip()
                            if add_to_skip_list(str(r["code"]), reason, desc):
                                added += 1
                        if GH_TOKEN and added:
                            ok, info = gh_push_skip_list(
                                f"Skip-list: +{added} codes via Streamlit ({reason})")
                            if ok: st.success(f"✓ {added} toegevoegd + gepusht naar GitHub ({info})")
                            else: st.error(f"{added} lokaal toegevoegd maar GitHub push faalde: {info}")
                        else:
                            st.success(f"✓ {added} codes toegevoegd aan skip-list"
                                        + ("" if GH_TOKEN else " (niet persistent zonder GH_TOKEN)"))
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

    # --------- TAB 4: VERDWENEN (niet meer in XML) ---------
    with tabs[3]:
        if extra_df.empty:
            st.success("Alle Top Systems producten in Odoo staan nog in de XML 🎉")
        else:
            st.caption("Deze producten hebben een Top Systems supplierinfo in Odoo maar "
                       "staan niet meer in de XML. Kandidaten om te archiveren.")
            df_show = extra_df.copy()
            df_show.insert(0, "Selecteer", False)
            edited = st.data_editor(
                df_show, hide_index=True, use_container_width=True,
                disabled=[c for c in df_show.columns if c != "Selecteer"],
                column_config={
                    "current_supplier_price": st.column_config.NumberColumn("Inkoop", format="€ %.2f"),
                    "supplierinfo_id": None,
                },
                key="extra_editor",
            )
            sel = edited[edited["Selecteer"]]
            st.markdown(f"**{len(sel)} geselecteerd**")
            if not sel.empty and st.button(f"📦 Archiveer {len(sel)} in Odoo",
                                            type="primary", key="archive_extra"):
                ok = err = 0
                for _, r in sel.iterrows():
                    try:
                        si = odoo.read("product.supplierinfo", [int(r["supplierinfo_id"])],
                                       ["product_tmpl_id"])
                        tid = si[0]["product_tmpl_id"][0] if si and si[0].get("product_tmpl_id") else None
                        if tid:
                            odoo.write("product.template", [tid], {"active": False})
                            ok += 1
                        else:
                            err += 1
                    except Exception as e:
                        err += 1
                        st.error(f"{r['code']}: {e}")
                st.success(f"✓ {ok} gearchiveerd · {err} fout")
