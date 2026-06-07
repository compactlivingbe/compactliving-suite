"""SO Opvolging — dashboard sales orders met leverancier-beschikbaarheid + ontvangststatus.

Per sales order:
  1. Is alles beschikbaar bij de leverancier (Reimo)?  -> live Profiweb lookup
  2. Is alles toegekomen bij ons?                       -> qty_received op gekoppelde PO('s)
  3. Wat is de verwachte leverdatum bij Reimo?          -> Profiweb expected_date
"""
import os, sys, time
from pathlib import Path
import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient
try:
    from reimo_scraper import Profiweb, decide, DEFAULT_RULES
except ImportError:
    Profiweb = None

st.set_page_config(page_title="SO Opvolging", page_icon="📊", layout="wide")

from auth import require_auth
require_auth()

REIMO_PARTNER_ID = 66

st.title("📊 Sales Order opvolging")
st.caption("Per order: beschikbaar bij leverancier (Reimo) · ontvangen bij ons · verwachte leverdatum")


def get_odoo():
    return OdooClient(
        url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
        login=os.environ["ODOO_LOGIN"], api_key=os.environ.get("ODOO_API_KEY", ""),
    )


# ============================================================================
# Helpers
# ============================================================================
def load_sale_orders(odoo, limit, only_open):
    domain = [("state", "in", ["sale", "done"])]
    if only_open:
        # delivery_status bestaat in Odoo 17+: 'full' = volledig geleverd aan klant
        domain.append(("delivery_status", "!=", "full"))
    fields = ["id", "name", "partner_id", "date_order", "amount_total", "state"]
    # procurement_group_id + delivery_status defensief toevoegen
    try:
        return odoo.search_read("sale.order", domain, fields + ["procurement_group_id", "delivery_status"],
                                limit, "date_order desc")
    except Exception:
        return odoo.search_read("sale.order", domain, fields, limit, "date_order desc")


def load_all_pos(odoo, since_days=180):
    """Alle recente PO's met hun origin/group voor SO-matching."""
    fields = ["id", "name", "origin", "partner_id", "state", "amount_total", "order_line"]
    try:
        fields.append("group_id")
    except Exception:
        pass
    pos = odoo.search_read(
        "purchase.order",
        [("state", "in", ["draft", "sent", "purchase", "done"])],
        fields, 500, "date_order desc",
    )
    return pos


def match_pos_to_so(so, all_pos):
    """Koppel PO's aan een SO via origin (SO-naam) of gedeelde procurement group."""
    so_name = so["name"]
    gid = so.get("procurement_group_id")
    gid = gid[0] if isinstance(gid, (list, tuple)) and gid else None
    matched = []
    for po in all_pos:
        origin = po.get("origin") or ""
        po_gid = po.get("group_id")
        po_gid = po_gid[0] if isinstance(po_gid, (list, tuple)) and po_gid else None
        if so_name and so_name in origin:
            matched.append(po)
        elif gid and po_gid and gid == po_gid:
            matched.append(po)
    return matched


def receipt_status(odoo, po_line_ids):
    """Aggregeer qty_received vs product_qty over PO-lijnen -> (status, recv, qty)."""
    if not po_line_ids:
        return "geen PO", 0.0, 0.0
    lines = odoo.read("purchase.order.line", po_line_ids,
                      ["product_qty", "qty_received"])
    tot_qty = sum(float(l.get("product_qty") or 0) for l in lines)
    tot_recv = sum(float(l.get("qty_received") or 0) for l in lines)
    if tot_qty <= 0:
        return "—", tot_recv, tot_qty
    if tot_recv >= tot_qty - 1e-6:
        return "volledig", tot_recv, tot_qty
    if tot_recv > 0:
        return "deels", tot_recv, tot_qty
    return "niets", tot_recv, tot_qty


RECEIPT_BADGE = {
    "volledig": "✅ Volledig ontvangen",
    "deels": "🟡 Deels ontvangen",
    "niets": "⏳ Nog niets ontvangen",
    "geen PO": "— geen PO",
    "—": "—",
}


# ============================================================================
# Data laden
# ============================================================================
try:
    odoo = get_odoo()
except Exception as e:
    st.error(f"Odoo connectie faalt: {e}")
    st.stop()

ctrl1, ctrl2, ctrl3 = st.columns([1, 1, 2])
with ctrl1:
    limit = st.number_input("Aantal orders", min_value=10, max_value=300, value=50, step=10)
with ctrl2:
    only_open = st.checkbox("Enkel niet-volledig geleverd", value=True)
with ctrl3:
    search = st.text_input("Zoek (SO-nr of klant)", value="")

with st.spinner("Sales orders laden..."):
    try:
        sos = load_sale_orders(odoo, int(limit), only_open)
    except Exception as e:
        st.error(f"Kon sales orders niet laden: {e}")
        st.stop()
    all_pos = load_all_pos(odoo)

if search:
    s = search.lower()
    sos = [so for so in sos
           if s in (so["name"] or "").lower()
           or s in ((so["partner_id"][1] if so.get("partner_id") else "")).lower()]

if not sos:
    st.info("Geen sales orders gevonden voor deze filter.")
    st.stop()

# PO's per SO koppelen + ontvangststatus (goedkoop, geen scraping)
po_by_id = {p["id"]: p for p in all_pos}
so_pos = {}          # so_id -> [po,...]
so_receipt = {}      # so_id -> (status, recv, qty)
for so in sos:
    matched = match_pos_to_so(so, all_pos)
    so_pos[so["id"]] = matched
    line_ids = [lid for po in matched for lid in (po.get("order_line") or [])]
    so_receipt[so["id"]] = receipt_status(odoo, line_ids) if line_ids else ("geen PO", 0, 0)

# ============================================================================
# Overzichtstabel
# ============================================================================
st.markdown(f"### {len(sos)} sales order(s)")

rows = []
for so in sos:
    matched = so_pos[so["id"]]
    reimo_pos = [p for p in matched if p.get("partner_id") and p["partner_id"][0] == REIMO_PARTNER_ID]
    rstat, recv, qty = so_receipt[so["id"]]
    av = st.session_state.get("so_avail", {}).get(so["id"])
    rows.append({
        "Selecteer": False,
        "SO": so["name"],
        "Klant": so["partner_id"][1] if so.get("partner_id") else "—",
        "Datum": (so.get("date_order") or "")[:10],
        "Bedrag": f"€ {so['amount_total']:,.2f}",
        "Ontvangst": RECEIPT_BADGE.get(rstat, rstat) + (f" ({recv:.0f}/{qty:.0f})" if qty else ""),
        "Reimo PO": ", ".join(p["name"] for p in reimo_pos) or "—",
        "Reimo beschikbaar": av or "— (klik check)",
        "_id": so["id"],
    })

df = pd.DataFrame(rows)
edited = st.data_editor(
    df, hide_index=True, use_container_width=True,
    column_config={"_id": None},
    disabled=[c for c in df.columns if c not in ("Selecteer",)],
    key="so_table",
)
selected_ids = edited[edited["Selecteer"]]["_id"].tolist()

st.caption("Vink orders aan en klik **Check Reimo beschikbaarheid** voor een live controle "
           "(beschikbaarheid + verwachte leverdatum per lijn).")

check_btn = st.button("🔍 Check Reimo beschikbaarheid (geselecteerd)",
                      type="primary", disabled=not selected_ids)


# ============================================================================
# Reimo code-resolutie (variant/template -> Reimo artikelcode)
# ============================================================================
def build_reimo_code_map(odoo, product_ids):
    if not product_ids:
        return {}
    prods = odoo.search_read("product.product", [("id", "in", product_ids)],
                             ["id", "product_tmpl_id", "default_code"])
    var_to_tmpl = {p["id"]: p["product_tmpl_id"][0] for p in prods if p.get("product_tmpl_id")}
    var_code = {p["id"]: p.get("default_code") for p in prods}
    tmpl_ids = list(set(var_to_tmpl.values()))
    sis_var = odoo.search_read(
        "product.supplierinfo",
        [("product_id", "in", product_ids), ("partner_id", "=", REIMO_PARTNER_ID)],
        ["product_id", "product_code"]) if product_ids else []
    sis_tmpl = odoo.search_read(
        "product.supplierinfo",
        [("product_tmpl_id", "in", tmpl_ids), ("partner_id", "=", REIMO_PARTNER_ID),
         ("product_id", "=", False)],
        ["product_tmpl_id", "product_code"]) if tmpl_ids else []
    code_map = {}
    for s in sis_var:
        if s.get("product_id") and s.get("product_code"):
            code_map[s["product_id"][0]] = s["product_code"].strip()
    tmpl_code = {s["product_tmpl_id"][0]: s["product_code"].strip()
                 for s in sis_tmpl if s.get("product_tmpl_id") and s.get("product_code")}
    for vid, tid in var_to_tmpl.items():
        if vid not in code_map and tid in tmpl_code:
            code_map[vid] = tmpl_code[tid]
    for vid, dc in var_code.items():
        if vid not in code_map and dc:
            code_map[vid] = dc.strip()
    return code_map


# ============================================================================
# Live check uitvoeren
# ============================================================================
if check_btn and selected_ids:
    if Profiweb is None:
        st.error("Reimo scraper niet beschikbaar.")
        st.stop()
    pw_user = os.environ.get("PROFIWEB_USER", "")
    pw_pass = os.environ.get("PROFIWEB_PASS", "")
    if not (pw_user and pw_pass):
        st.error("PROFIWEB_USER / PROFIWEB_PASS ontbreken in secrets.")
        st.stop()

    # Verzamel Reimo PO-lijnen voor de geselecteerde SO's
    detail = {}   # so_id -> list of line dicts
    all_var_ids = set()
    for sid in selected_ids:
        reimo_pos = [p for p in so_pos[sid]
                     if p.get("partner_id") and p["partner_id"][0] == REIMO_PARTNER_ID]
        line_ids = [lid for po in reimo_pos for lid in (po.get("order_line") or [])]
        lines = odoo.read("purchase.order.line", line_ids,
                          ["product_id", "product_qty", "qty_received", "name"]) if line_ids else []
        for l in lines:
            if l.get("product_id"):
                all_var_ids.add(l["product_id"][0])
        detail[sid] = lines

    code_map = build_reimo_code_map(odoo, list(all_var_ids))

    log_box = st.empty()
    log_lines = []
    def stlog(m):
        log_lines.append(str(m))
        log_box.code("\n".join(log_lines[-15:]), language=None)

    avail_cache = {}   # code -> info
    try:
        with st.spinner("Profiweb login..."):
            pw = Profiweb(pw_user, pw_pass, log=stlog)
            pw.login()

        avail_state = st.session_state.setdefault("so_avail", {})
        prog = st.progress(0, text="Beschikbaarheid ophalen...")
        codes_needed = []
        for sid in selected_ids:
            for l in detail[sid]:
                vid = l["product_id"][0] if l.get("product_id") else None
                code = code_map.get(vid)
                if code and code not in avail_cache:
                    codes_needed.append(code)
        codes_needed = list(dict.fromkeys(codes_needed))

        for i, code in enumerate(codes_needed, 1):
            try:
                avail_cache[code] = pw.lookup(code)
            except Exception as e:
                avail_cache[code] = {"found": False, "error": str(e), "raw_status": "ERROR",
                                     "expected_date": "", "verfuegbarkeit": "", "discontinued": False}
            prog.progress(int(i / max(len(codes_needed), 1) * 100),
                          text=f"{i}/{len(codes_needed)} codes")
            time.sleep(0.5)
        prog.empty()

        # Per SO aggregeren + tonen
        for sid in selected_ids:
            so = next(x for x in sos if x["id"] == sid)
            lines = detail[sid]
            line_rows = []
            n_avail = n_back = n_disc = n_unknown = 0
            latest_date = ""
            for l in lines:
                vid = l["product_id"][0] if l.get("product_id") else None
                code = code_map.get(vid, "?")
                info = avail_cache.get(code, {})
                status = info.get("raw_status", "UNKNOWN")
                exp = info.get("expected_date", "")
                if info.get("discontinued"):
                    n_disc += 1; badge = "🚫 Niet leverbaar"
                elif status == "AVAILABLE":
                    n_avail += 1; badge = "✅ Op voorraad"
                elif status == "BACKORDER" or exp:
                    n_back += 1; badge = "🟡 Backorder"
                else:
                    n_unknown += 1; badge = "❔ Onbekend"
                if exp and exp > latest_date:
                    latest_date = exp
                recv = float(l.get("qty_received") or 0)
                qty = float(l.get("product_qty") or 0)
                line_rows.append({
                    "Reimo code": code,
                    "Product": (l["product_id"][1] if l.get("product_id") else l.get("name", ""))[:50],
                    "Besteld": qty,
                    "Ontvangen": recv,
                    "Beschikbaar": badge,
                    "Verwacht": exp or "—",
                    "Detail": (info.get("verfuegbarkeit") or info.get("error") or "")[:45],
                })

            # Samenvatting voor overzichtstabel
            if n_disc:
                summ = f"🚫 {n_disc} niet leverbaar"
            elif n_back:
                summ = f"🟡 {n_back} backorder" + (f" → {latest_date}" if latest_date else "")
            elif n_unknown and not n_avail:
                summ = "❔ onbekend"
            else:
                summ = "✅ alles beschikbaar"
            avail_state[sid] = summ

            with st.expander(f"📋 {so['name']} — {so['partner_id'][1] if so.get('partner_id') else ''} · {summ}",
                             expanded=True):
                if not line_rows:
                    st.info("Geen Reimo PO-lijnen gevonden voor deze order.")
                else:
                    st.dataframe(pd.DataFrame(line_rows), hide_index=True,
                                 use_container_width=True)
                    if latest_date:
                        st.caption(f"⏱ Laatste verwachte leverdatum bij Reimo: **{latest_date}**")
        st.success("✓ Beschikbaarheid bijgewerkt. De samenvatting staat nu ook in de tabel hierboven.")
    except Exception as e:
        st.error(f"Check faalde: {e}")
        import traceback
        st.code(traceback.format_exc())
