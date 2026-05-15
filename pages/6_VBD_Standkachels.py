"""VBD Services (Autoterm standkachels) prijssync."""
import os, sys, csv, base64
from pathlib import Path
from datetime import datetime
import streamlit as st
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient
from vbd_scraper import fetch_all, compare_with_odoo, DEFAULT_CATEGORIES, BASE

st.set_page_config(page_title="VBD Standkachels", page_icon="🔥", layout="wide")

from auth import require_auth
require_auth()

st.title("🔥 VBD Services — Autoterm standkachels sync")
st.caption("Scrape vbdservices.nl (openbare prijzen incl BTW) → vergelijk met Odoo → import/update.")

VBD_PARTNER_ID = 56
DEFAULT_MARGIN = 1.32

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_LIST_PATH = REPO_ROOT / "skip_list_vbd.csv"

# ============ GITHUB PERSISTENT STORAGE ============
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_REPO = os.environ.get("GH_REPO", "compactlivingbe/compactliving-suite")
GH_BRANCH = os.environ.get("GH_BRANCH", "main")
GH_FILE_PATH = "skip_list_vbd.csv"


def gh_pull():
    if not GH_TOKEN: return None
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE_PATH}",
            headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"},
            params={"ref": GH_BRANCH}, timeout=15)
        if r.status_code == 200:
            content = base64.b64decode(r.json()["content"]).decode("utf-8")
            SKIP_LIST_PATH.write_text(content, encoding="utf-8")
            return r.json()["sha"]
    except Exception as e:
        st.warning(f"GitHub pull faalde: {e}")
    return None


def gh_push(commit_msg):
    if not GH_TOKEN: return False, "Geen GH_TOKEN secret"
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE_PATH}",
            headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"},
            params={"ref": GH_BRANCH}, timeout=15)
        sha = r.json()["sha"] if r.status_code == 200 else None
        content = SKIP_LIST_PATH.read_text(encoding="utf-8")
        body = {"message": commit_msg,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": GH_BRANCH}
        if sha: body["sha"] = sha
        r = requests.put(
            f"https://api.github.com/repos/{GH_REPO}/contents/{GH_FILE_PATH}",
            headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"},
            json=body, timeout=20)
        if r.status_code in (200, 201):
            return True, r.json()["commit"]["sha"][:7]
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)


if GH_TOKEN and "_vbd_skip_pulled" not in st.session_state:
    gh_pull()
    st.session_state["_vbd_skip_pulled"] = True


def _read_skip_rows():
    if not SKIP_LIST_PATH.exists():
        return [], ["sku,description,reason,date_added"]
    rows, headers = [], []
    for ln in SKIP_LIST_PATH.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("sku,") or s.startswith("code,"):
            headers.append(ln); continue
        parts = next(csv.reader([ln]))
        if len(parts) >= 4:
            rows.append({"sku": parts[0], "description": parts[1],
                          "reason": parts[2], "date_added": parts[3]})
        elif len(parts) == 3:
            rows.append({"sku": parts[0], "description": "",
                          "reason": parts[1], "date_added": parts[2]})
        else:
            rows.append({"sku": parts[0], "description": "", "reason": "", "date_added": ""})
    return rows, headers


class _StringWriter:
    def __init__(self, buf): self.buf = buf
    def write(self, s): self.buf.append(s)


def _write_skip_rows(rows, headers=None):
    lines = list(headers or [])
    if not any(ln.startswith("sku,") for ln in lines):
        lines.insert(0, "sku,description,reason,date_added")
    out = "\n".join(lines) + "\n"
    buf = []
    w = csv.writer(_StringWriter(buf))
    for r in rows:
        w.writerow([r["sku"], r["description"], r["reason"], r["date_added"]])
    out += "".join(buf)
    SKIP_LIST_PATH.write_text(out, encoding="utf-8")


def add_to_skip_list(sku, reason="", description=""):
    rows, headers = _read_skip_rows()
    if any(r["sku"] == sku for r in rows):
        return False
    rows.append({"sku": sku, "description": description, "reason": reason,
                  "date_added": datetime.now().strftime("%Y-%m-%d")})
    _write_skip_rows(rows, headers)
    return True


def get_skip_skus():
    return {r["sku"] for r in _read_skip_rows()[0]}


def get_odoo():
    return OdooClient(url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
                       login=os.environ["ODOO_LOGIN"], api_key=os.environ.get("ODOO_API_KEY", ""))


# ============ SKIP LIST UI ============
with st.expander(f"📋 Skip-list bekijken / bewerken ({SKIP_LIST_PATH.name})", expanded=False):
    st.caption("SKU's in deze lijst worden NIET als 'ontbrekend' getoond.")
    if GH_TOKEN:
        st.success(f"☁ Persistent — commits naar `{GH_REPO}` ({GH_BRANCH})")
    else:
        st.warning("⚠ Geen `GH_TOKEN` — wijzigingen verloren bij container restart.")
    skip_rows, skip_headers = _read_skip_rows()
    st.info(f"📊 {len(skip_rows)} SKU's in skip-list")
    if skip_rows:
        skip_df = pd.DataFrame(skip_rows)
        skip_df.insert(0, "Verwijder", False)
        edited_skip = st.data_editor(
            skip_df, hide_index=True, use_container_width=True,
            disabled=["sku", "date_added"],
            column_config={
                "Verwijder": st.column_config.CheckboxColumn(width="small"),
                "sku": st.column_config.TextColumn(width="small"),
                "description": st.column_config.TextColumn("Beschrijving", width="large"),
                "reason": st.column_config.TextColumn("Reden"),
                "date_added": st.column_config.TextColumn("Toegevoegd", width="small"),
            },
            key="vbd_skiplist_table",
        )
        if st.button("💾 Wijzigingen opslaan", key="vbd_save_skip"):
            kept = [r for _, r in edited_skip.iterrows() if not r["Verwijder"]]
            n_removed = len(skip_rows) - len(kept)
            _write_skip_rows([{"sku": r["sku"], "description": r["description"],
                                "reason": r["reason"], "date_added": r["date_added"]}
                               for r in kept], skip_headers)
            if GH_TOKEN:
                ok, info = gh_push(f"VBD skip-list: {len(kept)} SKU's (-{n_removed}) via Streamlit")
                if ok: st.success(f"✓ Opgeslagen + GitHub push ({info})")
                else: st.error(f"Lokaal opgeslagen, push faalde: {info}")
            else:
                st.success(f"✓ Lokaal opgeslagen ({len(kept)} SKU's)")
            st.rerun()


# ============ CONFIG ============
with st.expander("⚙ Categorieën om te scrapen", expanded=False):
    st.caption("Standaard alle Autoterm-categorieën. Voeg URL-paden toe (één per regel).")
    cats_text = st.text_area("Categorie-paden", value="\n".join(DEFAULT_CATEGORIES),
                              height=200, key="vbd_cats")
    selected_cats = [c.strip() for c in cats_text.splitlines() if c.strip()]
    st.caption(f"{len(selected_cats)} categorieën geselecteerd.")


col1, col2 = st.columns([3, 1])
with col1:
    delay = st.slider("Delay tussen pagina's (sec)", 0.0, 2.0, 0.4, 0.1)
with col2:
    run_btn = st.button("▶ Scrape + analyseren", type="primary", use_container_width=True)


# ============ SCRAPE ============
if run_btn:
    log_box = st.empty()
    log_lines = []

    def log(msg):
        log_lines.append(str(msg))
        log_box.code("\n".join(log_lines[-30:]), language=None)

    with st.spinner("VBD productlijst ophalen..."):
        products = fetch_all(selected_cats, log=log, delay=delay)
    st.success(f"✓ {len(products)} unieke producten gevonden op VBD")

    with st.spinner("Vergelijken met Odoo..."):
        odoo = get_odoo()
        result = compare_with_odoo(odoo, VBD_PARTNER_ID, products, log=log)

    st.session_state["_vbd_result"] = result
    st.session_state["_vbd_products"] = products


# ============ RESULTAAT ============
if "_vbd_result" in st.session_state:
    result = st.session_state["_vbd_result"]
    st.divider()
    st.markdown("## 📊 Resultaten")

    cm1, cm2, cm3, cm4 = st.columns(4)
    cm1.metric("VBD producten", result["total_vbd"])
    cm2.metric("Match in Odoo", result["total_matched"])
    cm3.metric("Ontbrekend", len(result["missing"]))
    cm4.metric("Kostprijs Δ", len(result["cost_diffs"]))

    skip_skus = get_skip_skus()
    filtered_missing = [p for p in result["missing"] if p["sku"] not in skip_skus]
    n_skipped = len(result["missing"]) - len(filtered_missing)
    if n_skipped:
        st.caption(f"ℹ️ {n_skipped} ontbrekende SKU's verborgen door skip-list.")
    miss_df = pd.DataFrame(filtered_missing)
    cost_df = pd.DataFrame(result["cost_diffs"])
    sale_df = pd.DataFrame(result["sale_diffs"])
    match_df = pd.DataFrame(result.get("matches", []))

    tabs = st.tabs([
        f"❓ Ontbrekend ({len(miss_df)})",
        f"📥 Kostprijs ({len(cost_df)})",
        f"📤 Verkoopprijs ({len(sale_df)})",
        f"✓ Matches ({len(match_df)})",
    ])

    odoo = get_odoo()

    # ----- TAB 1: MISSING -----
    with tabs[0]:
        if miss_df.empty:
            st.success("Alle VBD producten zitten al in Odoo 🎉")
        else:
            st.caption("Selecteer producten om toe te voegen aan Odoo (template + supplierinfo voor VBD).")
            search = st.text_input("🔍 Filter", key="vbd_miss_search")
            df_show = miss_df.copy()
            if search:
                mask = df_show.apply(
                    lambda r: search.lower() in " ".join(str(v) for v in r.values).lower(),
                    axis=1)
                df_show = df_show[mask]
            df_show.insert(0, "Selecteer", False)
            edited = st.data_editor(
                df_show, hide_index=True, use_container_width=True,
                disabled=[c for c in df_show.columns if c != "Selecteer"],
                column_config={
                    "price_incl": st.column_config.NumberColumn("Incl BTW", format="€ %.2f"),
                    "price_excl": st.column_config.NumberColumn("Excl BTW (kost)", format="€ %.2f"),
                    "url": st.column_config.LinkColumn("Bekijk"),
                    "image_url": st.column_config.ImageColumn("Foto", width="small"),
                    "category": None, "description": None,
                },
                key="vbd_miss_editor",
            )
            sel = edited[edited["Selecteer"]]
            st.markdown(f"**{len(sel)} geselecteerd**")
            if not sel.empty:
                margin = st.slider("Marge voor verkoopprijs (×)", 1.0, 3.0, DEFAULT_MARGIN, 0.05,
                                    key="vbd_miss_margin",
                                    help="Verkoopprijs = kost (excl BTW) × marge. Excl BTW.")
                use_vbd_sale = st.checkbox(
                    "Gebruik VBD incl-BTW prijs als verkoopprijs (i.p.v. kost × marge)",
                    value=False, key="vbd_use_incl",
                    help="Aanvinken als jullie dezelfde verkoopprijs willen aanhouden als VBD.")
                include_image = st.checkbox(
                    "📷 Productfoto van VBD mee importeren",
                    value=True, key="vbd_include_img",
                    help="Download foto en zet als hoofdafbeelding van het product.")
                act_col1, act_col2 = st.columns(2)
                with act_col1:
                    if st.button(f"➕ Voeg {len(sel)} toe in Odoo", key="vbd_add_miss", type="primary"):
                        added = errs = 0
                        for _, r in sel.iterrows():
                            try:
                                cost = float(r["price_excl"])
                                if use_vbd_sale and r.get("price_incl"):
                                    sale = float(r["price_incl"])
                                else:
                                    sale = round(cost * margin, 2)
                                vals = {
                                    "name": str(r["name"]).strip(),
                                    "default_code": str(r["sku"]).strip(),
                                    "type": "consu",
                                    "is_storable": True,
                                    "standard_price": cost,
                                    "list_price": sale,
                                    "description_sale": str(r.get("description") or "")[:1000],
                                }
                                if include_image and r.get("image_url"):
                                    try:
                                        img_r = requests.get(str(r["image_url"]), timeout=15,
                                                              headers={"User-Agent": "Mozilla/5.0"})
                                        if img_r.status_code == 200 and img_r.content:
                                            vals["image_1920"] = base64.b64encode(img_r.content).decode("ascii")
                                    except Exception as ie:
                                        st.warning(f"  {r['sku']}: foto download faalde ({ie})")
                                tid = odoo.create("product.template", vals)
                                odoo.create("product.supplierinfo", {
                                    "partner_id": VBD_PARTNER_ID,
                                    "product_tmpl_id": tid,
                                    "product_code": str(r["sku"]).strip(),
                                    "price": cost, "min_qty": 1, "delay": 3,
                                })
                                added += 1
                            except Exception as e:
                                errs += 1
                                st.error(f"  {r['sku']}: {e}")
                        st.success(f"✓ {added} toegevoegd · {errs} fout")
                        st.session_state.pop("_vbd_result", None)
                with act_col2:
                    skip_reason = st.text_input("Reden voor skip", value="niet nodig",
                                                 key="vbd_skip_reason")
                    if st.button(f"🚫 Voeg {len(sel)} toe aan skip-list", key="vbd_skip_miss"):
                        added = 0
                        for _, r in sel.iterrows():
                            desc = str(r.get("name") or "").strip()
                            if add_to_skip_list(str(r["sku"]), skip_reason, desc):
                                added += 1
                        if GH_TOKEN and added:
                            ok, info = gh_push(f"VBD skip-list: +{added} SKU's via Streamlit ({skip_reason})")
                            if ok: st.success(f"✓ {added} toegevoegd + GitHub push ({info})")
                            else: st.error(f"{added} lokaal toegevoegd, push faalde: {info}")
                        else:
                            st.success(f"✓ {added} toegevoegd aan skip-list"
                                        + ("" if GH_TOKEN else " (niet persistent)"))
                        st.rerun()

    # ----- TAB 2: COST DIFFS -----
    with tabs[1]:
        if cost_df.empty:
            st.success("Geen kostprijs verschillen 🎉")
        else:
            st.caption("Vink aan en pas toe om supplierinfo.price (kostprijs excl BTW) bij te werken.")
            df_show = cost_df.copy()
            df_show["Δ"] = df_show["new_pricenett"] - df_show["current_supplier_price"]
            df_show.insert(0, "Selecteer", True)
            edited = st.data_editor(
                df_show, hide_index=True, use_container_width=True,
                disabled=[c for c in df_show.columns if c != "Selecteer"],
                column_config={
                    "current_supplier_price": st.column_config.NumberColumn("Huidig", format="€ %.2f"),
                    "new_pricenett": st.column_config.NumberColumn("VBD excl", format="€ %.2f"),
                    "Δ": st.column_config.NumberColumn("Δ", format="€ %.2f"),
                    "supplierinfo_id": None, "template_id": None,
                },
                key="vbd_cost_editor",
            )
            sel = edited[edited["Selecteer"]]
            if not sel.empty and st.button(f"✓ Pas {len(sel)} kostprijs updates toe",
                                            type="primary", key="vbd_apply_cost"):
                ok = err = 0
                for _, r in sel.iterrows():
                    try:
                        odoo.write("product.supplierinfo", [int(r["supplierinfo_id"])],
                                    {"price": float(r["new_pricenett"])})
                        ok += 1
                    except Exception as e:
                        err += 1
                        st.error(f"{r['sku']}: {e}")
                st.success(f"✓ {ok} updates toegepast, {err} fout")

    # ----- TAB 3: SALE DIFFS -----
    with tabs[2]:
        if sale_df.empty:
            st.success("Geen verkoopprijs verschillen 🎉")
        else:
            st.caption("Pas marge aan of gebruik VBD incl-BTW als referentie.")
            global_margin = st.slider("Globale marge (× kostprijs)", 1.0, 3.0, DEFAULT_MARGIN, 0.05,
                                       key="vbd_sale_margin")
            df_show = sale_df.copy()
            tmpl_ids = df_show["template_id"].astype(int).tolist()
            tmpls = odoo.read("product.template", tmpl_ids,
                                ["standard_price"]) if tmpl_ids else []
            cost_by_tmpl = {t["id"]: t["standard_price"] for t in tmpls}
            df_show["kostprijs"] = df_show["template_id"].astype(int).map(cost_by_tmpl)
            df_show["voorgesteld"] = (df_show["kostprijs"] * global_margin).round(2)
            df_show["Δ huidige"] = df_show["vbd_incl_btw"] - df_show["current_list_price"]
            df_show["Toepassen"] = "Voorgesteld"
            df_show.insert(0, "Selecteer", True)
            edited = st.data_editor(
                df_show, hide_index=True, use_container_width=True,
                disabled=[c for c in df_show.columns if c not in ("Selecteer", "Toepassen")],
                column_config={
                    "current_list_price": st.column_config.NumberColumn("Huidig", format="€ %.2f"),
                    "vbd_incl_btw": st.column_config.NumberColumn("VBD incl", format="€ %.2f"),
                    "kostprijs": st.column_config.NumberColumn("Kost", format="€ %.2f"),
                    "voorgesteld": st.column_config.NumberColumn("Kost×marge", format="€ %.2f"),
                    "Δ huidige": st.column_config.NumberColumn("Δ", format="€ %.2f"),
                    "Toepassen": st.column_config.SelectboxColumn(
                        options=["Voorgesteld", "VBD incl", "Skip"]),
                    "template_id": None,
                },
                key="vbd_sale_editor",
            )
            sel = edited[edited["Selecteer"]]
            if not sel.empty and st.button(f"✓ Pas {len(sel)} verkoopprijs updates toe",
                                            type="primary", key="vbd_apply_sale"):
                ok = err = 0
                for _, r in sel.iterrows():
                    if r["Toepassen"] == "Skip": continue
                    new_price = float(r["voorgesteld"]) if r["Toepassen"] == "Voorgesteld" else float(r["vbd_incl_btw"])
                    try:
                        odoo.write("product.template", [int(r["template_id"])],
                                    {"list_price": new_price})
                        ok += 1
                    except Exception as e:
                        err += 1
                        st.error(f"{r['sku']}: {e}")
                st.success(f"✓ {ok} updates toegepast, {err} fout")

    # ----- TAB 4: MATCHES -----
    with tabs[3]:
        if match_df.empty:
            st.info("Geen matches — eerst scrape draaien.")
        else:
            st.caption("Producten die zowel op VBD als in Odoo bestaan (gematcht op SKU = supplierinfo.product_code).")
            c1, c2, c3 = st.columns(3)
            with c1:
                m_search = st.text_input("🔍 Filter op SKU/naam", key="vbd_match_search")
            with c2:
                show_filter = st.selectbox(
                    "Toon", ["Alles", "Alleen kostprijs ≠", "Alleen verkoopprijs ≠",
                             "Beide identiek (kost+verkoop)"], key="vbd_match_filter")
            with c3:
                show_imgs = st.checkbox("📷 Foto's tonen", value=True, key="vbd_match_imgs")

            df = match_df.copy()
            if m_search:
                mask = df.apply(
                    lambda r: m_search.lower() in " ".join(str(v) for v in r.values).lower(),
                    axis=1)
                df = df[mask]
            if show_filter == "Alleen kostprijs ≠":
                df = df[df["Δ_kost"].abs() > 0.01]
            elif show_filter == "Alleen verkoopprijs ≠":
                df = df[df["Δ_verkoop"].abs() > 0.01]
            elif show_filter == "Beide identiek (kost+verkoop)":
                df = df[(df["Δ_kost"].abs() <= 0.01) & (df["Δ_verkoop"].abs() <= 0.01)]

            st.caption(f"**{len(df)} / {len(match_df)} weergegeven**")

            col_cfg = {
                "sku": st.column_config.TextColumn("SKU", width="small"),
                "name": st.column_config.TextColumn("VBD naam"),
                "odoo_name": st.column_config.TextColumn("Odoo naam"),
                "vbd_excl": st.column_config.NumberColumn("VBD excl", format="€ %.2f"),
                "vbd_incl": st.column_config.NumberColumn("VBD incl", format="€ %.2f"),
                "odoo_supplier_price": st.column_config.NumberColumn("Odoo kost (sup)", format="€ %.2f"),
                "odoo_standard_price": st.column_config.NumberColumn("Odoo standard", format="€ %.2f"),
                "odoo_list_price": st.column_config.NumberColumn("Odoo verkoop", format="€ %.2f"),
                "Δ_kost": st.column_config.NumberColumn("Δ kost", format="€ %.2f"),
                "Δ_verkoop": st.column_config.NumberColumn("Δ verkoop", format="€ %.2f"),
                "url": st.column_config.LinkColumn("VBD"),
                "template_id": None,
                "supplierinfo_id": None,
            }
            if show_imgs:
                col_cfg["image_url"] = st.column_config.ImageColumn("Foto", width="small")
            else:
                col_cfg["image_url"] = None

            st.dataframe(df, use_container_width=True, hide_index=True,
                          column_config=col_cfg)

            odoo_url = os.environ.get("ODOO_URL", "https://compactliving.odoo.com").rstrip("/")
            with st.expander("🔗 Direct naar Odoo product"):
                for _, r in df.head(50).iterrows():
                    if r["template_id"]:
                        link = f"{odoo_url}/odoo/inventory/products/{int(r['template_id'])}"
                        st.markdown(f"- [{r['sku']} — {r['odoo_name'] or r['name']}]({link})")
