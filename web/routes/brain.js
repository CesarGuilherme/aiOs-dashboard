import { api, fmt } from '/web/app.js';
import { lineChart, stackedBarChart } from '/web/charts.js';
import { ringsCanvas } from '/web/rings.js';

const TYPE_CLASS = { user: 'opus', feedback: 'haiku', project: 'sonnet', reference: '', learning: 'haiku' };

export default async function (root) {
  const [brain, workspace] = await Promise.all([
    api('/api/brain'),
    api('/api/workspace').catch(() => ({ applications: [], routines: [], skills: [] })),
  ]);
  const allEntries = brain.projects.flatMap(p =>
    p.entries.map(e => ({ ...e, projectLabel: p.label, slug: e.id.split('::')[0] })));
  const roi = brain.roi || {};
  const timeline = brain.timeline || [];
  const eff = brain.effectiveness || { by_name: {}, prune_candidates: [] };
  const prune = eff.prune_candidates || [];

  const signed = (n, f = fmt.compact) => (n > 0 ? '+' : '') + f(n);
  const lastCache = roi.cache_trend && roi.cache_trend.length
    ? roi.cache_trend[roi.cache_trend.length - 1].hit_rate : null;

  root.innerHTML = `
    <div class="card rings-card">
      <h2>Second brain</h2>
      <div class="rings-wrap">
        <div id="rings-canvas"></div>
        <div class="rings-panel">
          <input id="rings-search" type="search" placeholder="Search nodes… ( / )" autocomplete="off">
          <div id="rings-results" class="rings-results" hidden></div>
          <label class="rings-row"><input id="rings-labels" type="checkbox"> Node names</label>
          <label class="rings-row">Drift
            <input id="rings-spin" type="range" min="0" max="1" step="0.05">
          </label>
          <div id="rings-detail" class="rings-detail muted">click a node</div>
          <div class="rings-layers" id="rings-layers"></div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <h2>Memory ROI</h2>
      <p class="muted" style="margin:-8px 0 16px">Does the auto-learning brain pay for itself? <b>Saved</b> = tokens spent re-reading files a memory already covers (Claude + Grok, last 30d — an avoided-re-read <em>estimate</em>). <b>Cost</b> = real tokens Claude's background extraction passes burned (Grok has no equivalent pass). <b>Net</b> is the difference. Cache hit-rate includes both agents.</p>
      <div class="row cols-4">
        <div class="card kpi">
          <div class="label">Net (est.)</div>
          <div class="value big" style="color:${(roi.net_tokens_est || 0) >= 0 ? 'var(--good)' : 'var(--bad)'}">${signed(roi.net_tokens_est || 0)}<span style="font-size:13px;color:var(--muted)"> tok</span></div>
          <div class="sub">${roi.net_usd_est == null ? 'rough estimate' : signed(roi.net_usd_est, fmt.usd) + ' (rough)'}</div>
        </div>
        <div class="card kpi">
          <div class="label">Saved · potential</div>
          <div class="value">${fmt.compact(roi.saved_tokens_est || 0)}<span style="font-size:13px;color:var(--muted)"> tok</span></div>
          <div class="sub">${roi.reread_count || 0} re-reads · ${roi.memorized_targets || 0} memorized files</div>
        </div>
        <div class="card kpi">
          <div class="label">Extraction cost</div>
          <div class="value">${fmt.compact(roi.extraction_tokens || 0)}<span style="font-size:13px;color:var(--muted)"> tok</span></div>
          <div class="sub">${roi.extraction_sessions || 0} passes${roi.extraction_usd == null ? '' : ' · ' + fmt.usd(roi.extraction_usd)}</div>
        </div>
        <div class="card kpi">
          <div class="label">Cache hit · today</div>
          <div class="value">${lastCache == null ? '—' : fmt.pct(lastCache)}</div>
          <div class="sub">context reuse</div>
        </div>
      </div>
      ${roi.cache_trend && roi.cache_trend.length > 1
        ? '<h3 style="margin-top:18px">Cache hit-rate trend</h3><div id="roi-cache" style="height:200px"></div>' : ''}
    </div>

    ${timeline.length ? `
    <div class="card" style="margin-top:16px">
      <h2>Learning timeline</h2>
      <p class="muted" style="margin:-8px 0 14px">Memories added per day — <span class="badge haiku">auto</span> written by the background pass, <span class="badge opus">you</span> hand-written, plus journal learnings.</p>
      <div id="brain-timeline" style="height:220px"></div>
    </div>` : ''}
`;

  root.innerHTML += `
    ${brain.suggestions.length ? `
    <div class="card">
      <h2>Suggested memories</h2>
      <p class="muted" style="margin:-8px 0 14px">Knowledge agents keep re-deriving instead of remembering — mined from the last 30 days of sessions. Copy the prompt into Claude Code or Grok to close the loop; once the memory exists the suggestion disappears.</p>
      ${brain.suggestions.map(s => `
        <div class="tip">
          <div class="tip-head">
            <span class="badge">${fmt.htmlSafe(s.project)}</span>
            <strong>${fmt.htmlSafe(s.title)}</strong>
            <span class="spacer"></span>
            <button data-copy="${fmt.htmlSafe(s.prompt)}">copy prompt</button>
            <button class="ghost" data-key="${fmt.htmlSafe(s.key)}">dismiss</button>
          </div>
          <p class="tip-body">${fmt.htmlSafe(s.body)}</p>
        </div>`).join('')}
    </div>` : ''}

    ${prune.length ? `
    <div class="card" style="margin-top:16px">
      <h2>Memory effectiveness</h2>
      <p class="muted" style="margin:-8px 0 14px">Memories the ranker injected <em>because it judged them relevant</em> to a prompt, but that were never referenced in the session — candidates to prune. Always-on global/baseline memories don't count here. Each appears in ≥3 sessions with zero hits. Injection logs are Claude Code hooks only.</p>
      ${prune.map(pc => `
        <div class="tip">
          <div class="tip-head">
            <span class="badge">${fmt.htmlSafe(pc.label || pc.slug || '')}</span>
            <strong>${fmt.htmlSafe(pc.name)}</strong>
            <span class="muted">injected ${pc.injected}× · used 0${pc.last ? ' · last ' + fmt.ts(pc.last) : ''}</span>
            <span class="spacer"></span>
            ${pc.slug && pc.slug !== 'global' && pc.file
              ? `<button class="ghost danger" data-remove="${fmt.htmlSafe(pc.slug)}" data-file="${fmt.htmlSafe(pc.file)}" title="quarantine to memory/.trash and drop the index line">remove</button>`
              : ''}
          </div>
        </div>`).join('')}
    </div>` : ''}

    ${brain.projects.map(p => `
      <div class="card" style="margin-top:16px">
        <h2>${fmt.htmlSafe(p.label)} <span class="muted" style="font-weight:400;font-size:12px">· ${p.entries.length} memories${p.learnings.length ? ` · ${p.learnings.length} learnings` : ''}</span></h2>
        ${p.learnings.length ? `
          <h3>Learning curve</h3>
          ${p.learnings.map(l => `
            <div class="tip">
              <div class="tip-head">
                <span class="badge haiku">${fmt.htmlSafe(l.date)}</span>
                <strong>${fmt.htmlSafe(l.title)}</strong>
              </div>
              ${l.body ? `<p class="tip-body">${fmt.htmlSafe(l.body)}</p>` : ''}
            </div>`).join('')}
          ${p.entries.length ? '<hr class="divider">' : ''}` : ''}
        ${p.entries.map(e => `
          <details class="tip" data-mem="${fmt.htmlSafe(e.id)}" style="cursor:pointer">
            <summary class="tip-head" style="list-style:none">
              <span class="badge ${TYPE_CLASS[e.type] ?? ''}">${fmt.htmlSafe(e.type)}</span>
              ${e.source === 'auto' ? '<span class="badge auto" title="written automatically by the background extraction pass">auto</span>' : ''}
              <strong>${fmt.htmlSafe(e.name)}</strong>
              <span class="muted">${fmt.htmlSafe(fmt.short(e.description, 90))}</span>
              ${e.usage && e.usage.injected ? `<span class="badge ${e.usage.used ? 'sonnet' : ''}" title="rank-injected into ${e.usage.injected} session(s), referenced in ${e.usage.used}">${e.usage.used}/${e.usage.injected} used</span>` : ''}
              <span class="spacer"></span>
              ${e.source === 'auto' ? `
                <button class="ghost" data-keep="${fmt.htmlSafe(p.slug)}" data-file="${fmt.htmlSafe(e.file)}" title="confirm — stop badging as auto">keep</button>
                <button class="ghost danger" data-remove="${fmt.htmlSafe(p.slug)}" data-file="${fmt.htmlSafe(e.file)}" title="quarantine to memory/.trash and drop the index line">remove</button>` : ''}
              <span class="muted mono" style="font-size:11px">${fmt.ts(e.mtime)}</span>
            </summary>
            <p class="tip-body" style="white-space:pre-wrap">${fmt.htmlSafe(e.body)}</p>
          </details>`).join('')}
        ${!p.entries.length && !p.learnings.length ? '<p class="muted">Empty memory dir.</p>' : ''}
      </div>`).join('')}
  `;

  const cacheEl = document.getElementById('roi-cache');
  if (cacheEl && roi.cache_trend && roi.cache_trend.length > 1) {
    lineChart(cacheEl, {
      x: roi.cache_trend.map(d => d.day.slice(5)),
      series: [{ name: 'hit rate %', data: roi.cache_trend.map(d => +(d.hit_rate * 100).toFixed(1)) }],
    });
  }

  const tlEl = document.getElementById('brain-timeline');
  if (tlEl && timeline.length) {
    stackedBarChart(tlEl, {
      categories: timeline.map(d => d.day.slice(5)),
      series: [
        { name: 'auto', values: timeline.map(d => d.auto), color: '#2FE6B8' },
        { name: 'you', values: timeline.map(d => d.user), color: '#8B7CFF' },
        { name: 'learnings', values: timeline.map(d => d.learnings), color: '#FFB53D' },
      ],
      formatter: v => `${v}`,
    });
  }

  // --- rings: memory nodes + wikilinks only — the canvas is a pure synapse graph.
  // Skills/routines/applications are agentic-layer context, shown in the sidebar list instead.
  const nodes = allEntries.map(e => ({
    id: e.id, name: e.name, layer: 'memory', group: e.projectLabel,
    size: 1 + e.links.length,
    meta: { project: e.projectLabel, links: e.links.length, mtime: e.mtime, mem: true, name: e.name },
  }));
  const links = brain.links;

  const detail = root.querySelector('#rings-detail');
  const fmtSize = b => b == null ? '' : b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : b > 1024 ? (b / 1024).toFixed(0) + ' KB' : b + ' B';
  const fmtAge = iso => { if (!iso) return ''; const d = Math.floor((Date.now() - Date.parse(iso)) / 86400000); return d <= 0 ? 'today' : d + 'd ago'; };
  const showDetail = n => {
    if (!n) { detail.className = 'rings-detail muted'; detail.textContent = 'click a node'; return; }
    const m = n.meta || {};
    const badges = [m.dept || m.project, n.layer].filter(Boolean)
      .map(b => `<span class="badge">${fmt.htmlSafe(String(b))}</span>`).join(' ');
    const linked = (brain.links || []).filter(l => l.source === n.id || l.target === n.id)
      .map(l => l.source === n.id ? l.target : l.source).slice(0, 8);
    detail.className = 'rings-detail';
    detail.innerHTML = `
      <div class="gi-name">${fmt.htmlSafe(n.name)}</div>
      <div style="margin:4px 0">${badges}</div>
      <div class="gi-meta">${[m.count && m.count + ' files', fmtSize(m.size), fmtAge(m.mtime), fmt.htmlSafe(m.ext || '')].filter(Boolean).join(' · ')}</div>
      ${m.path || m.rel ? `<div class="gi-meta mono" style="word-break:break-all">${fmt.htmlSafe(m.rel || m.path)}</div>` : ''}
      <div class="rings-actions">
        <button data-fly="${fmt.htmlSafe(n.id)}">Fly to</button>
        ${m.path ? `<button data-copy-path="${fmt.htmlSafe(m.path)}">Copy path</button>
        <button data-open="${fmt.htmlSafe(m.path)}">Open on device</button>` : ''}
      </div>
      ${linked.length ? `<div class="gi-meta" style="margin-top:6px">CONNECTIONS</div>
        ${linked.map(id => `<div class="gi-meta">• ${fmt.htmlSafe(String(id).split('::').pop())}</div>`).join('')}` : ''}`;
    detail.querySelector('[data-fly]')?.addEventListener('click', () => rings.flyTo(n.id));
    detail.querySelector('[data-copy-path]')?.addEventListener('click', async ev => {
      await navigator.clipboard.writeText(ev.target.dataset.copyPath); ev.target.textContent = 'copied ✓';
    });
    detail.querySelector('[data-open]')?.addEventListener('click', () =>
      fetch('/api/open', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: n.meta.path }) }));
  };
  const rings = ringsCanvas(root.querySelector('#rings-canvas'), {
    nodes, links,
    onNodeHover: () => {},          // hover shows the on-canvas label only
    onNodeClick: n => {
      showDetail(n);
      if (n.meta?.mem) {
        const elx = root.querySelector(`details[data-mem="${CSS.escape(n.id)}"]`);
        if (elx) { elx.open = true; elx.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
      }
    },
  });

  // search dropdown
  const search = root.querySelector('#rings-search');
  const results = root.querySelector('#rings-results');
  const searchIndex = nodes.map(n => ({ name: n.name, sub: n.meta?.project || n.group || n.layer, target: n.id, node: n, color: '#B48CFF' }));
  const renderResults = q => {
    if (!q) { results.hidden = true; results.innerHTML = ''; return; }
    const ql = q.toLowerCase();
    const hits = searchIndex.filter(e => e.name.toLowerCase().includes(ql)).slice(0, 12);
    results.hidden = hits.length === 0;
    results.innerHTML = hits.map((e, i) => `
      <div class="rings-result" data-i="${i}">
        <span class="dot" style="background:${e.color}"></span>
        <span class="rn">${fmt.htmlSafe(e.name)}</span>
        <span class="rp">${fmt.htmlSafe(e.sub)}</span>
      </div>`).join('');
    results.querySelectorAll('.rings-result').forEach(row =>
      row.addEventListener('click', () => {
        const e = hits[+row.dataset.i];
        results.hidden = true;
        if (!e) return;
        rings.flyTo(e.target);
        showDetail(e.node);
      }));
  };
  const layerLists = root.querySelector('#rings-layers');
  layerLists.innerHTML = [
    { title: 'Skills', items: workspace.skills, badge: s => s.source },
    { title: 'Routines', items: workspace.routines, badge: () => '' },
    { title: 'Applications', items: workspace.applications, badge: a => a.scope },
  ].filter(g => g.items.length).map(g => `
    <details>
      <summary>${g.title} <span class="muted">(${g.items.length})</span></summary>
      <div class="rings-layer-list">
        ${g.items.map(it => `
          <div class="rings-layer-item" data-name="${fmt.htmlSafe(it.name.toLowerCase())}">
            ${it.source || it.scope ? `<span class="badge">${fmt.htmlSafe(g.badge(it))}</span>` : ''}
            <span>${fmt.htmlSafe(it.name)}</span>
          </div>`).join('')}
      </div>
    </details>`).join('');
  search.addEventListener('input', () => {
    const q = search.value.trim();
    rings.setFilter(q || null);
    renderResults(q);
    const ql = q.toLowerCase();
    layerLists.querySelectorAll('.rings-layer-item').forEach(el =>
      el.style.display = !ql || el.dataset.name.includes(ql) ? '' : 'none');
  });
  search.addEventListener('keydown', e => {
    if (e.key === 'Escape') { results.hidden = true; search.blur(); }
    if (e.key === 'Enter') results.querySelector('.rings-result')?.click();
  });
  const onSlash = e => {
    if (e.key === '/' && document.activeElement !== search && !/INPUT|TEXTAREA/.test(document.activeElement?.tagName)) {
      e.preventDefault(); search.focus();
    }
  };
  document.addEventListener('keydown', onSlash);
  search.placeholder = `Search ${searchIndex.length.toLocaleString()} memories… ( / )`;
  const labelsBox = root.querySelector('#rings-labels');
  labelsBox.checked = rings.labels;
  labelsBox.addEventListener('change', () => rings.setLabels(labelsBox.checked));
  const spinSlider = root.querySelector('#rings-spin');
  spinSlider.value = rings.spin;
  spinSlider.addEventListener('input', () => rings.setSpin(spinSlider.value));

  root.querySelectorAll('button[data-copy]').forEach(b => {
    b.addEventListener('click', async () => {
      await navigator.clipboard.writeText(b.dataset.copy);
      b.textContent = 'copied ✓';
      setTimeout(() => { b.textContent = 'copy prompt'; }, 1500);
    });
  });
  root.querySelectorAll('button[data-key]').forEach(b => {
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        const res = await fetch('/api/tips/dismiss', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: b.dataset.key }),
        });
        if (!res.ok) throw new Error('dismiss failed');
        const card = b.closest('.card');
        b.closest('.tip')?.remove();
        // Drop the whole "Suggested memories" section when empty
        if (card && !card.querySelector('.tip')) card.remove();
      } catch {
        b.disabled = false;
      }
    });
  });

  // Correct auto-written memories: quarantine (remove) or confirm (keep).
  const postBrain = async (path, slug, file) => {
    await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slug, file }),
    });
  };
  root.querySelectorAll('button[data-remove]').forEach(b => {
    b.addEventListener('click', async (ev) => {
      ev.preventDefault();
      await postBrain('/api/brain/remove', b.dataset.remove, b.dataset.file);
      location.reload();
    });
  });
  root.querySelectorAll('button[data-keep]').forEach(b => {
    b.addEventListener('click', async (ev) => {
      ev.preventDefault();
      await postBrain('/api/brain/keep', b.dataset.keep, b.dataset.file);
      location.reload();
    });
  });

  return () => { rings.__teardown(); document.removeEventListener('keydown', onSlash); };
}
