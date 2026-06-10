"""Generieke GitHub-persistentie voor data-bestanden in de Suite.

De Suite draait op Streamlit Community Cloud, waar de filesystem efemeer is:
bij een container-restart gaan lokale schrijfacties verloren. Om configuratie
en sync-snapshots te bewaren, lezen/schrijven we daarom via de GitHub Contents
API naar de repo zelf (zelfde mechanisme als de bestaande skip-list).

Gebruik:
    from gh_storage import load_json, save_json
    cfg = load_json("allspark_discounts.json", default={})
    cfg["by_brand"]["Victron"] = 0.30
    save_json("allspark_discounts.json", cfg, "Korting Victron bijgewerkt")

Zonder GH_TOKEN werkt alles nog steeds lokaal (handig voor dev); persistentie
over restarts heen is dan niet gegarandeerd.
"""
import os
import json
import base64
from pathlib import Path

import requests

# data/ ligt naast lib/ in de repo-root
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

GH_API = "https://api.github.com"
_TIMEOUT = 20


def _cfg():
    return {
        "token": os.environ.get("GH_TOKEN", ""),
        "repo": os.environ.get("GH_REPO", "compactlivingbe/compactliving-suite"),
        "branch": os.environ.get("GH_BRANCH", "main"),
    }


def gh_enabled() -> bool:
    return bool(os.environ.get("GH_TOKEN"))


def _headers(token):
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"}


def _repo_path(name: str) -> str:
    """Pad binnen de repo. Losse bestandsnaam -> data/<naam>.
    Een naam met '/' wordt als pad t.o.v. repo-root behandeld."""
    name = name.lstrip("/")
    return name if "/" in name else f"data/{name}"


def _local_path(name: str) -> Path:
    rel = _repo_path(name)
    return REPO_ROOT / rel


# ---------------------------------------------------------------------------
# Lage-niveau pull/push (werkt voor elk tekst/JSON-bestand)
# ---------------------------------------------------------------------------
def pull(name: str) -> str | None:
    """Haal de laatst gecommitte versie van GitHub en schrijf lokaal.
    Returns de tekstinhoud, of None als GH uit staat / bestand niet bestaat."""
    c = _cfg()
    if not c["token"]:
        return None
    try:
        r = requests.get(
            f"{GH_API}/repos/{c['repo']}/contents/{_repo_path(name)}",
            headers=_headers(c["token"]), params={"ref": c["branch"]},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            content = base64.b64decode(r.json()["content"]).decode("utf-8")
            lp = _local_path(name)
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text(content, encoding="utf-8")
            return content
    except Exception:
        pass
    return None


def push(name: str, commit_msg: str) -> tuple[bool, str]:
    """Push de lokale versie van <name> naar GitHub. Returns (ok, info)."""
    c = _cfg()
    if not c["token"]:
        return False, "Geen GH_TOKEN ingesteld"
    lp = _local_path(name)
    if not lp.exists():
        return False, f"Lokaal bestand bestaat niet: {lp}"
    try:
        # huidige sha ophalen (nodig om te overschrijven)
        r = requests.get(
            f"{GH_API}/repos/{c['repo']}/contents/{_repo_path(name)}",
            headers=_headers(c["token"]), params={"ref": c["branch"]},
            timeout=_TIMEOUT,
        )
        sha = r.json()["sha"] if r.status_code == 200 else None
        body = {
            "message": commit_msg,
            "content": base64.b64encode(lp.read_bytes()).decode("ascii"),
            "branch": c["branch"],
        }
        if sha:
            body["sha"] = sha
        r = requests.put(
            f"{GH_API}/repos/{c['repo']}/contents/{_repo_path(name)}",
            headers=_headers(c["token"]), json=body, timeout=_TIMEOUT,
        )
        if r.status_code in (200, 201):
            return True, r.json()["commit"]["sha"][:7]
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# JSON-gemak (pull-on-read, write+push)
# ---------------------------------------------------------------------------
def load_json(name: str, default=None, pull_first: bool = True):
    """Lees een JSON-bestand uit data/. Haalt eerst de GitHub-versie op zodat
    container-restarts altijd de actuele source-of-truth hebben."""
    if pull_first and gh_enabled():
        pull(name)
    lp = _local_path(name)
    if not lp.exists():
        return {} if default is None else default
    try:
        return json.loads(lp.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def save_json(name: str, data, commit_msg: str) -> tuple[bool, str]:
    """Schrijf JSON lokaal en push naar GitHub (indien GH_TOKEN aanwezig).
    Returns (pushed, info). Lokaal opslaan slaagt altijd."""
    lp = _local_path(name)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if gh_enabled():
        return push(name, commit_msg)
    return False, "lokaal opgeslagen (geen GH_TOKEN)"
