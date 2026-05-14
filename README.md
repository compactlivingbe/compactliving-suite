# Compact Living Suite

Centrale automatiserings-app voor Compact Living. Combineert vier modules:

| Module | Wat | Schedule |
|---|---|---|
| **📄 Facturen** | PDF van leverancier → Odoo PO + Bill (Claude API extractie) | manueel/folder-watch |
| **📦 Reimo Sync** | Reimo Profiweb scrape → `sale_line_warn_msg` per template | wekelijks (GH Actions) |
| **🛒 Reimo Bestellen** | Odoo PO → Profiweb winkelmandje (beta) | manueel |
| **💰 Top Systems** | XML productlijst → Victron prijzen update | maandelijks (GH Actions) |

## Architectuur

```
compactliving-suite/
├── streamlit_app.py            ← landing + auth gate
├── pages/                      ← Streamlit multi-page
│   ├── 1_Facturen.py
│   ├── 2_Reimo_Sync.py
│   ├── 3_Reimo_Bestellen.py
│   └── 4_TopSystems_Prijzen.py
├── lib/                        ← gedeelde business logic
│   ├── odoo_client.py
│   ├── extractor.py
│   ├── matcher.py
│   ├── bill_matcher.py
│   ├── reimo_scraper.py
│   ├── reimo_orderer.py
│   ├── topsystems_sync.py
│   └── templates/
├── scripts/                    ← CLI helpers
│   ├── verwerk_factuur.py
│   ├── watch_inbox.py
│   └── publish_dashboard.py
├── .github/workflows/          ← schedules
│   ├── reimo_weekly.yml
│   └── topsystems_monthly.yml
├── docs/                       ← GitHub Pages dashboard
├── .streamlit/secrets.toml.example
└── requirements.txt
```

## Setup

### 1. Lokaal testen
```cmd
cd compactliving-suite
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
notepad .streamlit\secrets.toml      REM vul credentials in
streamlit run streamlit_app.py
```

### 2. Push naar GitHub
```cmd
git init -b main
git add .
git commit -m "Initial commit: Compact Living Suite"
git remote add origin https://github.com/<user>/compactliving-suite.git
git push -u origin main
```

### 3. Deploy Streamlit Cloud
1. https://share.streamlit.io → New app
2. Repository: `<user>/compactliving-suite`
3. Main file: `streamlit_app.py`
4. Settings → Secrets → plak inhoud van `secrets.toml.example` (met echte values)
5. Deploy

URL wordt: `https://<naam>.streamlit.app`

### 4. GitHub Secrets (voor Actions schedules)
Repo → Settings → Secrets → toevoegen:
- `ODOO_URL`, `ODOO_DB`, `ODOO_LOGIN`, `ODOO_PASSWORD`
- `PROFIWEB_USER`, `PROFIWEB_PASS`
- `TOPSYSTEMS_XML_URL`

### 5. GitHub Pages dashboard
Repo → Settings → Pages → Branch `main` / `/docs` → Save.
URL: `https://<user>.github.io/compactliving-suite/`

## Reimo Bestellen — beta

De checkout flow van Profiweb is **nog niet gemapt**. Vereist eenmalige HAR capture:

1. Login Profiweb in Chrome
2. F12 → Network → Preserve log aan
3. Doe handmatige test bestelling van 1 stuk
4. Rechtsklik in Network → "Save all as HAR with content"
5. Stuur HAR file → ik implementeer `lib/reimo_orderer.py`

Voor nu: pagina toont per PO de Reimo codes + opent Profiweb URLs voor manuele invoer
in Schnellbestellung.

## Kost

- Streamlit Cloud: gratis (private apps)
- GitHub Actions: gratis (public repos onbeperkt; private 2000 min/mo)
- Anthropic API: ~€0,01-0,05 per factuur
- GitHub Pages: gratis

**Totaal: ±€2-5/maand** (alleen Anthropic verbruik).
