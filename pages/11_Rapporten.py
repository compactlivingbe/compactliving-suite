"""
Rapporten — Reimo.

Twee wekelijkse rapporten over leverancier Reimo:

1. Nieuwe producten — gescrapet van reimo.com/nl/nieuwigheden. Cumulatief:
   elk product met de datum waarop het voor het eerst in het rapport verscheen
   (`toegevoegd_op`). Oude blijven staan, nieuwe komen erbij. Data-bron:
   data/reimo_newcomers.json, wekelijks ververst door de GitHub Action
   (zelfde run als de Reimo Profiweb sync).

2. Niet meer verkocht — live uit Odoo: Reimo-producten waar de Profiweb-sync
   "NIET LEVERBAAR" in de verkoop-waarschuwing (sale_line_warn_msg) schreef.
"""
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import gh_storage as ghs  # noqa: E402
from odoo_client import OdooClient  # noqa: E402

try:
    st.set_page_config(page_title="Rapporten", page_icon="📈", layout="wide")
except Exception:
    pass

from auth import require_auth  # noqa: E402
require_auth()

REIMO_PARTNER_ID = 66
NEWCOMERS_FILE = "reimo_newcomers.json"
ODOO_URL = os.environ.get("ODOO_URL", "https://compactliving.odoo.com").rstrip("/")
DISCONTINUED_NEEDLE = "NIET LEVERBAAR"

_KLEUR_EMOJI = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴", "grey": "⚪"}


st.title("📈 Rapporten — Reimo")
st.caption("Wekelijkse leveranciersrapporten. Nieuwe producten worden cumulatief "
           "bijgehouden met datum van toevoeging; niet-leverbare producten komen "
           "live uit Odoo (Profiweb-sync).")

tab_nieuw, tab_uit = st.tabs(["🆕 Nieuwe producten", "🚫 Niet meer verkocht"])


# ============================================================================
# 1. NIEUWE PRODUCTEN
# ============================================================================
def _fmt_datum(s: str) -> str:
    try:
        return datetime.fromisoformat(s).strftime("%d-%m-%Y")
    except Exception:
        return s or ""


with tab_nieuw:
    store = ghs.load_json(NEWCOMERS_FILE, default={})
    producten = list((store.get("products") or {}).values())

    if not producten:
        st.info(
            "Nog geen nieuwigheden opgeslagen. Het bestand "
            f"`data/{NEWCOMERS_FILE}` wordt wekelijks door de GitHub Action "
            "aangemaakt (of gebruik hieronder **Nu verversen**)."
        )
    else:
        updated = _fmt_datum(store.get("updated", ""))
        vandaag = date.today().isoformat()
        datums = sorted({p.get("toegevoegd_op", "") for p in producten if p.get("toegevoegd_op")},
                        reverse=True)
        laatste_datum = datums[0] if datums else ""
        nieuw_laatste = sum(1 for p in producten if p.get("toegevoegd_op") == laatste_datum)

        c1, c2, c3 = st.columns(3)
        c1.metric("Producten in rapport", len(producten))
        c2.metric(f"Nieuw op {_fmt_datum(laatste_datum)}", nieuw_laatste)
        c3.metric("Laatst ververst", updated or "—")

        # ---- filters ----
        f1, f2, f3 = st.columns([2, 2, 3])
        with f1:
            keuze = ["Alle datums"] + [f"{_fmt_datum(d)}" for d in datums]
            sel = st.selectbox("Toegevoegd op", keuze, index=0)
        with f2:
            enkel_actueel = st.checkbox("Alleen nog op nieuwigheden-pagina", value=False)
        with f3:
            zoek = st.text_input("Zoek (naam / artikelnr / merk)", "")

        rows = producten
        if sel != "Alle datums":
            doel = datums[keuze.index(sel) - 1]
            rows = [p for p in rows if p.get("toegevoegd_op") == doel]
        if enkel_actueel:
            rows = [p for p in rows if p.get("op_nieuwigheden")]
        if zoek.strip():
            q = zoek.strip().lower()
            rows = [p for p in rows
                    if q in (p.get("naam", "").lower())
                    or q in (p.get("artikelnr", "").lower())
                    or q in (p.get("leverancier", "").lower())]

        # nieuwste eerst, dan op naam
        rows = sorted(rows, key=lambda p: (p.get("toegevoegd_op", ""), p.get("artikelnr", "")),
                      reverse=True)

        st.caption(f"{len(rows)} product(en) getoond.")

        df = pd.DataFrame([{
            "Foto": p.get("afbeelding", ""),
            "Artikelnr": p.get("artikelnr", ""),
            "Naam": p.get("naam", ""),
            "Merk": p.get("leverancier", ""),
            "Prijs": p.get("prijs", ""),
            "Leverbaarheid": f"{_KLEUR_EMOJI.get(p.get('voorraad_kleur',''),'')} "
                             f"{p.get('leverbaarheid','')}".strip(),
            "Toegevoegd": _fmt_datum(p.get("toegevoegd_op", "")),
            "Laatst gezien": _fmt_datum(p.get("laatst_gezien", "")),
            "Link": p.get("url", ""),
        } for p in rows])

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            height=min(680, 80 + 38 * max(1, len(rows))),
            column_config={
                "Foto": st.column_config.ImageColumn("Foto", width="small"),
                "Naam": st.column_config.TextColumn("Naam", width="large"),
                "Link": st.column_config.LinkColumn("Link", display_text="Bekijk ↗"),
            },
        )

        st.download_button(
            "⬇️ Download als CSV",
            df.drop(columns=["Foto"]).to_csv(index=False).encode("utf-8"),
            file_name=f"reimo_nieuwe_producten_{vandaag}.csv",
            mime="text/csv",
        )

    st.divider()
    with st.expander("🔄 Nu verversen (handmatig scrapen)"):
        st.caption("Draait de nieuwigheden-scraper live en voegt nieuwe producten "
                   "toe aan het cumulatieve rapport. Kan ~1 min duren.")
        max_p = st.number_input("Max pagina's", 1, 200, 130, step=10)
        if st.button("Start scrape", type="primary"):
            import reimo_newcomers as rn
            with st.spinner("Reimo nieuwigheden scrapen..."):
                scraped = rn.fetch_newcomers(max_pages=int(max_p), delay=0.4,
                                             log=lambda m: None)
                cur = ghs.load_json(NEWCOMERS_FILE, default={})
                cur = rn.merge_store(cur, scraped, date.today().isoformat())
                pushed, info = ghs.save_json(
                    NEWCOMERS_FILE, cur, f"Rapport: Reimo nieuwigheden {date.today().isoformat()}")
            st.success(f"{len(scraped)} producten gescrapet. "
                       + ("Opgeslagen naar GitHub." if pushed else f"Lokaal opgeslagen ({info})."))
            st.rerun()


# ============================================================================
# 2. NIET MEER VERKOCHT (live Odoo)
# ============================================================================
@st.cache_data(ttl=900, show_spinner=False)
def _odoo_discontinued():
    o = OdooClient(url=os.environ["ODOO_URL"], db=os.environ["ODOO_DB"],
                   login=os.environ["ODOO_LOGIN"],
                   api_key=os.environ.get("ODOO_API_KEY", ""))
    # Reimo-leveranciercodes per template
    sis = o.search_read(
        "product.supplierinfo", [("partner_id", "=", REIMO_PARTNER_ID)],
        ["product_tmpl_id", "product_code"], limit=100000)
    code_by_tmpl = {}
    for s in sis:
        tid = s["product_tmpl_id"][0] if s.get("product_tmpl_id") else None
        if tid and s.get("product_code") and tid not in code_by_tmpl:
            code_by_tmpl[tid] = s["product_code"]
    tmpl_ids = list(code_by_tmpl.keys())
    if not tmpl_ids:
        return []
    tmpls = o.search_read(
        "product.template",
        [("id", "in", tmpl_ids), ("sale_line_warn_msg", "ilike", DISCONTINUED_NEEDLE)],
        ["name", "default_code", "list_price", "sale_line_warn_msg"],
        limit=100000, order="name")
    out = []
    for t in tmpls:
        msg = (t.get("sale_line_warn_msg") or "").replace("🚫", "").strip()
        # eerste regel = korte reden
        reden = msg.split("\n", 1)[0].strip() if msg else ""
        out.append({
            "tmpl_id": t["id"],
            "naam": t.get("name", ""),
            "reimo_code": code_by_tmpl.get(t["id"], ""),
            "odoo_code": t.get("default_code") or "",
            "prijs": t.get("list_price") or 0.0,
            "reden": reden,
            "melding": msg,
            "odoo_url": f"{ODOO_URL}/odoo/inventory/products/{t['id']}",
        })
    return out


with tab_uit:
    st.caption("Reimo-producten die volgens de laatste Profiweb-sync **niet meer "
               "leverbaar** zijn (Auslauf). Bron: Odoo `sale_line_warn_msg`. "
               "Ververst elke 15 min.")

    colb, _ = st.columns([1, 4])
    if colb.button("🔄 Vernieuw uit Odoo"):
        _odoo_discontinued.clear()

    try:
        data = _odoo_discontinued()
    except KeyError as e:
        st.error(f"Odoo-configuratie ontbreekt (secret {e}). Stel de ODOO_*-secrets in.")
        data = None
    except Exception as e:  # noqa: BLE001
        st.error(f"Odoo-query mislukt: {e}")
        data = None

    if data is not None:
        st.metric("Niet meer verkochte Reimo-producten", len(data))
        if not data:
            st.success("Geen enkel Reimo-product staat momenteel op 'niet leverbaar'. 🎉")
        else:
            zoek2 = st.text_input("Zoek (naam / code)", "", key="zoek_uit")
            rows = data
            if zoek2.strip():
                q = zoek2.strip().lower()
                rows = [d for d in rows
                        if q in d["naam"].lower()
                        or q in str(d["reimo_code"]).lower()
                        or q in str(d["odoo_code"]).lower()]
            df2 = pd.DataFrame([{
                "Reimo-code": d["reimo_code"],
                "Odoo-code": d["odoo_code"],
                "Naam": d["naam"],
                "Verkoopprijs": f"€ {d['prijs']:.2f}".replace(".", ","),
                "Reden": d["reden"],
                "Odoo": d["odoo_url"],
            } for d in rows])
            st.dataframe(
                df2, use_container_width=True, hide_index=True,
                height=min(680, 80 + 38 * max(1, len(rows))),
                column_config={
                    "Naam": st.column_config.TextColumn("Naam", width="large"),
                    "Odoo": st.column_config.LinkColumn("Odoo", display_text="Open ↗"),
                },
            )
            st.download_button(
                "⬇️ Download als CSV",
                df2.to_csv(index=False).encode("utf-8"),
                file_name=f"reimo_niet_meer_verkocht_{date.today().isoformat()}.csv",
                mime="text/csv",
            )
