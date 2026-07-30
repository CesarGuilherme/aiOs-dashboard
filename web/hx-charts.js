// Chart islands for the /hx frontend. The server emits an empty sized <div>
// carrying `data-chart` (which chart) and `data-opt` (its ECharts payload as
// JSON); this mounts them after every htmx swap using the SPA's chart wrappers,
// unchanged. Teardown lives in the shell's htmx:beforeSwap hook (disposeAll).

import { barChart, donutChart, groupedBarChart, lineChart, stackedBarChart } from '/web/charts.js';

const KIND = {
  line: lineChart,
  bar: barChart,
  stacked: stackedBarChart,
  grouped: groupedBarChart,
  donut: donutChart,
};

export function mountCharts(root) {
  root.querySelectorAll('[data-chart]').forEach(el => {
    const fn = KIND[el.dataset.chart];
    if (!fn) return;
    try {
      // A history restore hands us cached markup — drop the dead SVG so ECharts
      // paints into an empty container instead of stacking on top of it.
      el.replaceChildren();
      fn(el, JSON.parse(el.dataset.opt));
    } catch (err) {
      el.innerHTML = `<p class="muted">chart failed: ${String(err)}</p>`;
    }
  });
}
