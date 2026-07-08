import { api, fmt } from '/web/app.js';
import { forceGraph, lineChart, stackedBarChart } from '/web/charts.js';
import { galaxyCanvas } from '/web/galaxy.js';

const TYPE_CLASS = { user: 'opus', feedback: 'haiku', project: 'sonnet', reference: '', learning: 'haiku' };

export default async function (root) {
  let galaxyTeardown = null;   // returned to the router so the galaxy loop stops on navigate
  const brain = await api('/api/brain');
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
    <div class="card">
      <h2>Memory ROI</h2>
      <p class="muted" style="margin:-8px 0 16px">Does the auto-learning brain pay for itself? <b>Saved</b> = tokens spent re-reading files a memory already covers (last 30d — an avoided-re-read <em>estimate</em>). <b>Cost</b> = real tokens the background extraction passes burned. <b>Net</b> is the difference.</p>
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
      <p class="muted" style="margin:-8px 0 14px">Knowledge Claude keeps re-deriving instead of remembering — mined from the last 30 days of sessions. Copy the prompt into Claude Code to close the loop; once the memory exists the suggestion disappears.</p>
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
      <p class="muted" style="margin:-8px 0 14px">Memories the ranker injected <em>because it judged them relevant</em> to a prompt, but that were never referenced in the session — candidates to prune. Always-on global/baseline memories don't count here. Each appears in ≥3 sessions with zero hits.</p>
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

    <div class="card" style="margin-top:16px">
      <h2>Memory galaxy</h2>
      ${allEntries.length === 0
        ? '<p class="muted">No memories yet. Ask Claude Code to "save a memory" in any project and it shows up here live.</p>'
        : `<div class="graph-head">
             <p class="muted" style="margin:0">${allEntries.length} stars across ${brain.projects.length} projects · ${brain.links.filter(l => l.kind === 'explicit').length} solid [[links]] · ${brain.links.filter(l => l.kind === 'soft').length} faint auto-links. Brightest = freshest. Drag to orbit, scroll to zoom, click a star to open it, double-click to pause the flight.</p>
             <div class="range-tabs" id="brain-view">
               <button data-view="3d">3D</button>
               <button data-view="2d">2D</button>
             </div>
           </div>
            <div class="galaxy">
              <div id="brain-graph"></div>
              <div class="galaxy-hud" id="galaxy-hud">
                <div class="gx-title"><span class="gx-orbit">☊</span> Memory Galaxy</div>
                <div class="gx-count">${allEntries.length} stars · ${brain.links.length} links</div>
                <div class="gx-hint">drag to orbit · scroll to zoom · click a star</div>
                <div class="gx-legend">brighter = more connected / recently touched</div>
              </div>
              <div class="galaxy-stamp" id="galaxy-stamp">
                <div class="gs-date">${fmt.htmlSafe(new Date().toISOString().slice(0, 10))}</div>
                <div class="gs-meta">Memory OS · ${brain.links.length} links</div>
              </div>
              <div class="galaxy-info" id="galaxy-info" hidden></div>
            </div>`}
    </div>

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
        { name: 'auto', values: timeline.map(d => d.auto), color: '#3FB68B' },
        { name: 'you', values: timeline.map(d => d.user), color: '#7C5CFF' },
        { name: 'learnings', values: timeline.map(d => d.learnings), color: '#E8A23B' },
      ],
      formatter: v => `${v}`,
    });
  }

  if (allEntries.length) {
    const categories = brain.projects.map(p => p.label);
    // Galaxy brightness ∝ recency: newest memory burns brightest, oldest dimmest.
    // mtime is an ISO string, so parse to epoch ms before any arithmetic.
    const epoch = e => { const t = Date.parse(e.mtime); return Number.isNaN(t) ? null : t; };
    const times = allEntries.map(epoch).filter(t => t != null);
    const minT = times.length ? Math.min(...times) : 0;
    const maxT = times.length ? Math.max(...times) : 1;
    const span = Math.max(1, maxT - minT);
    const nodes = allEntries.map(e => ({
      id: e.id, name: e.name, desc: e.description,
      category: categories.indexOf(e.projectLabel),
      symbolSize: 12 + 4 * e.links.length,   // ECharts 2D sizing
      val: 1 + e.links.length,               // 3D sizing
      recency: ((epoch(e) ?? minT) - minT) / span,   // 0 (oldest) … 1 (freshest)
      mtime: e.mtime, project: e.projectLabel, links: e.links.length,
    }));
    const links = brain.links;
    const el = document.getElementById('brain-graph');
    const hud = document.getElementById('galaxy-hud');
    const info = document.getElementById('galaxy-info');
    const stamp = document.getElementById('galaxy-stamp');
    const onNodeClick = d => {
      const node = root.querySelector(`details[data-mem="${CSS.escape(d.id)}"]`);
      if (node) { node.open = true; node.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    };
    const onNodeHover = n => {
      if (!info) return;
      if (!n) { info.hidden = true; return; }
      info.hidden = false;
      info.innerHTML = `
        <div class="gi-name">${fmt.htmlSafe(n.name)}</div>
        <div class="gi-meta">${fmt.htmlSafe(n.project || '')} · ${n.links || 0} link${n.links === 1 ? '' : 's'}</div>
        ${n.mtime ? `<div class="gi-meta mono">${fmt.ts(n.mtime)}</div>` : ''}`;
    };

    let chart = null, view = null;
    const teardown = () => {
      if (!chart) return;
      if (view === '2d') chart.dispose();   // ECharts
      else chart.__teardown?.();            // galaxy canvas
      el.innerHTML = '';
      chart = null;
    };
    galaxyTeardown = teardown;
    const mount = next => {
      if (next === view) return;
      teardown();
      view = next;
      chart = next === '3d'
        ? galaxyCanvas(el, { nodes, links, onNodeClick, onNodeHover })
        : forceGraph(el, { nodes, links, categories, onNodeClick });
      // HUD belongs to the galaxy; hide it (and any stale info card) in 2D.
      if (hud) hud.style.display = next === '3d' ? '' : 'none';
      if (stamp) stamp.style.display = next === '3d' ? '' : 'none';
      if (info && next !== '3d') info.hidden = true;
      root.querySelectorAll('#brain-view button').forEach(b =>
        b.classList.toggle('active', b.dataset.view === next));
    };
    root.querySelectorAll('#brain-view button').forEach(b =>
      b.addEventListener('click', () => mount(b.dataset.view)));
    mount('3d');   // default to the galaxy view
  }

  root.querySelectorAll('button[data-copy]').forEach(b => {
    b.addEventListener('click', async () => {
      await navigator.clipboard.writeText(b.dataset.copy);
      b.textContent = 'copied ✓';
      setTimeout(() => { b.textContent = 'copy prompt'; }, 1500);
    });
  });
  root.querySelectorAll('button[data-key]').forEach(b => {
    b.addEventListener('click', async () => {
      await fetch('/api/tips/dismiss', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: b.dataset.key }),
      });
      location.reload();
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

  return () => galaxyTeardown?.();
}
