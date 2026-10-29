// Bars, columns and sparklines. Every value is labeled or in the table, so
// the hover tooltip only repeats what is already on screen.
import {
  svgEl, htmlEl, colorOf, scaleLinear, ticks, niceDomain, roundedBarPath, Tooltip, observeWidth,
} from './core.js';

// Horizontal bars, one row per category, the value written at the bar's end.
// barList(container, { rows: [{ label, sub, value, display, color }], max })
export function barList(container, spec) {
  container.classList.add('chart');
  const svg = svgEl('svg', { role: 'img', 'aria-label': spec.ariaLabel || 'Bar chart' }, container);
  const tooltip = new Tooltip(container);
  const rowHeight = spec.rowHeight || 30;
  const labelWidth = spec.labelWidth || 120;
  const valueWidth = 56;

  const stop = observeWidth(container, (width) => {
    const height = spec.rows.length * rowHeight;
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('height', height);
    svg.replaceChildren();
    const max = spec.max ?? Math.max(1, ...spec.rows.map((row) => row.value));
    const scale = scaleLinear([0, max], [0, Math.max(10, width - labelWidth - valueWidth)]);
    spec.rows.forEach((row, index) => {
      const y = index * rowHeight;
      const barY = y + (rowHeight - 14) / 2;
      const label = svgEl('text', { class: 'cat-label', x: 0, y: y + rowHeight / 2 - (row.sub ? 3 : -4) }, svg);
      label.textContent = row.label;
      if (row.sub) {
        const sub = svgEl('text', { class: 'cat-sub', x: 0, y: y + rowHeight / 2 + 10 }, svg);
        sub.textContent = row.sub;
      }
      const barWidth = scale(row.value);
      svgEl('rect', { x: labelWidth, y: barY, width: scale.range[1], height: 14, fill: 'var(--surface-2)', rx: 4 }, svg);
      if (barWidth > 0) {
        svgEl('path', { d: roundedBarPath(labelWidth, barY, Math.max(barWidth, 2), 14, 4, 'right'), fill: colorOf(row.color), class: 'bar' }, svg);
      }
      const value = svgEl('text', { class: 'bar-label', x: labelWidth + scale.range[1] + 8, y: y + rowHeight / 2 + 4 }, svg);
      value.textContent = row.display ?? String(row.value);
      const hit = svgEl('rect', { class: 'bar-hit', x: 0, y, width, height: rowHeight }, svg);
      hit.addEventListener('pointerenter', () => tooltip.show(labelWidth + barWidth, y + rowHeight / 2, {
        title: row.label, rows: [{ color: row.color, label: row.sub || '', value: row.detail ?? row.display ?? String(row.value), square: true }],
      }));
      hit.addEventListener('pointerleave', () => tooltip.hide());
    });
  });
  return { destroy: stop };
}

// One horizontal stacked bar with a legend: part-to-whole for a few classes.
// stackedBar(container, { segments: [{ label, value, color, display }] })
export function stackedBar(container, spec) {
  container.classList.add('chart');
  const svg = svgEl('svg', { role: 'img', 'aria-label': spec.ariaLabel || 'Share' }, container);
  const legend = htmlEl('div', 'legend mt-2', container);
  const tooltip = new Tooltip(container);
  const total = spec.segments.reduce((sum, segment) => sum + segment.value, 0) || 1;

  legend.replaceChildren();
  for (const segment of spec.segments) {
    const item = htmlEl('span', 'row', legend);
    item.style.gap = '6px';
    const key = htmlEl('span', 'key square', item);
    key.style.background = colorOf(segment.color);
    const text = htmlEl('span', '', item);
    text.textContent = `${segment.label} ${Math.round((segment.value / total) * 100)}%`;
  }

  const stop = observeWidth(container, (width) => {
    const height = 14;
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('height', height);
    svg.replaceChildren();
    const gap = 2;
    const usable = width - gap * (spec.segments.length - 1);
    let x = 0;
    spec.segments.forEach((segment, index) => {
      const segmentWidth = (segment.value / total) * usable;
      const isFirst = index === 0;
      const isLast = index === spec.segments.length - 1;
      const rect = svgEl('rect', {
        x, y: 0, width: Math.max(0, segmentWidth), height, fill: colorOf(segment.color), class: 'bar',
        rx: isFirst || isLast ? 4 : 0,
      }, svg);
      rect.addEventListener('pointerenter', () => tooltip.show(x + segmentWidth / 2, height / 2, {
        title: segment.label,
        rows: [{ color: segment.color, label: 'Share', value: `${Math.round((segment.value / total) * 100)}%`, square: true },
          ...(segment.display ? [{ color: segment.color, label: spec.valueLabel || 'Total', value: segment.display, square: true }] : [])],
      }));
      rect.addEventListener('pointerleave', () => tooltip.hide());
      x += segmentWidth + gap;
    });
  });
  return { destroy: stop };
}

// Vertical columns for a short category series (weeks). One column is the
// emphasis (accent), the rest are muted; only the listed indices get labels.
// columnChart(container, { categories: [{ label, sub }], values, display(value),
//   highlightIndex, labelIndices, height })
export function columnChart(container, spec) {
  container.classList.add('chart');
  const svg = svgEl('svg', { role: 'img', 'aria-label': spec.ariaLabel || 'Column chart' }, container);
  const tooltip = new Tooltip(container);
  const margin = { top: 18, right: 8, bottom: 34, left: 34 };
  const height = spec.height || 200;

  const stop = observeWidth(container, (width) => {
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('height', height);
    svg.replaceChildren();
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const domain = niceDomain(0, Math.max(1, ...spec.values), 4, true);
    const yScale = scaleLinear(domain, [margin.top + plotHeight, margin.top]);
    for (const tick of ticks(domain[0], domain[1], 4)) {
      const y = yScale(tick);
      svgEl('line', { class: tick === 0 ? 'axis-line' : 'grid-line', x1: margin.left, x2: width - margin.right, y1: y, y2: y }, svg);
      const text = svgEl('text', { class: 'axis-text', x: margin.left - 8, y: y + 4, 'text-anchor': 'end' }, svg);
      text.textContent = spec.tickFormat ? spec.tickFormat(tick) : String(tick);
    }
    const slot = plotWidth / spec.values.length;
    const barWidth = Math.min(24, slot * 0.6);
    const labelIndices = new Set(spec.labelIndices || []);
    spec.values.forEach((value, index) => {
      const cx = margin.left + slot * index + slot / 2;
      const x = cx - barWidth / 2;
      const y = yScale(value);
      const zero = yScale(0);
      const highlighted = index === spec.highlightIndex;
      if (value > 0) {
        svgEl('path', {
          d: roundedBarPath(x, y, barWidth, zero - y, 4, 'up'),
          fill: colorOf(highlighted ? spec.color || 'series-1' : spec.mutedColor || 'series-muted'), class: 'bar',
        }, svg);
      }
      if (labelIndices.has(index) && value > 0) {
        const label = svgEl('text', { class: 'bar-label', x: cx, y: y - 6, 'text-anchor': 'middle' }, svg);
        label.textContent = spec.display(value);
      }
      const category = spec.categories[index];
      const everyOther = slot < 44 && index % 2 === 1;
      if (!everyOther) {
        const text = svgEl('text', { class: 'axis-text', x: cx, y: margin.top + plotHeight + 16, 'text-anchor': 'middle' }, svg);
        text.textContent = category.label;
      }
      const hit = svgEl('rect', { class: 'bar-hit', x: margin.left + slot * index, y: margin.top, width: slot, height: plotHeight }, svg);
      hit.addEventListener('pointerenter', () => tooltip.show(cx, y, {
        title: category.sub || category.label,
        rows: [{ color: highlighted ? spec.color || 'series-1' : 'series-muted', label: spec.valueLabel || 'Total', value: spec.display(value), square: true },
          ...(spec.extra ? spec.extra(index) : [])],
      }));
      hit.addEventListener('pointerleave', () => tooltip.hide());
    });
  });
  return { destroy: stop };
}

// A tiny trend line for a stat tile. The last point is marked.
export function sparkline(svg, values, { color = 'series-1', muted = 'series-muted' } = {}) {
  svg.replaceChildren();
  const clean = values.filter((value) => value != null);
  if (clean.length < 2) return;
  const width = 100;
  const height = 28;
  const [min, max] = [Math.min(...clean), Math.max(...clean)];
  const xScale = scaleLinear([0, values.length - 1], [2, width - 4]);
  const yScale = scaleLinear([min, max === min ? min + 1 : max], [height - 4, 4]);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  let path = '';
  values.forEach((value, index) => {
    if (value == null) return;
    path += `${path ? 'L' : 'M'}${xScale(index).toFixed(1)},${yScale(value).toFixed(1)}`;
  });
  svgEl('path', { d: path, fill: 'none', stroke: colorOf(muted), 'stroke-width': 1.5, 'vector-effect': 'non-scaling-stroke' }, svg);
  let last = values.length - 1;
  while (last >= 0 && values[last] == null) last -= 1;
  const dot = svgEl('circle', { cx: xScale(last), cy: yScale(values[last]), r: 2.5, fill: colorOf(color) }, svg);
  dot.setAttribute('vector-effect', 'non-scaling-stroke');
}
