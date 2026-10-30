// Stacked panels that share one x-axis and one crosshair: the fitness/form
// chart on the dashboard and the stream charts on an activity page.
//
// panelChart(container, {
//   x: number[],                       ascending; timestamps or seconds
//   xTicks(min, max) -> number[],      tick positions
//   xFormat(x) -> string,              axis label
//   xTitle(x) -> string,               tooltip title (defaults to xFormat)
//   panels: [{ kind: 'line' | 'columns', height, title, yFormat, yTicks,
//              reverse, includeZero, min, max, bands: [{from, to, color, label}],
//              series: [{ key, label, color, values, dash, area, endLabel,
//                         negativeColor }] }],
//   markers: [{x, label}],             vertical lines across every panel
//   xBands: [{from, to, label}],       x ranges shaded across every panel
//   legend: boolean, table: boolean, note(index) -> string
// }) -> { update(spec), destroy() }
import {
  svgEl, htmlEl, colorOf, scaleLinear, ticks, niceDomain, nearestIndex,
  roundedBarPath, Tooltip, observeWidth, spreadLabels,
} from './core.js';

const MARGIN = { top: 10, right: 16, bottom: 26, left: 46 };
const END_LABEL_WIDTH = 44;
const PANEL_GAP = 22;
let instanceCount = 0;

export function panelChart(container, initialSpec) {
  container.classList.add('chart');
  const uid = ++instanceCount;
  const toolbar = htmlEl('div', 'row spread wrap chart-toolbar', container);
  const legend = htmlEl('div', 'legend', toolbar);
  const tableToggle = htmlEl('button', 'btn btn-ghost btn-sm', toolbar);
  tableToggle.type = 'button';
  tableToggle.textContent = 'Table';
  const svg = svgEl('svg', { tabindex: 0, role: 'img' }, container);
  const tableWrap = htmlEl('div', 'chart-table hidden', container);
  const tooltip = new Tooltip(container);

  let spec = initialSpec;
  let hidden = new Set();
  let hoverIndex = null;
  let layout = null;
  let width = 0;

  function visibleSeries(panel) {
    return panel.series.filter((series) => !hidden.has(series.key));
  }

  function hasEndLabels() {
    return spec.panels.some((panel) => panel.series.some((series) => series.endLabel));
  }

  function renderLegend() {
    legend.replaceChildren();
    const all = spec.panels.flatMap((panel) => panel.series.map((series) => ({ ...series, kind: panel.kind })));
    if (!spec.legend || all.length < 2) {
      legend.classList.add('hidden');
      return;
    }
    legend.classList.remove('hidden');
    for (const series of all) {
      const button = htmlEl('button', '', legend);
      button.type = 'button';
      button.setAttribute('aria-pressed', String(!hidden.has(series.key)));
      const key = htmlEl('span', `key${series.kind === 'columns' ? ' square' : ''}${series.points && series.width === 0 ? ' dot' : ''}${series.dash ? ' dashed' : ''}`, button);
      key.style.background = series.dash ? '' : colorOf(series.color);
      key.style.color = colorOf(series.color);
      const label = htmlEl('span', '', button);
      label.textContent = series.label;
      button.addEventListener('click', () => {
        if (hidden.has(series.key)) hidden.delete(series.key);
        else hidden.add(series.key);
        render();
      });
    }
  }

  function renderTable() {
    tableWrap.replaceChildren();
    const table = htmlEl('table', 'table table-compact', tableWrap);
    const head = htmlEl('tr', '', htmlEl('thead', '', table));
    htmlEl('th', '', head).textContent = spec.tableXLabel || '';
    const columns = spec.panels.flatMap((panel) => panel.series.map((series) => ({ series, panel })));
    for (const column of columns) {
      const th = htmlEl('th', 'num', head);
      th.textContent = column.series.label;
    }
    const body = htmlEl('tbody', '', table);
    const step = Math.max(1, Math.ceil(spec.x.length / 400));
    for (let index = 0; index < spec.x.length; index += step) {
      const row = htmlEl('tr', '', body);
      htmlEl('td', '', row).textContent = (spec.xTitle || spec.xFormat)(spec.x[index]);
      for (const column of columns) {
        const value = column.series.values[index];
        htmlEl('td', 'num', row).textContent = value == null ? '–' : column.panel.yFormat(value);
      }
    }
  }

  function computeLayout() {
    const right = hasEndLabels() ? MARGIN.right + END_LABEL_WIDTH : MARGIN.right;
    const plotWidth = Math.max(40, width - MARGIN.left - right);
    const xDomain = [spec.x[0], spec.x[spec.x.length - 1]];
    const xScale = scaleLinear(xDomain, [MARGIN.left, MARGIN.left + plotWidth]);
    let top = MARGIN.top;
    const panels = spec.panels.map((panel) => {
      if (panel.title) top += 16;
      const values = visibleSeries(panel).flatMap((series) => series.values.filter((value) => value != null));
      let [min, max] = values.length ? [Math.min(...values), Math.max(...values)] : [0, 1];
      if (panel.bands) {
        for (const band of panel.bands) {
          min = Math.min(min, band.from);
          max = Math.max(max, band.to);
        }
      }
      if (panel.min != null) min = panel.min;
      if (panel.max != null) max = panel.max;
      const domain = niceDomain(min, max, panel.yTicks || 4, panel.includeZero || panel.kind === 'columns');
      const range = panel.reverse ? [top, top + panel.height] : [top + panel.height, top];
      const yScale = scaleLinear(domain, range);
      const entry = { panel, top, bottom: top + panel.height, yScale, domain };
      top += panel.height + PANEL_GAP;
      return entry;
    });
    const totalHeight = top - PANEL_GAP + MARGIN.bottom;
    return { xScale, panels, plotWidth, totalHeight, right };
  }

  function drawAxes(group) {
    const { xScale, panels } = layout;
    const [xMin, xMax] = xScale.domain;
    for (const entry of panels) {
      const yTicks = ticks(entry.domain[0], entry.domain[1], entry.panel.yTicks || 4);
      for (const tick of yTicks) {
        const y = entry.yScale(tick);
        svgEl('line', { class: tick === 0 ? 'axis-line' : 'grid-line', x1: xScale.range[0], x2: xScale.range[1], y1: y, y2: y }, group);
        const text = svgEl('text', { class: 'axis-text', x: MARGIN.left - 8, y: y + 4, 'text-anchor': 'end' }, group);
        text.textContent = entry.panel.yFormat(tick, true);
      }
      if (entry.panel.title) {
        const title = svgEl('text', { class: 'panel-title', x: MARGIN.left, y: entry.top - 6 }, group);
        title.textContent = entry.panel.title;
      }
    }
    const last = panels[panels.length - 1];
    svgEl('line', { class: 'axis-line', x1: xScale.range[0], x2: xScale.range[1], y1: last.bottom, y2: last.bottom }, group);
    const xTickValues = spec.xTicks ? spec.xTicks(xMin, xMax) : ticks(xMin, xMax, 6);
    for (const tick of xTickValues) {
      const x = xScale(tick);
      const text = svgEl('text', { class: 'axis-text', x, y: last.bottom + 17, 'text-anchor': 'middle' }, group);
      text.textContent = spec.xFormat(tick);
    }
  }

  function drawBands(group, entry) {
    (entry.panel.bands || []).forEach((band, index) => {
      const y1 = entry.yScale(band.from);
      const y2 = entry.yScale(band.to);
      const top = Math.min(y1, y2);
      const height = Math.abs(y2 - y1);
      // Neighbouring bands alternate opacity so their edges stay visible.
      svgEl('rect', {
        x: layout.xScale.range[0], y: top, width: layout.plotWidth, height,
        fill: colorOf(band.color), 'fill-opacity': index % 2 ? 0.1 : 0.2,
      }, group);
      if (band.label && height > 18) {
        const label = svgEl('text', { class: 'band-label', x: layout.xScale.range[1] - 4, y: top + height / 2 + 3.5, 'text-anchor': 'end' }, group);
        label.textContent = band.label;
      }
    });
  }

  // x ranges (the reps of an interval session) shaded behind every panel.
  function drawXBands(group, entry) {
    for (const band of spec.xBands || []) {
      const from = layout.xScale(Math.max(band.from, layout.xScale.domain[0]));
      const to = layout.xScale(Math.min(band.to, layout.xScale.domain[1]));
      if (to <= from) continue;
      svgEl('rect', {
        x: from, y: entry.top, width: to - from, height: entry.panel.height,
        fill: colorOf('series-muted'), 'fill-opacity': 0.16, class: 'x-band',
      }, group);
    }
  }

  function drawXBandLabels(group) {
    const first = layout.panels[0];
    for (const band of spec.xBands || []) {
      if (!band.label) continue;
      const from = layout.xScale(Math.max(band.from, layout.xScale.domain[0]));
      const to = layout.xScale(Math.min(band.to, layout.xScale.domain[1]));
      if (to - from < 12) continue;
      const text = svgEl('text', { class: 'x-band-label', x: (from + to) / 2, y: first.top + 11, 'text-anchor': 'middle' }, group);
      text.textContent = band.label;
    }
  }

  function linePath(series, entry) {
    const { xScale } = layout;
    let path = '';
    let pen = false;
    series.values.forEach((value, index) => {
      if (value == null) {
        pen = false;
        return;
      }
      const x = xScale(spec.x[index]).toFixed(1);
      const y = entry.yScale(value).toFixed(1);
      path += `${pen ? 'L' : 'M'}${x},${y}`;
      pen = true;
    });
    return path;
  }

  function drawLine(group, series, entry) {
    if (series.points) {
      series.values.forEach((value, index) => {
        if (value == null) return;
        svgEl('circle', {
          cx: layout.xScale(spec.x[index]), cy: entry.yScale(value), r: 3.5,
          fill: colorOf(series.color), stroke: 'var(--surface)', 'stroke-width': 1.5,
        }, group);
      });
    }
    if (series.width === 0) return;
    const path = linePath(series, entry);
    if (!path) return;
    if (series.area) {
      const baseline = entry.yScale(Math.max(entry.domain[0], 0));
      const first = series.values.findIndex((value) => value != null);
      let last = series.values.length - 1;
      while (last > 0 && series.values[last] == null) last -= 1;
      const area = `${path}L${layout.xScale(spec.x[last]).toFixed(1)},${baseline}L${layout.xScale(spec.x[first]).toFixed(1)},${baseline}Z`;
      svgEl('path', { d: area, fill: colorOf(series.color), 'fill-opacity': 0.1 }, group);
    }
    svgEl('path', {
      d: path, fill: 'none', stroke: colorOf(series.color), 'stroke-width': series.width || 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round', 'stroke-dasharray': series.dash ? '5 4' : null,
      'stroke-opacity': series.opacity ?? null,
    }, group);
  }

  function drawColumns(group, series, entry) {
    const { xScale } = layout;
    const slot = spec.x.length > 1 ? layout.plotWidth / (spec.x.length - 1) : layout.plotWidth;
    const barWidth = Math.max(1, Math.min(24, slot * 0.72));
    const zero = entry.yScale(0);
    series.values.forEach((value, index) => {
      if (value == null || value === 0) return;
      const x = xScale(spec.x[index]) - barWidth / 2;
      const y = entry.yScale(value);
      const negative = value < 0;
      const color = negative ? colorOf(series.negativeColor || series.color) : colorOf(series.color);
      const path = negative
        ? roundedBarPath(x, zero, barWidth, y - zero, 4, 'down')
        : roundedBarPath(x, y, barWidth, zero - y, 4, 'up');
      svgEl('path', { d: path, fill: color, class: 'bar', 'data-index': index, 'fill-opacity': series.opacity ?? null }, group);
    });
  }

  // Vertical markers (race days) across every panel, labelled at the top.
  function drawMarkers(group) {
    const first = layout.panels[0];
    const last = layout.panels[layout.panels.length - 1];
    for (const marker of spec.markers || []) {
      if (marker.x < layout.xScale.domain[0] || marker.x > layout.xScale.domain[1]) continue;
      const x = layout.xScale(marker.x);
      svgEl('line', { class: 'marker-line', x1: x, x2: x, y1: first.top - (first.panel.title ? 14 : 4), y2: last.bottom }, group);
      if (marker.label) {
        const text = svgEl('text', { class: 'marker-label', x: x + 5, y: first.top - (first.panel.title ? 18 : 8) }, group);
        text.textContent = marker.label;
      }
    }
  }

  function drawEndLabels(group) {
    const labels = [];
    for (const entry of layout.panels) {
      for (const series of visibleSeries(entry.panel)) {
        if (!series.endLabel || entry.panel.kind !== 'line') continue;
        let last = series.values.length - 1;
        while (last >= 0 && series.values[last] == null) last -= 1;
        if (last < 0) continue;
        labels.push({ y: entry.yScale(series.values[last]), text: entry.panel.yFormat(series.values[last]), x: layout.xScale(spec.x[last]) });
      }
    }
    spreadLabels(labels, 13);
    for (const label of labels) {
      const text = svgEl('text', { class: 'end-label', x: layout.xScale.range[1] + 8, y: label.y + 4 }, group);
      text.textContent = label.text;
    }
  }

  function drawHover(group) {
    if (hoverIndex == null) {
      tooltip.hide();
      return;
    }
    const x = layout.xScale(spec.x[hoverIndex]);
    const first = layout.panels[0];
    const last = layout.panels[layout.panels.length - 1];
    svgEl('line', { class: 'crosshair', x1: x, x2: x, y1: first.top, y2: last.bottom }, group);
    const rows = [];
    for (const entry of layout.panels) {
      for (const series of visibleSeries(entry.panel)) {
        const value = series.values[hoverIndex];
        if (value == null) continue;
        const [low, high] = [Math.min(...entry.domain), Math.max(...entry.domain)];
        if (entry.panel.kind === 'line' && value >= low && value <= high) {
          svgEl('circle', { class: 'hover-dot', cx: x, cy: entry.yScale(value), r: 4, fill: colorOf(series.color) }, group);
        }
        rows.push({
          color: value < 0 && series.negativeColor ? series.negativeColor : series.color,
          label: series.label,
          value: entry.panel.yFormat(value),
          square: entry.panel.kind === 'columns',
        });
      }
    }
    const title = (spec.xTitle || spec.xFormat)(spec.x[hoverIndex]);
    const midY = (first.top + last.bottom) / 2;
    tooltip.show(x, midY, { title, rows, note: spec.note ? spec.note(hoverIndex) : null });
  }

  function render() {
    if (!spec.x.length) {
      svg.replaceChildren();
      return;
    }
    layout = computeLayout();
    svg.setAttribute('viewBox', `0 0 ${width} ${layout.totalHeight}`);
    svg.setAttribute('height', layout.totalHeight);
    svg.setAttribute('aria-label', spec.ariaLabel || 'Chart');
    svg.replaceChildren();
    const defs = svgEl('defs', {}, svg);
    drawAxes(svgEl('g', {}, svg));
    layout.panels.forEach((entry, index) => {
      // Values outside a fixed domain (a capped pace axis) are clipped, not drawn across other panels.
      const clip = svgEl('clipPath', { id: `panel-clip-${uid}-${index}` }, defs);
      svgEl('rect', { x: layout.xScale.range[0], y: entry.top - 1, width: layout.plotWidth, height: entry.panel.height + 2 }, clip);
      const group = svgEl('g', { 'clip-path': `url(#panel-clip-${uid}-${index})` }, svg);
      drawXBands(group, entry);
      drawBands(group, entry);
      for (const series of visibleSeries(entry.panel)) {
        if (entry.panel.kind === 'columns') drawColumns(group, series, entry);
        else drawLine(group, series, entry);
      }
    });
    drawXBandLabels(svgEl('g', {}, svg));
    drawMarkers(svgEl('g', {}, svg));
    drawEndLabels(svgEl('g', {}, svg));
    drawHover(svgEl('g', { class: 'hover-layer' }, svg));
    const hit = svgEl('rect', {
      class: 'hit', x: layout.xScale.range[0], y: layout.panels[0].top,
      width: layout.plotWidth, height: layout.panels[layout.panels.length - 1].bottom - layout.panels[0].top,
    }, svg);
    hit.addEventListener('pointermove', (event) => {
      const bounds = svg.getBoundingClientRect();
      const px = ((event.clientX - bounds.left) / bounds.width) * width;
      setHover(nearestIndex(spec.x, layout.xScale.invert(px)));
    });
    hit.addEventListener('pointerleave', () => setHover(null));
    renderLegend();
    renderTable();
  }

  function setHover(index) {
    if (index === hoverIndex) return;
    hoverIndex = index;
    const old = svg.querySelector('.hover-layer');
    const group = svgEl('g', { class: 'hover-layer' });
    if (old) svg.replaceChild(group, old);
    else svg.appendChild(group);
    drawHover(group);
  }

  svg.addEventListener('keydown', (event) => {
    if (!spec.x.length) return;
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      event.preventDefault();
      const delta = event.key === 'ArrowRight' ? 1 : -1;
      const next = hoverIndex == null ? spec.x.length - 1 : Math.max(0, Math.min(spec.x.length - 1, hoverIndex + delta));
      setHover(next);
    } else if (event.key === 'Escape') {
      setHover(null);
    }
  });
  svg.addEventListener('blur', () => setHover(null));

  tableToggle.addEventListener('click', () => {
    const open = tableWrap.classList.toggle('hidden');
    tableToggle.setAttribute('aria-expanded', String(!open));
  });
  tableToggle.classList.toggle('hidden', !spec.table);

  const stopObserving = observeWidth(container, (nextWidth) => {
    width = nextWidth;
    render();
  });

  return {
    update(nextSpec) {
      spec = nextSpec;
      hoverIndex = null;
      hidden = new Set([...hidden].filter((key) => spec.panels.some((panel) => panel.series.some((series) => series.key === key))));
      tableToggle.classList.toggle('hidden', !spec.table);
      render();
    },
    destroy() {
      stopObserving();
      container.replaceChildren();
    },
  };
}
