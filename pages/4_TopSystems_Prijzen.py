"""Top Systems Victron prijssync."""
import os, sys, subprocess, tempfile, urllib.request
from pathlib import Path
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

st.set_page_config(page_title="Top Systems prijzen", page_icon="💰", layout="wide")

from auth import require_auth
require_auth()

st.title("💰 Top Systems Victron prijssync")
st.caption("Vergelijk Top Systems XML productlijst met Odoo + update Victron prijzen.")

# Skip-list management
SKIP_LIST_PATH = Path(__file__).resolve().parent.parent / "skip_list.csv"
with st.expander(f"📋 Skip-list bekijken / bewerken ({SKIP_LIST_PATH.name})", expanded=False):
    st.caption("Codes in deze lijst worden NIET geïmporteerd als 'missing' Victron product. "
               "Eerste regel = headers, # = commentaar.")
    if SKIP_LIST_PATH.exists():
        current = SKIP_LIST_PATH.read_text(encoding="utf-8")
        n_codes = sum(1 for ln in current.splitlines()
                      if ln.strip() and not ln.strip().startswith("#") and not ln.startswith("code,"))
        st.info(f"📊 {n_codes} codes in skip-list")
    else:
        current = "code,reason,date_added\n"
        st.warning("skip_list.csv bestaat niet — wordt aangemaakt bij opslag.")
    edited = st.text_area("Bewerk skip-list", value=current, height=300, key="skiplist_edit")
    sk_col1, sk_col2 = st.columns([1, 4])
    with sk_col1:
        if st.button("💾 Opslaan", key="save_skip"):
            SKIP_LIST_PATH.write_text(edited, encoding="utf-8")
            st.success(f"✓ Opgeslagen ({SKIP_LIST_PATH})")
            st.caption("⚠ Op Streamlit Cloud: tijdelijk tot redeploy. Voor permanent: commit + push naar Git.")
            st.rerun()
    with sk_col2:
        st.caption("Op Streamlit Cloud blijven wijzigingen alleen in deze sessie. "
                    "Voor permanent: edit het bestand in GitHub of run lokaal + commit.")

xml_url_env = os.environ.get("TOPSYSTEMS_XML_URL", "")
col1, col2 = st.columns([3, 1])
with col1:
    xml_input = st.text_input("XML URL of upload bestand", value=xml_url_env,
                               placeholder="https://shop.top.systems/api/...")
with col2:
    uploaded = st.file_uploader("of upload XML", type="xml")

dry_run = st.checkbox("🔬 Dry run (geen Odoo wijzigingen)", value=True)
apply_cost = st.checkbox("Update kostprijzen (supplierinfo.price)", value=True)
apply_sale = st.checkbox("Update verkoopprijzen (template.list_price)", value=True)

if st.button("▶ Analyseren", type="primary"):
    # Acquire XML
    if uploaded:
        xml_path = tempfile.NamedTemporaryFile(delete=False, suffix=".xml").name
        Path(xml_path).write_bytes(uploaded.getvalue())
        st.info(f"Upload {len(uploaded.getvalue())//1024} KB ontvangen")
    elif xml_input:
        xml_path = tempfile.NamedTemporaryFile(delete=False, suffix=".xml").name
        with st.spinner("Download XML..."):
            urllib.request.urlretrieve(xml_input, xml_path)
        st.info(f"Download: {Path(xml_path).stat().st_size//1024} KB")
    else:
        st.error("Geef een URL of upload een XML.")
        st.stop()

    # Build temp config from env
    cfg = {
        "odoo": {
            "url": os.environ.get("ODOO_URL", ""),
            "db": os.environ.get("ODOO_DB", ""),
            "user": os.environ.get("ODOO_LOGIN", ""),
            "password": os.environ.get("ODOO_PASSWORD", ""),
        }
    }
    import json
    cfg_path = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w").name
    Path(cfg_path).write_text(json.dumps(cfg))

    # Run analyze
    args = [sys.executable, str(Path(__file__).resolve().parent.parent / "lib" / "topsystems_sync.py"),
            "--xml", xml_path, "--config", cfg_path]
    if not dry_run and apply_cost: args.append("--apply-cost")
    if not dry_run and apply_sale: args.append("--apply-sale")
    if not dry_run and apply_cost and apply_sale: args.append("--apply")

    log_box = st.empty()
    log = ""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8")
    for line in proc.stdout:
        log += line
        log_box.code(log[-3000:], language="")
    proc.wait()
    if proc.returncode == 0:
        st.success("✓ Analyse voltooid")
    else:
        st.error(f"✗ Exit code {proc.returncode}")
