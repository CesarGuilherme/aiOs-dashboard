// charts.js — themed ECharts wrappers, HUD styling ported from jarvis-ui's ChartWidget

const COMPACT = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
const PALETTE = ['#27e0ff', '#ff4133', '#30d9b8', '#8b95a5', '#ffb53d', '#818cf8'];
const CHART_TICK = 'rgba(180, 220, 255, 0.48)';
const CHART_GRID = 'rgba(39, 224, 255, 0.13)';
const MONO = "'JetBrains Mono', ui-monospace, monospace";

const BASE = {
  textStyle: { color: 'rgba(226,244,255,0.92)', fontFamily: 'Inter' },
  color: PALETTE,
  grid: { left: 48, right: 14, top: 30, bottom: 28, containLabel: true },
};

const X_AXIS = {
  axisLine:  { lineStyle: { color: CHART_GRID } },
  axisLabel: { color: CHART_TICK, fontFamily: MONO, fontSize: 10, formatter: v => String(v).toUpperCase() },
  axisTick:  { show: false },
};

const Y_AXIS = {
  axisLine:  { show: false },
  axisTick:  { show: false },
  splitLine: { lineStyle: { color: CHART_GRID, type: 'dashed', dashOffset: 2 } },
  axisLabel: { color: CHART_TICK, fontFamily: MONO, fontSize: 10 },
};

function htmlEsc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** ECharts may pass a LinearGradient object as params.color for gradient bars. */
function cssColor(c) {
  if (typeof c === 'string') return c;
  if (c && Array.isArray(c.colorStops) && c.colorStops.length) {
    return c.colorStops[0].color || PALETTE[0];
  }
  return PALETTE[0];
}

function tooltipHtml(title, rows) {
  const rowsHtml = rows.map(r => {
    const color = cssColor(r.color);
    return `
    <div style="display:flex;align-items:center;gap:8px;font-size:11px;margin-top:5px">
      <span style="width:6px;height:6px;border-radius:50%;background:${color};box-shadow:0 0 6px ${color}88"></span>
      <span style="color:rgba(148,163,184,0.8)">${htmlEsc(r.name)}</span>
      <span style="margin-left:auto;padding-left:12px;font-weight:700;font-variant-numeric:tabular-nums">${htmlEsc(r.value)}</span>
    </div>`;
  }).join('');
  return `
    <div style="font-family:${MONO}">
      <div style="font-size:9px;font-weight:700;letter-spacing:.13em;text-transform:uppercase;color:#22d3ee">${htmlEsc(title)}</div>
      ${rowsHtml}
    </div>`;
}

function axisTooltipFormatter(valueFn) {
  const fmtVal = valueFn || (v => Number(v).toLocaleString());
  return params => {
    const list = Array.isArray(params) ? params : [params];
    if (!list.length) return '';
    return tooltipHtml(String(list[0].axisValueLabel ?? list[0].name ?? '').toUpperCase(), list.map(p => ({
      color: p.color,
      name: p.seriesName,
      value: fmtVal(p.value),
    })));
  };
}

const TOOLTIP = {
  trigger: 'axis',
  backgroundColor: '#050410',
  borderColor: 'rgba(34, 211, 238, 0.35)',
  borderWidth: 1,
  padding: [10, 12],
  extraCssText: 'box-shadow: 0 0 20px rgba(34, 211, 238, 0.15);',
  formatter: axisTooltipFormatter(),
};

const LEGEND_BASE = {
  textStyle: { color: CHART_TICK, fontFamily: MONO, fontSize: 10 },
  icon: 'circle', itemWidth: 7, itemHeight: 7,
  formatter: name => name.toUpperCase(),
};

function glowLine(color) {
  return { shadowBlur: 10, shadowColor: color };
}

function gradientBar(color) {
  return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color },
    { offset: 1, color: color + '33' },
  ]);
}

// Every chart instance + its window resize listener, so a route swap can
// dispose them all — without this they leak on full navigation re-renders,
// including a phantom resize handler per chart. Soft SSE refresh reuses
// instances via echarts.getInstanceByDom (see mount).
const mounted = [];

export function disposeAll() {
  for (const { c, onResize } of mounted) {
    window.removeEventListener('resize', onResize);
    c.dispose();
  }
  mounted.length = 0;
}

function mount(el) {
  // Reuse existing instance on soft refresh so setOption updates in place.
  const existing = echarts.getInstanceByDom(el);
  if (existing) return existing;

  const c = echarts.init(el, null, { renderer: 'svg' });
  const onResize = () => c.resize();
  window.addEventListener('resize', onResize);
  mounted.push({ c, onResize });
  // Corners come from app.js's blanket addHudCorners pass (size 12 / inset 0).
  // Don't call addHudCorners here — the existing-corner guard would make app.js a no-op.
  const card = el.closest('.card');
  if (card) card.classList.add('chart-card');
  return c;
}

/** Full replace of option data (soft refresh). */
function apply(c, option) {
  c.setOption(option, { notMerge: true });
  return c;
}

export function lineChart(el, { x, series }) {
  const c = mount(el);
  return apply(c, {
    ...BASE,
    tooltip: TOOLTIP,
    legend: { ...LEGEND_BASE, top: 0, right: 0 },
    xAxis: { ...X_AXIS, type: 'category', data: x, boundaryGap: false },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map((s, i) => ({
      ...s, type: 'line', smooth: true, showSymbol: false,
      areaStyle: { opacity: 0.12 },
      symbol: 'circle', symbolSize: 7,
      itemStyle: { borderColor: '#030d15', borderWidth: 2 },
      lineStyle: { width: 2.5, ...glowLine(s.color || PALETTE[i % PALETTE.length]) },
    })),
  });
}

export function barChart(el, { categories, values, color }) {
  const c = mount(el);
  const barColor = color || PALETTE[0];
  return apply(c, {
    ...BASE,
    tooltip: { ...TOOLTIP, axisPointer: { type: 'shadow' } },
    xAxis: { ...X_AXIS, type: 'category', data: categories, axisLabel: { ...X_AXIS.axisLabel, interval: 0, rotate: categories.length > 5 ? 25 : 0 } },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: [{
      type: 'bar', data: values,
      itemStyle: { color: gradientBar(barColor), borderRadius: [2, 2, 0, 0], ...glowLine(barColor) },
      barMaxWidth: 32,
    }],
  });
}

export function stackedBarChart(el, { categories, series, formatter }) {
  const c = mount(el);
  return apply(c, {
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      axisPointer: { type: 'shadow' },
      formatter: axisTooltipFormatter(formatter),
    },
    legend: { ...LEGEND_BASE, top: 0, right: 0 },
    xAxis: {
      ...X_AXIS, type: 'category', data: categories,
      axisLabel: { ...X_AXIS.axisLabel, interval: categories.length > 20 ? 'auto' : 0, rotate: categories.length > 12 ? 45 : 0 },
    },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map((s, i) => {
      const c2 = s.color || PALETTE[i % PALETTE.length];
      return {
        name: s.name,
        type: 'bar',
        stack: 'total',
        data: s.values,
        itemStyle: { color: gradientBar(c2) },
        barMaxWidth: 24,
        emphasis: { focus: 'series' },
      };
    }),
  });
}

export function groupedBarChart(el, { categories, series, formatter }) {
  const c = mount(el);
  return apply(c, {
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      axisPointer: { type: 'shadow' },
      formatter: axisTooltipFormatter(formatter),
    },
    legend: { ...LEGEND_BASE, top: 0, right: 0 },
    xAxis: {
      ...X_AXIS, type: 'category', data: categories,
      axisLabel: { ...X_AXIS.axisLabel, interval: 0, rotate: categories.length > 5 ? 25 : 0 },
    },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map((s, i) => {
      const c2 = s.color || PALETTE[i % PALETTE.length];
      return {
        name: s.name,
        type: 'bar',
        data: s.values,
        itemStyle: { color: gradientBar(c2), borderRadius: [4, 4, 0, 0], ...glowLine(c2) },
        barMaxWidth: 24,
        emphasis: { focus: 'series' },
      };
    }),
  });
}

export function donutChart(el, data) {
  const c = mount(el);
  const total = data.reduce((sum, d) => sum + (Number(d.value) || 0), 0);
  return apply(c, {
    color: PALETTE,
    tooltip: {
      trigger: 'item',
      backgroundColor: '#050410', borderColor: 'rgba(34, 211, 238, 0.35)', borderWidth: 1,
      extraCssText: 'box-shadow: 0 0 20px rgba(34, 211, 238, 0.15);',
      formatter: p => tooltipHtml(String(p.name).toUpperCase(), [
        { color: p.color, name: 'tokens', value: Number(p.value).toLocaleString() },
        { color: p.color, name: 'share', value: p.percent.toFixed(1) + '%' },
      ]),
    },
    legend: {
      ...LEGEND_BASE, orient: 'vertical', right: 4, top: 'middle', itemGap: 14,
      formatter: name => {
        const d = data.find(x => x.name === name);
        const pct = total && d ? ((d.value / total) * 100).toFixed(1) : '0.0';
        return `${name.toUpperCase()}  ${pct}%`;
      },
    },
    series: [{
      type: 'pie',
      center: ['32%', '50%'],
      radius: ['48%', '68%'],
      avoidLabelOverlap: true,
      padAngle: 2,
      itemStyle: { borderColor: '#050410', borderWidth: 2, borderRadius: 4 },
      label: { show: false },
      labelLine: { show: false },
      data,
    }],
    graphic: {
      elements: [
        {
          type: 'text', left: '29.5%', top: '44%',
          style: { text: COMPACT.format(total), fontSize: 20, fontWeight: 700, fontFamily: MONO, fill: PALETTE[0], textAlign: 'center' },
        },
        {
          type: 'text', left: '29.5%', top: '52%',
          style: { text: 'TOTAL', fontSize: 9, letterSpacing: 2, fontFamily: MONO, fill: CHART_TICK, textAlign: 'center' },
        },
      ],
    },
  });
}
