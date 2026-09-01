import { api, fmt, setUsdBrlRate, state } from '/web/app.js';
import { barChart, donutChart, groupedBarChart, stackedBarChart } from '/web/charts.js';

const RANGES = [
  { key: '7d',  label: '7d',  days: 7 },
  { key: '30d', label: '30d', days: 30 },
  { key: '90d', label: '90d', days: 90 },
  { key: 'all', label: 'All', days: null },
];

function hashQuery() {
  return location.hash.split('?')[1] || '';
}

function readParam(name, fallback = null) {
  const m = new RegExp(`(?:^|&)${name}=([^&]*)`).exec(hashQuery());
  return m ? decodeURIComponent(m[1]) : fallback;
}

function readRange() {
  const k = readParam('range');
  return RANGES.find(r => r.key === k) || RANGES[1];
}

function readSource() {
  const s = readParam('source', 'all');
  return (s === 'claude' || s === 'grok') ? s : 'all';
}

function writeQuery({ range, source }) {
  const base = (location.hash.replace(/^#/, '').split('?')[0]) || '/overview';
  const parts = [];
  if (range && range !== '30d') parts.push('range=' + encodeURIComponent(range));
  // always keep source when not all so deep-links work; omit all for clean URL
  if (source && source !== 'all') parts.push('source=' + encodeURIComponent(source));
  location.hash = '#' + base + (parts.length ? '?' + parts.join('&') : '');
}

function sinceIso(range) {
  if (!range.days) return null;
  return new Date(Date.now() - range.days * 86400 * 1000).toISOString();
}

function withParams(url, { since, source }) {
  const q = [];
  if (since) q.push('since=' + encodeURIComponent(since));
  if (source && source !== 'all') q.push('source=' + encodeURIComponent(source));
  if (!q.length) return url;
  return url + (url.includes('?') ? '&' : '?') + q.join('&');
}

function planSubtitle() {
  if (!state.pricing || state.plan === 'api') return '';
  const p = state.pricing.plans[state.plan];
  if (!p || !p.monthly) return '';
  return `<div class="sub">pay ${fmt.usd(p.monthly)}/mo on ${fmt.htmlSafe(p.label)}</div>`;
}

function sessionsRowsHtml(sessions) {
  return sessions.map(s => `
    <tr>
      <td class="mono">${fmt.ts(s.started)}</td>
      <td><span class="badge ${fmt.sourceBadge(s.source)}">${fmt.htmlSafe(s.source || 'claude')}</span></td>
      <td><a href="#/sessions/${encodeURIComponent(s.session_id)}">${fmt.htmlSafe(s.project_name || s.project_slug)}</a></td>
      <td class="num">${fmt.compact(s.tokens)}</td>
    </tr>`).join('') || '<tr><td colspan="4" class="muted">no sessions in this range</td></tr>';
}

function knowledgeHtml(brain) {
  const projs = brain.projects || [];
  const memories = projs.reduce((n, p) => n + (p.entries?.length || 0), 0);
  const links = (brain.links || []).length;
  const suggestions = (brain.suggestions || []).length;
  const neverUsed = (brain.effectiveness?.prune_candidates || []).length;
  const stat = (v, label, warn = false) =>
    `<span style="margin-right:18px"><b style="font-size:16px;color:${warn && v ? '#FFB454' : 'inherit'}">${v}</b> ${label}</span>`;
  return stat(memories, 'memories') +
    stat(projs.length, 'projects') +
    stat(links, 'wikilinks') +
    stat(suggestions, 'suggested', true) +
    stat(neverUsed, 'injected but never used', true);
}

function byModelData(byModel) {
  return byModel.map(m => ({
    name: fmt.modelShort(m.model) || 'unknown',
    value: (m.input_tokens || 0) + (m.output_tokens || 0)
         + (m.cache_create_5m_tokens || 0) + (m.cache_create_1h_tokens || 0),
  })).filter(d => d.value > 0);
}

function paintCharts({ daily, byModel, projects, tools }) {
  const billable = document.getElementById('ch-daily-billable');
  const cache = document.getElementById('ch-daily-cache');
  const model = document.getElementById('ch-model');
  const projEl = document.getElementById('ch-projects');
  const toolsEl = document.getElementById('ch-tools');
  if (!billable || !cache || !model || !projEl || !toolsEl) return;

  stackedBarChart(billable, {
    categories: daily.map(d => d.day),
    series: [
      { name: 'input',        values: daily.map(d => d.input_tokens),        color: '#27E0FF' },
      { name: 'output',       values: daily.map(d => d.output_tokens),       color: '#8B7CFF' },
      { name: 'cache create', values: daily.map(d => d.cache_create_tokens), color: '#FFB53D' },
    ],
  });
  stackedBarChart(cache, {
    categories: daily.map(d => d.day),
    series: [
      { name: 'cache read', values: daily.map(d => d.cache_read_tokens), color: '#2FE6B8' },
    ],
  });
  donutChart(model, byModelData(byModel));

  const topProjects = projects.slice(0, 8);
  groupedBarChart(projEl, {
    categories: topProjects.map(p => {
      const name = p.project_name || p.project_slug;
      return name.length > 20 ? name.slice(0, 19) + '…' : name;
    }),
    series: [
      { name: 'input',  values: topProjects.map(p => p.input_tokens  || 0), color: '#27E0FF' },
      { name: 'output', values: topProjects.map(p => p.output_tokens || 0), color: '#8B7CFF' },
    ],
  });

  const topTools = tools.slice(0, 8);
  barChart(toolsEl, {
    categories: topTools.map(t => t.tool_name),
    values: topTools.map(t => t.calls),
    color: '#8B7CFF',
  });
}

async function fetchOverviewBundle() {
  const range = readRange();
  const source = readSource();
  const since = sinceIso(range);
  const q = { since, source };
  const [totals, projects, sessions, tools, daily, byModel] = await Promise.all([
    api(withParams('/api/overview', q)),
    api(withParams('/api/projects', q)),
    api(withParams('/api/sessions?limit=10', q)),
    api(withParams('/api/tools', q)),
    api(withParams('/api/daily', q)),
    api(withParams('/api/by-model', q)),
  ]);
  if (totals.usd_brl_rate) setUsdBrlRate(totals.usd_brl_rate);
  return { range, source, totals, projects, sessions, tools, daily, byModel };
}

function patchLiveData(root, data) {
  const { range, source, totals, projects, sessions, tools, daily, byModel } = data;
  const cacheCreate =
    (totals.cache_create_5m_tokens || 0) +
    (totals.cache_create_1h_tokens || 0);

  const meta = root.querySelector('.flex > .muted');
  if (meta) {
    meta.textContent =
      (range.days ? `last ${range.days} days` : 'all time') +
      (totals.usd_brl_rate ? ` · USD→BRL ${Number(totals.usd_brl_rate).toFixed(2).replace('.', ',')}` : '');
  }

  // Source chip labels (keep selection; only refresh counts meta)
  const sources = totals.by_source || [];
  const byKey = Object.fromEntries(sources.map(s => [s.source, s]));
  root.querySelectorAll('.source-chip').forEach(btn => {
    const key = btn.dataset.source;
    if (key === 'all') {
      btn.textContent = 'all';
      return;
    }
    const s = byKey[key];
    if (!s) return;
    btn.innerHTML =
      `${fmt.htmlSafe(s.source)} · ${fmt.int(s.sessions)} sess · ${fmt.compact(s.billable_tokens)} tok`;
  });

  const kpis = root.querySelectorAll('.row.cols-7 .card.kpi');
  const setKpi = (i, compactVal, fullVal) => {
    const card = kpis[i];
    if (!card) return;
    const value = card.querySelector('.value');
    if (!value) return;
    value.textContent = compactVal;
    value.title = fullVal;
  };
  setKpi(0, fmt.int(totals.sessions), fmt.int(totals.sessions));
  setKpi(1, fmt.int(totals.turns), fmt.int(totals.turns));
  setKpi(2, fmt.compact(totals.input_tokens), fmt.int(totals.input_tokens) + ' tokens');
  setKpi(3, fmt.compact(totals.output_tokens), fmt.int(totals.output_tokens) + ' tokens');
  setKpi(4, fmt.compact(totals.cache_read_tokens), fmt.int(totals.cache_read_tokens) + ' tokens');
  setKpi(5, fmt.compact(cacheCreate), fmt.int(cacheCreate) + ' tokens');
  setKpi(6, fmt.usd(totals.cost_usd), fmt.usd(totals.cost_usd));

  const tbody = root.querySelector('#recent-sessions-body');
  if (tbody) tbody.innerHTML = sessionsRowsHtml(sessions);

  paintCharts({ daily, byModel, projects, tools });

  // knowledge strip (async, non-blocking)
  api('/api/brain').then(brain => {
    const el = root.querySelector('#knowledge-stats') || document.getElementById('knowledge-stats');
    if (el) el.innerHTML = knowledgeHtml(brain);
  }).catch(() => {
    const el = root.querySelector('#knowledge-stats') || document.getElementById('knowledge-stats');
    if (el) el.textContent = 'brain data unavailable';
  });
}

/** Soft SSE refresh — patch data in place; no full remount. */
export async function refresh(root) {
  if (!root?.querySelector?.('#ch-daily-billable')) return;
  const data = await fetchOverviewBundle();
  patchLiveData(root, data);
}

export default async function (root) {
  const data = await fetchOverviewBundle();
  const { range, source, totals, projects, sessions } = data;

  const cacheCreate =
    (totals.cache_create_5m_tokens || 0) +
    (totals.cache_create_1h_tokens || 0);

  // Chips always list agents present in by_source (unfiltered counts from API)
  const sources = totals.by_source || [];
  const chip = (key, label, cls, meta = '') => {
    const active = source === key;
    return `<button type="button" class="source-chip badge ${cls}${active ? ' active' : ''}"
      data-source="${key}" aria-pressed="${active}">
      ${fmt.htmlSafe(label)}${meta ? ` · ${meta}` : ''}
    </button>`;
  };
  const chips = [
    chip('all', 'all', 'all-src'),
    ...sources.map(s => chip(
      s.source,
      s.source,
      fmt.sourceBadge(s.source) || 'sonnet',
      `${fmt.int(s.sessions)} sess · ${fmt.compact(s.billable_tokens)} tok`,
    )),
  ].join('');

  // Cycle through the theme's accent tokens so each KPI tile glows a
  // distinct color, like sentimentos' per-tile HUD accents.
  const KPI_ACCENTS = ['--accent', '--accent-2', '--warn', '--good'];
  let kpiIndex = 0;
  const kpi = (label, compactVal, fullVal, cls = '') => {
    const accent = KPI_ACCENTS[kpiIndex++ % KPI_ACCENTS.length];
    return `
    <div class="card kpi ${cls}" style="--hud-accent:var(${accent})">
      <div class="label">${label}</div>
      <div class="value" title="${fullVal}">${compactVal}</div>
    </div>`;
  };

  const rangeTabs = `
    <div class="range-tabs" role="tablist">
      ${RANGES.map(r => `<button data-range="${r.key}" class="${r.key === range.key ? 'active' : ''}">${r.label}</button>`).join('')}
    </div>`;

  const filterHint = source === 'all'
    ? ''
    : `<span class="muted filter-hint" style="font-size:12px">showing <b>${fmt.htmlSafe(source)}</b> only</span>`;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px">
      <h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">Overview</h2>
      <span class="muted" style="font-size:12px">${range.days ? `last ${range.days} days` : 'all time'}${totals.usd_brl_rate ? ` · USD→BRL ${Number(totals.usd_brl_rate).toFixed(2).replace('.', ',')}` : ''}</span>
      <div class="spacer"></div>
      ${rangeTabs}
    </div>
    <div class="flex source-chips" style="gap:8px;margin:-4px 0 14px;flex-wrap:wrap;align-items:center">
      ${chips}
      ${filterHint}
    </div>

    <div class="row cols-7">
      ${kpi('Sessions',     fmt.int(totals.sessions),       fmt.int(totals.sessions))}
      ${kpi('Turns',        fmt.int(totals.turns),          fmt.int(totals.turns))}
      ${kpi('Input',        fmt.compact(totals.input_tokens),       fmt.int(totals.input_tokens) + ' tokens')}
      ${kpi('Output',       fmt.compact(totals.output_tokens),      fmt.int(totals.output_tokens) + ' tokens')}
      ${kpi('Cache read',   fmt.compact(totals.cache_read_tokens),  fmt.int(totals.cache_read_tokens) + ' tokens')}
      ${kpi('Cache create', fmt.compact(cacheCreate),               fmt.int(cacheCreate) + ' tokens')}
      <div class="card kpi cost">
        <div class="label">Est. cost</div>
        <div class="value" title="${fmt.usd(totals.cost_usd)}">${fmt.usd(totals.cost_usd)}</div>
        ${planSubtitle()}
      </div>
    </div>

    <div class="card" id="knowledge-card" style="margin-top:16px">
      <div class="flex">
        <h3 style="margin:0">Knowledge</h3>
        <span class="muted" style="font-size:12px">your second brain at a glance</span>
        <div class="spacer"></div>
        <a href="#/brain" style="font-size:12px">Open Brain →</a>
      </div>
      <div id="knowledge-stats" class="muted" style="margin-top:8px;font-size:13px">loading…</div>
    </div>

    <details class="card glossary" style="margin-top:16px">
      <summary><h3 style="display:inline-block;margin:0">What do these numbers mean?</h3><span class="muted" style="font-size:12px">— click to expand</span></summary>
      <dl>
        <dt>Session</dt><dd>One agent run — Claude Code JSONL under <code>~/.claude/projects/</code>, or a Grok session under <code>~/.grok/sessions/</code>.</dd>
        <dt>Turn</dt><dd>One message you sent. Each turn triggers a response (possibly with tool calls in between).</dd>
        <dt>Input tokens</dt><dd>New context this turn. Claude: billed input. Grok: reconstructed from context-size deltas (see Known Limitations).</dd>
        <dt>Output tokens</dt><dd>Agent reply text. Claude: billed output. Grok: estimated from message length.</dd>
        <dt>Cache read</dt><dd>Tokens re-used from cache (~10× cheaper). Claude + Grok (from <code>turn_completed.usage</code>).</dd>
        <dt>Cache create</dt><dd>Writing into the cache (Claude only — Grok does not report create buckets).</dd>
        <dt>Est. cost</dt><dd>Shown in <strong>R$</strong> (USD rates × <code>~/.brain/.usd_brl</code>). API-equivalent, not subscription math.</dd>
        <dt>Billable tokens</dt><dd>Input + Output + Cache create. Cache reads are billed separately (and much cheaper).</dd>
        <dt>Agent chips</dt><dd>Click <b>claude</b> or <b>grok</b> to filter every KPI and chart on this page to that agent. Click again or <b>all</b> to clear.</dd>
      </dl>
    </details>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card">
        <h3>Your daily work</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:12px">Tokens you paid for: what you sent (<b>input</b>), what the agent wrote (<b>output</b>), and what got stored for re-use (<b>cache create</b>).</p>
        <div id="ch-daily-billable" style="height:260px"></div>
      </div>
      <div class="card">
        <h3>Daily cache reads</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:12px"><b>Cache reads</b> are cheap re-uses of things already seen (like CLAUDE.md / system context). They cost ~10× less than regular input — high numbers here are good.</p>
        <div id="ch-daily-cache" style="height:260px"></div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card"><h3>Tokens by project</h3><div id="ch-projects" style="height:320px"></div></div>
      <div class="card">
        <h3>Token usage by model</h3>
        <p class="muted" style="margin:-4px 0 4px;font-size:12px">Share of billable tokens per model${source !== 'all' ? ` (${fmt.htmlSafe(source)})` : ' (Claude + Grok)'}.</p>
        <div id="ch-model" style="height:300px"></div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card"><h3>Top tools (by call count)</h3><div id="ch-tools" style="height:320px"></div></div>
      <div class="card">
        <h3 style="display:flex;align-items:center"><span>Recent sessions</span><span class="spacer"></span><a href="#/sessions" style="font-weight:400;font-size:12px">all →</a></h3>
        <table>
          <thead><tr><th>started</th><th>agent</th><th>project</th><th class="num">tokens</th></tr></thead>
          <tbody id="recent-sessions-body">
            ${sessionsRowsHtml(sessions)}
          </tbody>
        </table>
      </div>
    </div>
  `;

  // range buttons — preserve source
  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => writeQuery({ range: btn.dataset.range, source }));
  });

  // source chips — toggle: click active source → all
  root.querySelectorAll('.source-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const next = btn.dataset.source;
      const selected = (next === source && next !== 'all') ? 'all' : next;
      writeQuery({ range: range.key, source: selected });
    });
  });

  paintCharts(data);

  // knowledge card — fetched after first paint so it never delays the overview
  api('/api/brain').then(brain => {
    const el = document.getElementById('knowledge-stats');
    if (!el) return; // user already navigated away
    el.innerHTML = knowledgeHtml(brain);
  }).catch(() => {
    const el = document.getElementById('knowledge-stats');
    if (el) el.textContent = 'brain data unavailable';
  });
}
