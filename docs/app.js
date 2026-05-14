// Compact Living dashboard
// Reads JSON files committed by GitHub Actions workflows

const $ = (s) => document.querySelector(s);

// Detect repo from current URL: github.io/<repo>/...
const path = location.pathname.split("/").filter(Boolean);
const repoName = location.hostname.endsWith("github.io") && path.length ? path[0] : "compactliving-reimo";
const orgName = location.hostname.endsWith("github.io")
  ? location.hostname.split(".")[0] : "your-org";
const repoUrl = `https://github.com/${orgName}/${repoName}`;
$("#repo_link").href = repoUrl;
$("#generated_at").textContent = new Date().toLocaleString("nl-BE");

async function loadJson(path) {
  try {
    const r = await fetch(path + "?v=" + Date.now());
    if (!r.ok) return null;
    return await r.json();
  } catch (e) { return null; }
}

function formatWhen(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const now = new Date();
  const diffH = (now - d) / 1000 / 3600;
  if (diffH < 24) return "vandaag " + d.toLocaleTimeString("nl-BE", {hour:"2-digit", minute:"2-digit"});
  if (diffH < 48) return "gisteren";
  if (diffH < 24*7) return Math.round(diffH/24) + " dagen geleden";
  return d.toLocaleDateString("nl-BE");
}

(async () => {
  // Reimo latest
  const reimo = await loadJson("data/reimo_latest.json");
  if (reimo) {
    $("#reimo_when").textContent = formatWhen(reimo.timestamp);
    $("#reimo_ok").textContent = reimo.counts?.ok ?? "—";
    $("#reimo_warn").textContent = reimo.counts?.warn ?? "—";
    $("#reimo_block").textContent = reimo.counts?.block ?? "—";
    if (reimo.delta) $("#reimo_trend").textContent = reimo.delta;
  }
  // Top Systems latest
  const ts = await loadJson("data/topsystems_latest.json");
  if (ts) {
    $("#ts_when").textContent = formatWhen(ts.timestamp);
    $("#ts_updated").textContent = (ts.codes_total || 0) - (ts.cost_diffs || 0) - (ts.sale_diffs || 0);
    $("#ts_changed").textContent = (ts.cost_diffs || 0) + (ts.sale_diffs || 0);
    $("#ts_missing").textContent = ts.missing || 0;
    if (ts.delta) $("#ts_trend").textContent = ts.delta;
  }
  // Warnings table (alle templates met block/warning)
  const warnings = (await loadJson("data/reimo_warnings.json")) || [];
  const tbody = $("#warn_table tbody");
  if (!warnings.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#80868b;padding:20px">Geen waarschuwingen 🎉</td></tr>';
  } else {
    renderWarnings(warnings);
    $("#search_warn").addEventListener("input", () => renderWarnings(warnings));
  }
  function renderWarnings(rows) {
    const q = $("#search_warn").value.toLowerCase().trim();
    const filtered = q ? rows.filter(r =>
      (r.template || "").toLowerCase().includes(q) ||
      (r.detail || "").toLowerCase().includes(q)) : rows;
    tbody.innerHTML = "";
    filtered.forEach(r => {
      const tr = document.createElement("tr");
      tr.className = "tag-" + (r.action === "block" ? "block" : "warning");
      const icon = r.action === "block" ? "🚫 niet leverbaar" : "⚠ warning";
      tr.innerHTML = `
        <td>${icon}</td>
        <td>${escapeHtml(r.template || "")}</td>
        <td>${escapeHtml(r.categ || "")}</td>
        <td>${escapeHtml((r.detail || "").slice(0, 100))}</td>
        <td><a class="btn" href="${r.odoo_url || '#'}" target="_blank">Open in Odoo</a></td>
      `;
      tbody.appendChild(tr);
    });
  }
  // Run history
  const history = (await loadJson("data/history.json")) || [];
  const histBody = $("#history_table tbody");
  if (!history.length) {
    histBody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#80868b;padding:20px">Nog geen runs</td></tr>';
  } else {
    history.slice(-30).reverse().forEach(h => {
      const tr = document.createElement("tr");
      const status = h.success ? "✓ OK" : "✗ Fout";
      const cls = h.success ? "tag-ok" : "tag-block";
      tr.className = cls;
      tr.innerHTML = `
        <td>${formatWhen(h.timestamp)}</td>
        <td>${escapeHtml(h.workflow || "")}</td>
        <td>${status}</td>
        <td>${escapeHtml(h.summary || "")}</td>
        <td><a class="btn" href="${h.url || '#'}" target="_blank">Logs</a></td>
      `;
      histBody.appendChild(tr);
    });
  }
})();

function escapeHtml(s) {
  return (s + "").replace(/[<>&]/g, c => ({"<":"&lt;",">":"&gt;","&":"&amp;"}[c]));
}
