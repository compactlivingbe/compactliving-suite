"""
Reads run output (CSV / log) and writes summary JSON files to docs/data/
zodat de dashboard ze kan tonen.
Wordt door GitHub Actions aangeroepen na elke succesvolle run.
"""
import argparse, json, csv, os, sys
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", choices=["reimo", "topsystems"], required=True)
    ap.add_argument("--csv", help="results CSV pad")
    ap.add_argument("--workflow-url", default="", help="GitHub run URL")
    ap.add_argument("--success", default="true")
    args = ap.parse_args()

    odoo_url = os.environ.get("ODOO_URL", "https://compactliving.odoo.com").rstrip("/")
    now = datetime.utcnow().isoformat() + "Z"

    if args.type == "reimo":
        process_reimo(args.csv, now, odoo_url, args.workflow_url, args.success == "true")
    else:
        process_topsystems(args.csv, now, odoo_url, args.workflow_url, args.success == "true")


def process_reimo(csv_path, now, odoo_url, workflow_url, success):
    counts = Counter()
    warnings_by_tmpl = defaultdict(lambda: {"action": "no-message", "details": [], "categ": ""})
    if csv_path and Path(csv_path).exists():
        with open(csv_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                action = r.get("action", "no-message")
                tag = "block" if action == "block" else ("warn" if action == "warning" else "ok")
                counts[tag] += 1
                if action in ("block", "warning"):
                    tid = r.get("tmpl_id")
                    name = r.get("tmpl_name", "")
                    code = r.get("code", "")
                    detail = r.get("expected_date") or r.get("verfuegbarkeit") or r.get("raw_status", "")
                    warnings_by_tmpl[tid]["template"] = name
                    warnings_by_tmpl[tid]["action"] = "block" if action == "block" else warnings_by_tmpl[tid]["action"]
                    if action == "block": warnings_by_tmpl[tid]["action"] = "block"
                    elif warnings_by_tmpl[tid]["action"] != "block":
                        warnings_by_tmpl[tid]["action"] = "warning"
                    warnings_by_tmpl[tid]["details"].append(f"[{code}] {detail}")
                    warnings_by_tmpl[tid]["odoo_url"] = f"{odoo_url}/odoo/inventory/products/{tid}"

    latest = {
        "timestamp": now,
        "counts": {"ok": counts.get("ok", 0), "warn": counts.get("warn", 0), "block": counts.get("block", 0)},
        "success": success,
    }
    (DATA_DIR / "reimo_latest.json").write_text(json.dumps(latest, indent=2), encoding="utf-8")

    warnings_list = []
    for tid, w in warnings_by_tmpl.items():
        warnings_list.append({
            "tmpl_id": tid,
            "template": w.get("template", ""),
            "categ": w.get("categ", ""),
            "action": w.get("action"),
            "detail": " | ".join(w.get("details", [])[:5]),
            "odoo_url": w.get("odoo_url", ""),
        })
    warnings_list.sort(key=lambda x: (0 if x["action"] == "block" else 1, x["template"]))
    (DATA_DIR / "reimo_warnings.json").write_text(json.dumps(warnings_list, indent=2), encoding="utf-8")

    append_history({
        "timestamp": now, "workflow": "Reimo Profiweb weekly",
        "success": success,
        "summary": f"{counts.get('ok',0)} OK · {counts.get('warn',0)} warning · {counts.get('block',0)} block",
        "url": workflow_url,
    })


def process_topsystems(csv_path, now, odoo_url, workflow_url, success):
    # CSV's: missing/cost_diffs/sale_diffs in reports/
    reports = REPO_ROOT / "reports"
    counts = {"missing": 0, "cost_diffs": 0, "sale_diffs": 0, "no_price": 0, "codes_total": 0}
    if reports.exists():
        for f in reports.glob("missing_*.csv"):
            counts["missing"] = sum(1 for _ in open(f, encoding="utf-8")) - 1
            break
        for f in reports.glob("cost_diffs_*.csv"):
            counts["cost_diffs"] = sum(1 for _ in open(f, encoding="utf-8")) - 1
            break
        for f in reports.glob("sale_diffs_*.csv"):
            counts["sale_diffs"] = sum(1 for _ in open(f, encoding="utf-8")) - 1
            break

    latest = {"timestamp": now, **counts, "success": success}
    (DATA_DIR / "topsystems_latest.json").write_text(json.dumps(latest, indent=2), encoding="utf-8")

    append_history({
        "timestamp": now, "workflow": "Top Systems Victron monthly",
        "success": success,
        "summary": f"{counts['missing']} missing · {counts['cost_diffs']} cost · {counts['sale_diffs']} sale",
        "url": workflow_url,
    })


def append_history(entry):
    h_path = DATA_DIR / "history.json"
    history = []
    if h_path.exists():
        try: history = json.loads(h_path.read_text(encoding="utf-8"))
        except: history = []
    history.append(entry)
    history = history[-100:]  # keep last 100
    h_path.write_text(json.dumps(history, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
