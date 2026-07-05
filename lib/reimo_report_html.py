"""
Bouwt de zelfstandige HTML voor het Reimo-rapport dat als ir.attachment in Odoo
wordt gezet en op /inttools/rapporten/reimo in een iframe getoond wordt.

Zelfde huisstijl (CSS/tabs) als het bestaande 'Producten met documenten'-rapport
zodat het native aanvoelt. Twee tabs:
  - Nieuwe producten  (uit data/reimo_newcomers.json, cumulatief met datum)
  - Niet meer verkocht (live uit Odoo: x_reimo_beschikbaarheid = NIET LEVERBAAR)
"""
from __future__ import annotations

import html
from datetime import datetime

_KLEUR = {"green": "#16a34a", "yellow": "#d97706", "orange": "#ea580c",
          "red": "#dc2626", "grey": "#9ca3af"}

CSS = """
  :root { --ink:#1d2433; --muted:#6b7280; --line:#e5e7eb; --bg:#f4f6f9; --accent:#0f766e; }
  * { box-sizing:border-box; }
  body { font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; color:var(--ink);
         margin:0; background:var(--bg); line-height:1.45; }
  .wrap { max-width:1180px; margin:0 auto; padding:28px 20px 64px; }
  header h1 { font-size:23px; margin:0 0 3px; }
  header .sub { color:var(--muted); font-size:13.5px; }
  .cards { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:22px 0 4px; }
  .card { background:#fff; border:1px solid var(--line); border-radius:12px; padding:15px 16px; }
  .card .k { font-size:26px; font-weight:700; color:var(--accent); line-height:1.1; }
  .card .l { font-size:12.5px; color:var(--muted); margin-top:3px; }
  .tabs { display:flex; gap:4px; margin:26px 0 0; border-bottom:2px solid var(--line); flex-wrap:wrap; }
  .tab { appearance:none; border:none; background:transparent; cursor:pointer; font:inherit;
         padding:10px 16px; color:var(--muted); border-bottom:2px solid transparent; margin-bottom:-2px;
         border-radius:8px 8px 0 0; font-weight:500; }
  .tab:hover { background:#eef2f6; color:var(--ink); }
  .tab.active { color:var(--accent); border-bottom-color:var(--accent); font-weight:600; }
  .tab .pill { display:inline-block; background:#e2e8f0; color:#475569; border-radius:999px;
               font-size:11px; padding:1px 7px; margin-left:6px; vertical-align:1px; }
  .tab.active .pill { background:#ccfbf1; color:#0f766e; }
  .panel { display:none; padding-top:18px; }
  .panel.active { display:block; }
  .muted { color:var(--muted); }
  .search { width:100%; max-width:380px; padding:9px 12px; border:1px solid var(--line);
            border-radius:9px; font:inherit; margin-bottom:14px; }
  table { width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line);
          border-radius:12px; overflow:visible; font-size:13px; }
  th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:middle; }
  th { background:#f1f5f9; font-weight:600; }
  tbody tr:nth-child(even) { background:#fafbfc; }
  tbody tr:hover { background:#f0fdfa; }
  tr:last-child td { border-bottom:none; }
  .num { text-align:right; white-space:nowrap; }
  .ctr { text-align:center; }
  .cat { color:#475569; font-size:12.5px; max-width:190px; }
  /* thumbnail + hover-zoom */
  .thumbwrap { position:relative; display:inline-block; line-height:0; }
  .thumb { width:76px; height:76px; object-fit:contain; background:#fff; border:1px solid var(--line);
           border-radius:8px; cursor:zoom-in; }
  .noimg { width:76px; height:76px; border:1px dashed var(--line); border-radius:8px;
           display:inline-block; }
  .thumbwrap .zoom { display:none; position:absolute; z-index:60; left:86px; top:50%;
           transform:translateY(-50%); width:320px; height:320px; object-fit:contain; background:#fff;
           border:1px solid var(--line); border-radius:14px; padding:8px;
           box-shadow:0 10px 34px rgba(0,0,0,.22); }
  .thumbwrap:hover .zoom { display:block; }
  .sku { font-family:ui-monospace,Menlo,Consolas,monospace; color:var(--muted); white-space:nowrap; }
  .pname { font-weight:600; }
  .pdesc { color:var(--muted); font-size:12px; }
  .verv-vol { color:#991b1b; font-weight:600; }
  .verv-part { color:#9a3412; font-weight:600; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; vertical-align:0; }
  .badge { display:inline-block; padding:2px 9px; border-radius:999px; font-size:12px; white-space:nowrap;
           background:#fee2e2; color:#991b1b; }
  .st-arch { background:#e5e7eb; color:#374151; }
  .st-web { background:#ffedd5; color:#9a3412; }
  .st-buy { background:#fee2e2; color:#991b1b; }
  a.btn { color:var(--accent); text-decoration:none; font-weight:600; white-space:nowrap; }
  a.btn:hover { text-decoration:underline; }
  a.addbtn { display:inline-block; background:#0f766e; color:#fff !important; text-decoration:none;
             font-weight:600; font-size:12px; padding:4px 10px; border-radius:8px; white-space:nowrap; }
  a.addbtn:hover { background:#0b5c55; }
  a.inodoo { display:inline-block; background:#dcfce7; color:#166534 !important; text-decoration:none;
             font-weight:600; font-size:12px; padding:4px 10px; border-radius:8px; white-space:nowrap;
             border:1px solid #86efac; }
  a.inodoo:hover { background:#bbf7d0; }
  .new { background:#ecfdf5 !important; }
  .newtag { display:inline-block; background:#ccfbf1; color:#0f766e; border-radius:999px;
            font-size:10.5px; padding:1px 7px; margin-left:6px; vertical-align:1px; }
  footer { margin-top:40px; color:var(--muted); font-size:12px; }
  @media (max-width:720px) { .cards { grid-template-columns:1fr; } .thumb,.noimg{width:42px;height:42px;} }
"""

SCRIPT = """<script>
function showTab(id, btn){
  document.querySelectorAll('.panel').forEach(function(p){p.classList.remove('active');});
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active');});
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}
function filterRows(inputId, tblId, cntId){
  var q = document.getElementById(inputId).value.toLowerCase();
  var n = 0;
  document.querySelectorAll('#'+tblId+' tbody tr').forEach(function(tr){
    var hit = tr.getAttribute('data-s').indexOf(q) !== -1;
    tr.style.display = hit ? '' : 'none';
    if (hit) n++;
  });
  if (cntId) document.getElementById(cntId).textContent = n;
}
</script>"""


def _td(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _thumb_html(thumb: str, zoom: str = "") -> str:
    """Kleine thumbnail met hover-zoom (grote afbeelding verschijnt bij mouseover)."""
    if not thumb:
        return "<span class='noimg'></span>"
    t = html.escape(thumb, quote=True)
    z = html.escape(zoom or thumb, quote=True)
    return (f"<span class='thumbwrap'><img class='thumb' loading='lazy' src='{t}' alt=''>"
            f"<img class='zoom' loading='lazy' src='{z}' alt=''></span>")


def _reimo_zoom(url: str) -> str:
    """Grotere Reimo-afbeelding voor de zoom (w200 -> full)."""
    return url.replace("/w200/", "/full/") if url else ""


def _fmt_datum(s: str) -> str:
    try:
        return datetime.fromisoformat(s).strftime("%d-%m-%Y")
    except Exception:
        return s or ""


def _newcomer_rows(producten: list[dict], laatste_datum: str,
                   odoo_codes: dict | None = None, odoo_base: str = "",
                   suite_url: str = "") -> str:
    odoo_codes = odoo_codes or {}
    rows = sorted(producten,
                  key=lambda p: (p.get("toegevoegd_op", ""), p.get("artikelnr", "")),
                  reverse=True)
    out = []
    for p in rows:
        art = _td(p.get("artikelnr"))
        naam = _td(p.get("naam"))
        merk = _td(p.get("leverancier"))
        prijs = _td(p.get("prijs"))
        img = p.get("afbeelding") or ""
        thumb = _thumb_html(img, _reimo_zoom(img))
        cat = _td(p.get("categorie"))
        cat_pad = html.escape(p.get("categorie_pad") or p.get("categorie") or "", quote=True)
        cat_cell = f"<td class='cat' title=\"{cat_pad}\">{cat or '—'}</td>"
        kleur = _KLEUR.get(p.get("voorraad_kleur", ""), "#9ca3af")
        lev = _td(p.get("leverbaarheid") or "—")
        dot = f"<span class='dot' style='background:{kleur}'></span>"
        toeg = p.get("toegevoegd_op", "")
        is_new = toeg == laatste_datum
        newtag = "<span class='newtag'>nieuw</span>" if is_new else ""
        url = html.escape(p.get("url", ""), quote=True)
        link = f"<a class='btn' href='{url}' target='_blank' rel='noopener'>Bekijk &#8599;</a>" if url else "—"
        desc = _td(p.get("omschrijving"))
        desc_html = f"<div class='pdesc'>{desc}</div>" if desc else ""
        # Odoo-actie: al aanwezig -> link naar product; anders -> toevoegen-knop
        artcode = str(p.get("artikelnr", ""))
        if artcode in odoo_codes:
            tid = odoo_codes[artcode]
            odoo_link = html.escape(f"{odoo_base}/odoo/inventory/products/{tid}", quote=True)
            actie = f"<a class='inodoo' href='{odoo_link}' target='_blank' rel='noopener'>&#10003; in Odoo</a>"
        else:
            add_link = html.escape(f"{suite_url}/product_toevoegen?artikelnr={artcode}", quote=True)
            actie = f"<a class='addbtn' href='{add_link}' target='_blank' rel='noopener'>&#43; In Odoo</a>"
        search = html.escape(f"{art} {naam} {merk} {p.get('categorie','')}".lower(), quote=True)
        rowcls = " class='new'" if is_new else ""
        out.append(
            f"<tr{rowcls} data-s=\"{search}\">"
            f"<td class='ctr'>{thumb}</td>"
            f"<td class='sku'>{art}</td>"
            f"<td><div class='pname'>{naam}{newtag}</div>{desc_html}</td>"
            f"<td>{merk}</td>"
            f"{cat_cell}"
            f"<td class='num'>{prijs}</td>"
            f"<td>{dot}{lev}</td>"
            f"<td class='ctr'>{_fmt_datum(toeg)}</td>"
            f"<td class='ctr'>{link}</td>"
            f"<td class='ctr'>{actie}</td>"
            "</tr>")
    return "".join(out)


def _discontinued_rows(items: list[dict], suite_url: str = "") -> str:
    out = []
    for d in items:
        code = _td(d.get("reimo_code"))
        odoo_code = _td(d.get("odoo_code"))
        naam = _td(d.get("naam"))
        det = _td(d.get("detail"))
        n_verv = d.get("n_vervallen") or 0
        n_tot = d.get("n_totaal") or 0
        melding = html.escape(d.get("melding", ""), quote=True)
        if d.get("volledig", True):
            kop, kls = "Volledig niet meer verkocht", "verv-vol"
        else:
            kop = f"{n_verv} van {n_tot} varianten niet meer verkocht &middot; overige nog leverbaar"
            kls = "verv-part"
        sub = f"<div class='pdesc {kls}' title=\"{melding}\">{kop}"
        if det:
            sub += f": {det}"
        sub += "</div>"
        naam_html = f"<div class='pname'>{naam}</div>{sub}"
        prijs = f"&euro; {float(d.get('prijs') or 0):.2f}".replace(".", ",")
        vr = float(d.get("voorraad") or 0)
        vr_txt = f"{vr:g}"
        vr_cell = (f"<td class='num' style='color:#166534;font-weight:600'>{vr_txt}</td>"
                   if vr > 0 else "<td class='num muted'>0</td>")
        # status = welke actie(s) uitgevoerd (uit de Odoo-toestand)
        badges = []
        if not d.get("actief", True):
            badges.append("<span class='badge st-arch'>&#128230; Gearchiveerd</span>")
        elif not d.get("op_website", True):
            badges.append("<span class='badge st-web'>&#127760; Uit webshop</span>")
        if not d.get("bestelbaar", True):
            badges.append("<span class='badge st-buy'>&#9940; Niet meer bestellen</span>")
        status = " ".join(badges) if badges else "<span class='muted'>geen actie</span>"
        thumb = _thumb_html(d.get("foto", ""), d.get("foto_groot", ""))
        url = html.escape(d.get("odoo_url", ""), quote=True)
        link = f"<a class='btn' href='{url}' target='_blank' rel='noopener'>Open &#8599;</a>" if url else "—"
        tmpl_id = d.get("tmpl_id")
        if tmpl_id:
            beheer_link = html.escape(f"{suite_url}/product_beheren?tmpl_id={tmpl_id}", quote=True)
            beheer = (f"<a class='addbtn' href='{beheer_link}' target='_blank' rel='noopener'>"
                      "&#9881; Beheren</a>")
        else:
            beheer = "—"
        search = html.escape(f"{code} {odoo_code} {naam}".lower(), quote=True)
        out.append(
            f"<tr data-s=\"{search}\">"
            f"<td class='ctr'>{thumb}</td>"
            f"<td class='sku'>{code}</td>"
            f"<td class='sku'>{odoo_code or '—'}</td>"
            f"<td>{naam_html}</td>"
            f"<td class='num'>{prijs}</td>"
            f"{vr_cell}"
            f"<td>{status}</td>"
            f"<td class='ctr'>{link}</td>"
            f"<td class='ctr'>{beheer}</td>"
            "</tr>")
    return "".join(out)


def build_html(store: dict, discontinued: list[dict], gen_date: str,
               odoo_codes: dict | None = None, odoo_base: str = "",
               suite_url: str = "https://compactliving-suite.streamlit.app") -> str:
    producten = list((store.get("products") or {}).values())
    datums = sorted({p.get("toegevoegd_op", "") for p in producten if p.get("toegevoegd_op")},
                    reverse=True)
    laatste_datum = datums[0] if datums else ""
    nieuw_laatste = sum(1 for p in producten if p.get("toegevoegd_op") == laatste_datum)
    in_odoo = sum(1 for p in producten if str(p.get("artikelnr", "")) in (odoo_codes or {}))

    nc_rows = _newcomer_rows(producten, laatste_datum, odoo_codes, odoo_base, suite_url) or \
        "<tr><td colspan='10' class='muted'>Nog geen producten opgeslagen.</td></tr>"
    dc_rows = _discontinued_rows(discontinued, suite_url) or \
        "<tr><td colspan='9' class='muted'>Geen producten op 'niet leverbaar'. \U0001F389</td></tr>"

    laatste_lbl = _fmt_datum(laatste_datum) if laatste_datum else "—"

    return f"""<!DOCTYPE html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reimo rapport &mdash; {gen_date}</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<header>
  <h1>Reimo &mdash; leveranciersrapport</h1>
  <div class="sub">Compact Living &middot; automatisch rapport &middot; gegenereerd op {gen_date}</div>
</header>

<div class="cards">
  <div class="card"><div class="k">{len(producten)}</div><div class="l">nieuwe producten in rapport (cumulatief)</div></div>
  <div class="card"><div class="k">{nieuw_laatste}</div><div class="l">nieuw op {laatste_lbl}</div></div>
  <div class="card"><div class="k">{len(discontinued)}</div><div class="l">niet meer verkocht (Odoo)</div></div>
</div>

<div class="tabs">
  <button class="tab active" onclick="showTab('p-nieuw', this)">Nieuwe producten<span class="pill">{len(producten)}</span></button>
  <button class="tab" onclick="showTab('p-uit', this)">Niet meer verkocht<span class="pill">{len(discontinued)}</span></button>
</div>

<section id="p-nieuw" class="panel active">
  <p class="muted">Producten van de Reimo-nieuwigheden, cumulatief bijgehouden met de datum
  waarop ze voor het eerst verschenen. Groen gemarkeerd = toegevoegd in de laatste ronde.
  <b>{in_odoo}</b> van de {len(producten)} staan al in Odoo; gebruik <b>&#43; In Odoo</b> om een
  product toe te voegen.</p>
  <input class="search" placeholder="Zoek op naam, artikelnr of merk&hellip;"
         id="q-nieuw" oninput="filterRows('q-nieuw','tbl-nieuw','cnt-nieuw')">
  <span class="muted" style="font-size:12px;">&nbsp;<b id="cnt-nieuw">{len(producten)}</b> getoond</span>
  <table id="tbl-nieuw"><thead><tr>
    <th class="ctr">Foto</th><th>Artikelnr</th><th>Naam</th><th>Merk</th><th>Categorie</th>
    <th class="num">Prijs</th><th>Leverbaarheid</th><th class="ctr">Toegevoegd</th><th class="ctr">Link</th>
    <th class="ctr">Odoo</th>
  </tr></thead><tbody>{nc_rows}</tbody></table>
</section>

<section id="p-uit" class="panel">
  <p class="muted">Reimo-producten die volgens de laatste Profiweb-sync niet meer leverbaar zijn
  (Auslauf). Live uit Odoo. Gebruik <b>&#9881; Beheren</b> om te archiveren (bij 0 voorraad),
  uit de webshop te halen (bij voorraad) of niet meer te bestellen.</p>
  <input class="search" placeholder="Zoek op naam of code&hellip;"
         id="q-uit" oninput="filterRows('q-uit','tbl-uit','cnt-uit')">
  <span class="muted" style="font-size:12px;">&nbsp;<b id="cnt-uit">{len(discontinued)}</b> getoond</span>
  <table id="tbl-uit"><thead><tr>
    <th class="ctr">Foto</th><th>Reimo-code</th><th>Odoo-code</th><th>Naam</th><th class="num">Verkoopprijs</th>
    <th class="num">Voorraad</th><th>Status</th><th class="ctr">Odoo</th><th class="ctr">Actie</th>
  </tr></thead><tbody>{dc_rows}</tbody></table>
</section>

<footer>Bron: reimo.com/nl/nieuwigheden (nieuwe producten) &amp; Odoo Profiweb-sync (beschikbaarheid).</footer>
</div>
{SCRIPT}
</body></html>"""
