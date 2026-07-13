// app.js — router, state, fetch helpers

import { addHudCorners, mountHudBackground } from './hud-background.js';
import { disposeAll as disposeAllCharts } from './charts.js';
// setUsdBrlRate is defined below with fmt

export const $  = (sel, root=document) => root.querySelector(sel);
export const $$ = (sel, root=document) => Array.from(root.querySelectorAll(sel));

const COMPACT = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
// USD→BRL: filled from /api/overview or /api/plan (mirrors ~/.claude/.usd_brl)
let _usdBrl = 5.40;
export function setUsdBrlRate(r) {
  const n = Number(r);
  if (Number.isFinite(n) && n > 0) _usdBrl = n;
}
function _brl(n, digits) {
  if (n == null) return '—';
  const v = Number(n) * _usdBrl;
  return 'R$ ' + v.toFixed(digits).replace('.', ',');
}
export const fmt = {
  int:   n => (n ?? 0).toLocaleString(),
  compact: n => COMPACT.format(n ?? 0),
  // Primary money format is BRL; names kept as usd* for call-site compatibility.
  usd:   n => _brl(n, 2),
  usd4:  n => _brl(n, 4),
  brl:   n => _brl(n, 2),
  brl4:  n => _brl(n, 4),
  pct:   n => n == null ? '—' : (n * 100).toFixed(0) + '%',
  short: (s, n=80) => s == null ? '' : (s.length > n ? s.slice(0, n - 1) + '…' : s),
  htmlSafe: s => (s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  modelClass: m => {
    const s = (m || '').toLowerCase();
    if (s.includes('opus'))   return 'opus';
    if (s.includes('sonnet')) return 'sonnet';
    if (s.includes('haiku'))  return 'haiku';
    if (s.includes('grok'))   return 'grok';
    return '';
  },
  modelShort: m => (m || '').replace('claude-', '').replace(/^grok-/, 'grok-'),
  sourceBadge: s => s === 'grok' ? 'grok' : (s === 'claude' ? 'sonnet' : ''),
  ts: t => (t || '').slice(0, 16).replace('T', ' '),
};

export async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

export const state = { plan: 'api', pricing: null };

const ROUTES = {
  '/overview': () => import('/web/routes/overview.js'),
  '/brain':    () => import('/web/routes/brain.js'),
  '/prompts':  () => import('/web/routes/prompts.js'),
  '/sessions': () => import('/web/routes/sessions.js'),
  '/projects': () => import('/web/routes/projects.js'),
  '/skills':   () => import('/web/routes/skills.js'),
  '/tips':     () => import('/web/routes/tips.js'),
  '/settings': () => import('/web/routes/settings.js'),
};

function buildTopbar() {
  const wrap = document.createElement('header');
  wrap.className = 'topbar';
  wrap.innerHTML = `
    <div class="topbar-inner">
      <div class="brand font-hud">AI-DASHBOARD</div>
      <nav>
        ${Object.keys(ROUTES).map(p => `<a href="#${p}" data-route="${p}">${p.slice(1)}</a>`).join('')}
      </nav>
      <div class="spacer"></div>
      <span class="pill" id="plan-pill">api</span>
      <span class="pill muted" title="Cmd/Ctrl+B blurs sensitive text">⌘B blur</span>
    </div>
  `;
  document.body.prepend(wrap);
}

function setActiveTab(routeKey) {
  $$('header.topbar nav a').forEach(a => a.classList.toggle('active', a.dataset.route === routeKey));
}

// A route's default export may return a cleanup fn (e.g. the Brain rings cancel
// their animation loop + global listeners). Run it before swapping in the next view
// so nothing leaks or stacks up.
let currentCleanup = null;
// Module for the mounted route — used by soft SSE refresh (optional `refresh`).
let currentMod = null;
// Debounced SSE soft-refresh timer (cleared on full navigation).
let scanTimer = null;

function currentRouteKey() {
  const hash = location.hash.replace(/^#/, '') || '/overview';
  const path = hash.split('?')[0];
  if (path.startsWith('/sessions/')) return '/sessions';
  return path;
}

// Full remount — initial load + hash navigation only. Never used for SSE.
async function render() {
  clearTimeout(scanTimer);
  scanTimer = null;

  const key = currentRouteKey();
  setActiveTab(key);
  const loader = ROUTES[key] || ROUTES['/overview'];
  const mod = await loader();
  if (currentCleanup) { try { currentCleanup(); } catch {} currentCleanup = null; }
  currentMod = null;
  disposeAllCharts();
  $('#app').innerHTML = '';
  try {
    const cleanup = await mod.default($('#app'));
    $('#app').querySelectorAll('.card').forEach(card => addHudCorners(card, { accent: 'cyan', size: 12, inset: 0 }));
    if (typeof cleanup === 'function') currentCleanup = cleanup;
    currentMod = mod;
  } catch (e) {
    $('#app').innerHTML = `<div class="card"><h2>Error</h2><pre>${fmt.htmlSafe(String(e.stack || e))}</pre></div>`;
  }
}

// Live scan updates: patch the current view in place. Routes without `refresh`
// are a no-op (no flash). Brain is skipped so the rings canvas stays stable.
async function softRefresh() {
  if (location.hash.startsWith('#/brain')) return;
  const mod = currentMod;
  const root = $('#app');
  if (!mod || typeof mod.refresh !== 'function' || !root) return;
  try {
    await mod.refresh(root);
  } catch {}
}

async function firstRun() {
  if (localStorage.getItem('td.plan-set')) return;
  const plans = Object.entries(state.pricing.plans);
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal">
      <h2>Welcome — pick your plan</h2>
      <p>This sets how costs are displayed. Change it later in Settings.</p>
      <select id="firstplan" style="width:100%">
        ${plans.map(([k,v]) => `<option value="${k}">${v.label}${v.monthly ? ` — ${fmt.usd(v.monthly)}/mo` : ''}</option>`).join('')}
      </select>
      <div class="actions">
        <div class="spacer"></div>
        <button class="primary" id="firstsave">Continue</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  await new Promise(res => $('#firstsave', overlay).addEventListener('click', async () => {
    const plan = $('#firstplan', overlay).value;
    await fetch('/api/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan }) });
    localStorage.setItem('td.plan-set', '1');
    overlay.remove();
    res();
  }));
  state.plan = (await api('/api/plan')).plan;
}

async function boot() {
  // Apply JARVIS ironman-hud-ui layout (class + background lattice)
  document.documentElement.classList.add('hud-theme');
  mountHudBackground({ variant: 'subtle' });

  buildTopbar();
  const planResp = await api('/api/plan');
  state.plan = planResp.plan;
  state.pricing = planResp.pricing;
  if (planResp.usd_brl_rate) setUsdBrlRate(planResp.usd_brl_rate);
  $('#plan-pill').textContent = state.plan;

  await firstRun();

  window.addEventListener('hashchange', render);
  await render();

  // Privacy blur (Cmd+B / Ctrl+B)
  window.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'b') {
      e.preventDefault();
      document.body.classList.toggle('privacy-on');
    }
  });

  // SSE live updates — soft-refresh only (never full remount). Debounce bursts
  // so a busy scan doesn't thrash the overview; clear on nav in render().
  try {
    const es = new EventSource('/api/stream');
    es.onmessage = ev => {
      try {
        const evt = JSON.parse(ev.data);
        if (evt.type === 'scan' && !location.hash.startsWith('#/brain')) {
          clearTimeout(scanTimer);
          scanTimer = setTimeout(() => softRefresh(), 1500);
        }
      } catch {}
    };
  } catch {}
}

boot();
