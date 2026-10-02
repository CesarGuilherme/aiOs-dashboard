// Brain graph island for the /hx frontend. The page ships nodes/links/workspace
// in <script type="application/json" id="rings-data">; this mounts the SPA's
// rings.js canvas on it, unchanged, and wires the sidebar.
//
// It MUST register window.__hxTeardown — the shell calls it before swapping the
// tab out. Without that the rAF loop, the interval and the ResizeObserver keep
// running on a detached canvas forever.

import { ringsCanvas } from '/web/rings.js';

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtAge = iso => {
  if (!iso) return '';
  const d = Math.floor((Date.now() - Date.parse(iso)) / 86400000);
  return d <= 0 ? 'today' : d + 'd ago';
};

export function mountBrain(root) {
  const dataEl = root.querySelector('#rings-data') || document.querySelector('#rings-data');
  const host = root.querySelector('#rings-canvas') || document.querySelector('#rings-canvas');
  if (!dataEl || !host) return;
  const { nodes, links, workspace } = JSON.parse(dataEl.textContent);
  host.replaceChildren();   // history restore may hand us a cached, dead canvas

  const panel = host.closest('.rings-wrap');
  const detail = panel.querySelector('#rings-detail');
  const search = panel.querySelector('#rings-search');
  const results = panel.querySelector('#rings-results');
  const layerLists = panel.querySelector('#rings-layers');

  const showDetail = n => {
    if (!n) {
      detail.className = 'rings-detail muted';
      detail.textContent = 'click a node';
      return;
    }
    const m = n.meta || {};
    const badges = [m.dept || m.project, n.kind || n.layer].filter(Boolean)
      .map(b => `<span class="badge">${esc(b)}</span>`).join(' ');
    const linked = links.filter(l => l.kind !== 'parent' && l.kind !== 'soft' &&
      (l.source === n.id || l.target === n.id))
      .sort((x, y) => (x.kind === 'link') - (y.kind === 'link')).slice(0, 10);
    const children = links.filter(l => l.kind === 'parent' && l.source === n.id).length;
    detail.className = 'rings-detail';
    detail.innerHTML = `
      <div class="gi-name">${esc(n.name)}</div>
      <div style="margin:4px 0">${badges}</div>
      ${m.summary ? `<div class="gi-meta" style="margin-bottom:4px">${esc(m.summary)}</div>` : ''}
      <div class="gi-meta">${[children && children + ' children', fmtAge(m.mtime)]
        .filter(Boolean).join(' · ')}</div>
      <div class="rings-actions"><button data-fly="1">Fly to</button></div>
      ${linked.length ? `<div class="gi-meta" style="margin-top:6px">CONNECTIONS</div>` +
        linked.map(l => {
          const other = l.source === n.id ? l.target : l.source;
          return `<div class="gi-meta">${l.source === n.id ? '→' : '←'} <span class="badge">${esc(l.kind)}</span> `
            + `${esc(String(other).split('/').pop())}${l.why ? ` — ${esc(l.why)}` : ''}</div>`;
        }).join('') : ''}`;
    detail.querySelector('[data-fly]')?.addEventListener('click', () => rings.flyTo(n.id));
  };

  const rings = ringsCanvas(host, {
    nodes, links,
    onNodeHover: () => {},
    onNodeClick: n => {
      showDetail(n);
      if (n.meta?.mem) {
        const el = root.querySelector(`details[data-mem="${CSS.escape(n.id)}"]`);
        if (el) { el.open = true; el.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
      }
    },
  });

  // --- search
  const index = nodes.map(n => ({ name: n.name, sub: n.meta?.project || n.group || n.layer, node: n }));
  const renderResults = q => {
    if (!q) { results.hidden = true; results.innerHTML = ''; return; }
    const ql = q.toLowerCase();
    const hits = index.filter(x => x.name.toLowerCase().includes(ql)).slice(0, 12);
    results.hidden = hits.length === 0;
    results.innerHTML = hits.map((x, i) => `
      <div class="rings-result" data-i="${i}">
        <span class="dot" style="background:#B48CFF"></span>
        <span class="rn">${esc(x.name)}</span>
        <span class="rp">${esc(x.sub)}</span>
      </div>`).join('');
    results.querySelectorAll('.rings-result').forEach(row =>
      row.addEventListener('click', () => {
        const x = hits[+row.dataset.i];
        results.hidden = true;
        if (!x) return;
        rings.flyTo(x.node.id);
        showDetail(x.node);
      }));
  };

  layerLists.innerHTML = [
    { title: 'Skills', items: workspace.skills || [], badge: s => s.source },
    { title: 'Routines', items: workspace.routines || [], badge: () => '' },
    { title: 'Applications', items: workspace.applications || [], badge: a => a.scope },
  ].filter(g => g.items.length).map(g => `
    <details>
      <summary>${g.title} <span class="muted">(${g.items.length})</span></summary>
      <div class="rings-layer-list">
        ${g.items.map(it => `
          <div class="rings-layer-item" data-name="${esc(it.name.toLowerCase())}">
            ${it.source || it.scope ? `<span class="badge">${esc(g.badge(it))}</span>` : ''}
            <span>${esc(it.name)}</span>
          </div>`).join('')}
      </div>
    </details>`).join('');

  search.addEventListener('input', () => {
    const q = search.value.trim();
    rings.setFilter(q || null);
    renderResults(q);
    const ql = q.toLowerCase();
    layerLists.querySelectorAll('.rings-layer-item').forEach(el => {
      el.style.display = !ql || el.dataset.name.includes(ql) ? '' : 'none';
    });
  });
  search.addEventListener('keydown', e => {
    if (e.key === 'Escape') { results.hidden = true; search.blur(); }
    if (e.key === 'Enter') results.querySelector('.rings-result')?.click();
  });
  const onSlash = e => {
    if (e.key === '/' && document.activeElement !== search &&
        !/INPUT|TEXTAREA/.test(document.activeElement?.tagName)) {
      e.preventDefault();
      search.focus();
    }
  };
  document.addEventListener('keydown', onSlash);

  const labels = panel.querySelector('#rings-labels');
  labels.checked = rings.labels;
  labels.addEventListener('change', () => rings.setLabels(labels.checked));
  const spin = panel.querySelector('#rings-spin');
  spin.value = rings.spin;
  spin.addEventListener('input', () => rings.setSpin(spin.value));

  window.__hxTeardown = () => {
    rings.__teardown();
    document.removeEventListener('keydown', onSlash);
  };
}
