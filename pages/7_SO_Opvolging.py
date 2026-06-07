"""SO Opvolging — dashboard open sales orders met eigen-voorraad + Reimo-levertijd.

Overzicht:
  - Tabel van open SO's; direct zichtbaar of alle producten op voorraad zijn.
Detail (SO openklikken):
  - Per productlijn: op voorraad bij ons (ja/nee).
  - Indien niet op voorraad: de gekoppelde inkooporder (klikbare link)
    + de verwachte levertijd opgevraagd bij Reimo.
"""
import os, sys, time
from pathlib import Path
import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from odoo_client import OdooClient
try:
    from reimo_scraper import Profiweb
except ImportError:
    Profiweb = None

st.set_page_config(page_title="SO Opvolging", page_icon="📊", layout="wide")

from auth import require_auth
require_auth()

REIMO_PARTNER_ID = 66
ODOO_URL = os.environ.get("ODOO_URL", "https://compactliving.odoo.com").rstrip("/")

st.title("📊 Sales Order opvolging")
st.caption("Open orders · op voorraad bij ons · gekoppelde PO · verwachte levertijd bij Reimo")


def get_odoo():
    return OdooClient(
        url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
        login=os.environ["ODOO_LOGIN"], api_key=os.environ.get("ODOO_API_KEY", ""),
    )


# ============================================================================
# Data laden
# ============================================================================
def load_sale_orders(odoo, limit, only_open):
    domain = [("state", "in", ["sale", "done"])]
    base = ["id", "name", "partner_id", "date_order", "amount_total", "state"]
    if only_open:
        domain.append(("delivery_status", "!=", "full"))
    for fields in (base + ["delivery_status"], base):
        try:
            return odoo.search_read("sale.order", domain, fields, limit, "date_order desc")
        except Exception:
            continue
    return []


STATE_PRIO = {"purchase": 3, "done": 3, "sent": 2, "draft": 1, "cancel": 0}

PO_STATE_BADGE = {
    "draft": "📝 concept (RFQ)",
    "sent": "📨 RFQ verstuurd",
    "purchase": "✅ besteld",
    "done": "✅ besteld (vergrendeld)",
    "cancel": "❌ geannuleerd",
}


def build_product_po_map(odoo, product_ids):
    """product_id -> (po_id, po_name, po_state) — beste open PO per product.

    Onze PO's worden handmatig aangemaakt (geen SO-link via origin/group), dus
    matchen we op product: staat het product op een openstaande inkooporder?
    Bij meerdere PO's wint een bevestigde PO van een concept/RFQ.
    """
    if not product_ids:
        return {}
    pol = odoo.search_read(
        "purchase.order.line",
        [("product_id", "in", list(product_ids)),
         ("state", "in", ["draft", "sent", "purchase", "done"])],
        ["product_id", "order_id", "state"], 3000,
    )
    best = {}
    for l in pol:
        if not l.get("product_id") or not l.get("order_id"):
            continue
        pid = l["product_id"][0]
        po_id, po_name = l["order_id"][0], l["order_id"][1]
        stt = l.get("state")
        cand = (STATE_PRIO.get(stt, 0), po_id)
        cur = best.get(pid)
        if cur is None or cand > (STATE_PRIO.get(cur[2], 0), cur[0]):
            best[pid] = (po_id, po_name, stt)
    return best


def build_reimo_code_map(odoo, product_ids):
    """variant/template -> Reimo artikelcode."""
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
# Connect + filters
# ============================================================================
try:
    odoo = get_odoo()
except Exception as e:
    st.error(f"Odoo connectie faalt: {e}")
    st.stop()

c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    limit = st.number_input("Aantal orders", min_value=10, max_value=300, value=50, step=10)
with c2:
    only_open = st.checkbox("Enkel niet-volledig geleverd", value=True)
with c3:
    search = st.text_input("Zoek (SO-nr of klant)", value="")

with st.spinner("Sales orders laden..."):
    sos = load_sale_orders(odoo, int(limit), only_open)

if search:
    s = search.lower()
    sos = [so for so in sos if s in (so["name"] or "").lower()
           or s in ((so["partner_id"][1] if so.get("partner_id") else "")).lower()]

if not sos:
    st.info("Geen open sales orders gevonden voor deze filter.")
    st.stop()

so_ids = [so["id"] for so in sos]

# ---- SO-lijnen in batch ----
sol = odoo.search_read(
    "sale.order.line",
    [("order_id", "in", so_ids), ("display_type", "=", False)],
    ["id", "order_id", "product_id", "product_uom_qty", "qty_delivered"], 2000,
)
lines_by_so = {}
prod_ids = set()
for l in sol:
    if not l.get("product_id"):
        continue
    oid = l["order_id"][0]
    lines_by_so.setdefault(oid, []).append(l)
    prod_ids.add(l["product_id"][0])

# ---- Voorraad + type per product ----
stock = {}
service_ids = set()
if prod_ids:
    prs = odoo.read("product.product", list(prod_ids),
                    ["free_qty", "qty_available", "incoming_qty", "type"])
    for p in prs:
        stock[p["id"]] = p
        if p.get("type") in ("service", "combo"):
            service_ids.add(p["id"])

# Diensten + combi-producten uit de orderlijnen filteren: enkel fysieke producten tonen
for oid in list(lines_by_so.keys()):
    lines_by_so[oid] = [l for l in lines_by_so[oid] if l["product_id"][0] not in service_ids]

# ---- PO-koppeling op productniveau (handmatige PO's, geen SO-link) ----
prod_po = build_product_po_map(odoo, prod_ids)   # product_id -> (po_id, po_name, po_state)


# ============================================================================
# Per-SO voorraadstatus berekenen
# ============================================================================
def so_stock_summary(so):
    """-> (badge, n_missing, n_total)."""
    lines = lines_by_so.get(so["id"], [])
    n_total = 0
    n_missing = 0
    for l in lines:
        pid = l["product_id"][0]
        needed = float(l.get("product_uom_qty") or 0) - float(l.get("qty_delivered") or 0)
        if needed <= 1e-6:
            continue   # al geleverd
        n_total += 1
        onhand = float(stock.get(pid, {}).get("qty_available") or 0)
        if onhand < needed - 1e-6:
            n_missing += 1
    if n_total == 0:
        return "✅ Alles geleverd", 0, 0
    if n_missing == 0:
        return "✅ Alles op voorraad", 0, n_total
    return f"⚠️ {n_missing}/{n_total} niet op voorraad", n_missing, n_total


# ============================================================================
# Overzichtstabel
# ============================================================================
st.markdown(f"### {len(sos)} open sales order(s)")

rows = []
for so in sos:
    badge, n_missing, n_total = so_stock_summary(so)
    rows.append({
        "SO": so["name"],
        "Klant": so["partner_id"][1] if so.get("partner_id") else "—",
        "Datum": (so.get("date_order") or "")[:10],
        "Bedrag": f"€ {so['amount_total']:,.2f}",
        "Voorraad": badge,
        "Open in Odoo": f"{ODOO_URL}/odoo/sales/{so['id']}",
    })
st.dataframe(
    pd.DataFrame(rows), hide_index=True, use_container_width=True,
    column_config={"Open in Odoo": st.column_config.LinkColumn("Odoo", display_text="open ↗")},
)

st.divider()
st.markdown("### 🔎 Detail per order")
st.caption("Klik een order open. Producten die niet op voorraad zijn tonen de gekoppelde PO; "
           "klik **Check Reimo levertijd** voor de verwachte beschikbaarheid.")

reimo_lev = st.session_state.setdefault("reimo_lev", {})   # code -> info


def fmt_lev(info):
    if not info:
        return "—"
    if info.get("discontinued"):
        return "🚫 niet meer leverbaar"
    exp = info.get("expected_date")
    if exp:
        return f"🟡 verwacht {exp}"
    if info.get("raw_status") == "AVAILABLE":
        return "✅ op voorraad bij Reimo"
    if info.get("error"):
        return f"⚠️ {info['error'][:30]}"
    return info.get("verfuegbarkeit") or info.get("raw_status") or "❔ onbekend"


for so in sos:
    badge, n_missing, n_total = so_stock_summary(so)
    klant = so["partner_id"][1] if so.get("partner_id") else ""
    with st.expander(f"{so['name']} — {klant} · {badge}", expanded=False):
        lines = lines_by_so.get(so["id"], [])

        # Reimo codes voor niet-op-voorraad lijnen
        missing_pids = []
        for l in lines:
            pid = l["product_id"][0]
            needed = float(l.get("product_uom_qty") or 0) - float(l.get("qty_delivered") or 0)
            onhand = float(stock.get(pid, {}).get("qty_available") or 0)
            if needed > 1e-6 and onhand < needed - 1e-6:
                missing_pids.append(pid)
        code_map = build_reimo_code_map(odoo, missing_pids) if missing_pids else {}

        if st.button("🔍 Check Reimo levertijd", key=f"chk_{so['id']}",
                     disabled=not missing_pids):
            if Profiweb is None:
                st.error("Reimo scraper niet beschikbaar.")
            elif not (os.environ.get("PROFIWEB_USER") and os.environ.get("PROFIWEB_PASS")):
                st.error("PROFIWEB_USER / PROFIWEB_PASS ontbreken in secrets.")
            else:
                try:
                    with st.spinner("Reimo opvragen..."):
                        pw = Profiweb(os.environ["PROFIWEB_USER"],
                                      os.environ["PROFIWEB_PASS"], log=lambda *_: None)
                        pw.login()
                        codes = list(dict.fromkeys(c for c in code_map.values() if c))
                        for code in codes:
                            try:
                                reimo_lev[code] = pw.lookup(code)
                            except Exception as e:
                                reimo_lev[code] = {"error": str(e)}
                            time.sleep(0.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"Reimo check faalde: {e}")

        # Lijntabel
        detail_rows = []
        for l in lines:
            pid = l["product_id"][0]
            needed = float(l.get("product_uom_qty") or 0) - float(l.get("qty_delivered") or 0)
            onhand = float(stock.get(pid, {}).get("qty_available") or 0)
            free = float(stock.get(pid, {}).get("free_qty") or 0)
            qty = float(l.get("product_uom_qty") or 0)

            po = prod_po.get(pid)
            po_link = f"{ODOO_URL}/odoo/purchase/{po[0]}" if po else ""
            po_status = PO_STATE_BADGE.get(po[2], po[2]) if po else "— geen PO"

            if needed <= 1e-6:
                status = "✅ geleverd"
                lev = ""
            elif onhand >= needed - 1e-6:
                status = "✅ op voorraad"
                lev = ""
            else:
                status = f"❌ tekort ({onhand:.0f}/{needed:.0f})"
                code = code_map.get(pid)
                lev = fmt_lev(reimo_lev.get(code)) if code else "geen Reimo-code"
            detail_rows.append({
                "Product": (l["product_id"][1] if l.get("product_id") else "")[:55],
                "Besteld": qty,
                "Op voorraad": onhand,
                "Vrij": free,
                "Status": status,
                "PO": po_link,
                "PO status": po_status,
                "Reimo levertijd": lev,
            })
        st.dataframe(
            pd.DataFrame(detail_rows), hide_index=True, use_container_width=True,
            column_config={"PO": st.column_config.LinkColumn("PO", display_text="open ↗")},
        )
