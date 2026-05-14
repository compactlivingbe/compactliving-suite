"""Reimo Profiweb beschikbaarheid - manuele scrape vanuit Streamlit."""
import os, sys, time
from pathlib import Path
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from reimo_scraper import Profiweb, Odoo, decide, aggregate, DEFAULT_RULES

st.set_page_config(page_title="Reimo Sync", page_icon="📦", layout="wide")

if not st.session_state.get("pw_ok"):
    st.warning("Login eerst via Home.")
    st.stop()

st.title("📦 Reimo Profiweb sync")
st.caption("Scrape Reimo beschikbaarheid → schrijft `sale_line_warn_msg` per template in Odoo.")


def load_cfg():
    return {
        "odoo_url": os.environ.get("ODOO_URL", "https://compactliving.odoo.com"),
        "odoo_db": os.environ.get("ODOO_DB", "compactliving"),
        "odoo_login": os.environ.get("ODOO_LOGIN", ""),
        "odoo_password": os.environ.get("ODOO_PASSWORD", ""),
        "pw_user": os.environ.get("PROFIWEB_USER", ""),
        "pw_pass": os.environ.get("PROFIWEB_PASS", ""),
    }


cfg = load_cfg()
missing = [k for k, v in [("ODOO_LOGIN", cfg["odoo_login"]),
                          ("ODOO_PASSWORD", cfg["odoo_password"]),
                          ("PROFIWEB_USER", cfg["pw_user"]),
                          ("PROFIWEB_PASS", cfg["pw_pass"])] if not v]
if missing:
    st.error(f"Ontbrekende secrets: {', '.join(missing)} - voeg toe in Streamlit Cloud Settings.")
    st.stop()


tab_run, tab_test, tab_history = st.tabs(["▶ Scrape draaien", "🔍 Test 1 artikel", "📜 Geschiedenis"])

with tab_run:
    col1, col2, col3 = st.columns(3)
    with col1:
        max_articles = st.number_input("Max artikelen (0 = alle)", min_value=0, value=0)
    with col2:
        delay = st.number_input("Delay tussen artikelen (sec)", min_value=0.0, value=0.7, step=0.1)
    with col3:
        include_archived = st.checkbox("Inclusief gearchiveerde", value=False)

    if st.button("▶ Start scrape", type="primary", use_container_width=True):
        progress = st.progress(0, text="Verbinden...")
        log_box = st.empty()
        results_box = st.empty()
        log_lines = []

        def log(msg):
            log_lines.append(msg)
            log_box.code("\n".join(log_lines[-30:]), language=None)

        try:
            o = Odoo(cfg["odoo_url"], cfg["odoo_db"], cfg["odoo_login"], cfg["odoo_password"], log=log)
            o.authenticate()
            codes = o.find_codes([66], [], include_archived=include_archived, only_with_code=True)
            if max_articles:
                codes = codes[:max_articles]
            log(f"Te scrapen: {len(codes)} codes")

            pw = Profiweb(cfg["pw_user"], cfg["pw_pass"], log=log)
            pw.login()

            counts = {"ok": 0, "warn": 0, "block": 0, "err": 0}
            by_tmpl = {}
            results_data = []

            for i, c in enumerate(codes, 1):
                try:
                    info = pw.lookup(c["code"])
                    action, msg = decide(DEFAULT_RULES, info)
                    free_qty = float(c.get("free_qty") or 0)
                    incoming_qty = float(c.get("incoming_qty") or 0)
                    info["_free_qty"] = free_qty
                    info["_incoming_qty"] = incoming_qty
                    if free_qty > 0 and action != "no-message":
                        action = "no-message"; msg = ""
                    elif incoming_qty > 0 and action == "block":
                        action = "warning"
                        msg = f"{msg} (✓ {int(incoming_qty)} besteld - PO loopt)"

                    by_tmpl.setdefault(c["tmpl_id"], {"name": c["tmpl_name"], "results": []})\
                        ["results"].append((c["code"], c["variant_label"], info, action, msg))
                    if action == "block": counts["block"] += 1
                    elif action == "warning": counts["warn"] += 1
                    else: counts["ok"] += 1

                    results_data.append({
                        "Status": {"block":"🚫","warning":"⚠","no-message":"✓"}[action],
                        "Code": c["code"], "Template": c["tmpl_name"][:40],
                        "Variant": c.get("variant_label", "")[:30],
                        "Voorraad": int(free_qty), "PO": int(incoming_qty),
                        "Detail": (info.get("verfuegbarkeit") or info.get("expected_date") or info.get("raw_status",""))[:60],
                    })
                except Exception as e:
                    counts["err"] += 1
                    log(f"FOUT {c['code']}: {e}")
                pct = int(i / len(codes) * 100)
                progress.progress(pct, text=f"{i} / {len(codes)} - ✓{counts['ok']} ⚠{counts['warn']} 🚫{counts['block']}")
                # Update table every 10 items
                if i % 10 == 0 or i == len(codes):
                    results_box.dataframe(results_data[-50:], use_container_width=True, hide_index=True)
                time.sleep(delay)

            # Aggregate to Odoo
            log(f"Aggregeren naar {len(by_tmpl)} templates...")
            wrote = 0
            for tid, data in by_tmpl.items():
                a, m = aggregate(data["results"])
                try:
                    o.write_warning(tid, a, m)
                    wrote += 1
                except Exception as e:
                    log(f"  Tmpl {tid} FOUT: {e}")
            log(f"KLAAR. {wrote} templates geschreven.")
            st.success(f"✓ Scrape voltooid: {counts['ok']} OK · {counts['warn']} warning · {counts['block']} block · {wrote} templates bijgewerkt")
        except Exception as e:
            st.error(f"FOUT: {e}")
            log(f"FOUT: {e}")


with tab_test:
    code = st.text_input("Artikelnummer", value="31650")
    if st.button("Test"):
        try:
            pw = Profiweb(cfg["pw_user"], cfg["pw_pass"])
            pw.login()
            info = pw.lookup(code)
            action, msg = decide(DEFAULT_RULES, info)
            st.json({**info, "_action": action, "_msg": msg})
        except Exception as e:
            st.error(str(e))


with tab_history:
    st.info("Run-historiek wordt bijgewerkt door GitHub Actions. "
            "Bekijk in repo → Actions tab of via dashboard.")
