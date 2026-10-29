// Building blocks shared by every chart: SVG helpers, scales, tick
// generation, the hover tooltip and container resize tracking.

export const SVG_NS = 'http://www.w3.org/2000/svg';

export function svgEl(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value != null) node.setAttribute(key, value);
  }
  if (parent) parent.appendChild(node);
  return node;
}

export function htmlEl(tag, className = '', parent = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (parent) parent.appendChild(node);
  return node;
}

// Series colors are token names ('series-1') or literal CSS colors.
export function colorOf(color) {
  if (!color) return 'var(--series-1)';
  return color.startsWith('#') || color.startsWith('rgb') || color.startsWith('var(') ? color : `var(--${color})`;
}

// ---- Scales and ticks ----
export function scaleLinear(domain, range) {
  const [domainMin, domainMax] = domain;
  const [rangeMin, rangeMax] = range;
  const span = domainMax - domainMin || 1;
  const scale = (value) => rangeMin + ((value - domainMin) / span) * (rangeMax - rangeMin);
  scale.invert = (pixel) => domainMin + ((pixel - rangeMin) / (rangeMax - rangeMin || 1)) * span;
  scale.domain = domain;
  scale.range = range;
  return scale;
}

export function niceStep(rough) {
  if (!(rough > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(rough));
  const fraction = rough / power;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10;
  return nice * power;
}

export function ticks(min, max, count = 5) {
  if (min === max) return [min];
  const step = niceStep((max - min) / Math.max(1, count));
  const first = Math.ceil(min / step) * step;
  const values = [];
  for (let value = first; value <= max + step / 1000; value += step) {
    values.push(Number(value.toFixed(10)));
  }
  return values;
}

// Expands [min, max] to tick boundaries so marks never touch the frame.
export function niceDomain(min, max, count = 5, includeZero = false) {
  if (includeZero) {
    min = Math.min(min, 0);
    max = Math.max(max, 0);
  }
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const step = niceStep((max - min) / Math.max(1, count));
  return [Math.floor(min / step) * step, Math.ceil(max / step) * step];
}

const DAY_MS = 86400000;

// Tick positions for a time axis in milliseconds: whole days, weeks or months.
export function dateTicks(minTs, maxTs, count = 6) {
  const days = (maxTs - minTs) / DAY_MS;
  const stepDays = [1, 2, 3, 7, 14].find((step) => days / step <= count);
  const result = [];
  if (stepDays) {
    const start = new Date(minTs);
    start.setHours(0, 0, 0, 0);
    if (start.getTime() < minTs) start.setDate(start.getDate() + 1);
    for (let cursor = new Date(start); cursor.getTime() <= maxTs; cursor.setDate(cursor.getDate() + stepDays)) {
      result.push(cursor.getTime());
    }
    return result;
  }
  const monthStep = days / 30 <= count ? 1 : days / 60 <= count ? 2 : 3;
  const cursor = new Date(minTs);
  cursor.setHours(0, 0, 0, 0);
  cursor.setDate(1);
  if (cursor.getTime() < minTs) cursor.setMonth(cursor.getMonth() + 1);
  while (cursor.getTime() <= maxTs) {
    result.push(cursor.getTime());
    cursor.setMonth(cursor.getMonth() + monthStep);
  }
  return result;
}

// Tick positions for an elapsed-time axis in seconds.
export function secondsTicks(maxSeconds, count = 6) {
  const step = [30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200].find((candidate) => maxSeconds / candidate <= count) || 7200;
  const result = [];
  for (let value = 0; value <= maxSeconds; value += step) result.push(value);
  return result;
}

export function fmtTick(value) {
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(Math.abs(value) >= 10000 ? 0 : 1)}K`;
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function fmtDateTick(ts) {
  return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function fmtSecondsTick(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:00`;
  return `${minutes}:00`;
}

export function nearestIndex(sorted, target) {
  let low = 0;
  let high = sorted.length - 1;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (sorted[mid] < target) low = mid + 1;
    else high = mid;
  }
  if (low > 0 && Math.abs(sorted[low - 1] - target) < Math.abs(sorted[low] - target)) return low - 1;
  return low;
}

// A rect with the data end rounded and the baseline end square.
// `direction` is 'up', 'down' or 'right'.
export function roundedBarPath(x, y, width, height, radius, direction) {
  const r = Math.max(0, Math.min(radius, width / 2, height / 2));
  if (height <= 0 || width <= 0) return '';
  if (direction === 'up') {
    return `M${x},${y + height} V${y + r} Q${x},${y} ${x + r},${y} H${x + width - r} Q${x + width},${y} ${x + width},${y + r} V${y + height} Z`;
  }
  if (direction === 'down') {
    const bottom = y + height;
    return `M${x},${y} V${bottom - r} Q${x},${bottom} ${x + r},${bottom} H${x + width - r} Q${x + width},${bottom} ${x + width},${bottom - r} V${y} Z`;
  }
  const right = x + width;
  return `M${x},${y} H${right - r} Q${right},${y} ${right},${y + r} V${y + height - r} Q${right},${y + height} ${right - r},${y + height} H${x} Z`;
}

// ---- Tooltip ----
export class Tooltip {
  constructor(container) {
    this.container = container;
    this.node = htmlEl('div', 'chart-tip', container);
    this.node.setAttribute('role', 'status');
  }

  show(x, y, { title, rows = [], note }) {
    this.node.replaceChildren();
    if (title) {
      const titleEl = htmlEl('div', 'tip-title', this.node);
      titleEl.textContent = title;
    }
    for (const row of rows) {
      const rowEl = htmlEl('div', 'tip-row', this.node);
      const key = htmlEl('span', `tip-key${row.square ? ' square' : ''}`, rowEl);
      key.style.background = colorOf(row.color);
      const label = htmlEl('span', 'tip-label', rowEl);
      label.textContent = row.label;
      const value = htmlEl('span', 'tip-value', rowEl);
      value.textContent = row.value;
    }
    if (note) {
      const noteEl = htmlEl('div', 'tip-note', this.node);
      noteEl.textContent = note;
    }
    this.node.classList.add('show');
    const bounds = this.container.getBoundingClientRect();
    const width = this.node.offsetWidth;
    const height = this.node.offsetHeight;
    let left = x + 14;
    if (left + width > bounds.width - 4) left = x - width - 14;
    let top = y - height / 2;
    top = Math.max(4, Math.min(top, bounds.height - height - 4));
    this.node.style.left = `${Math.max(4, left)}px`;
    this.node.style.top = `${top}px`;
  }

  hide() {
    this.node.classList.remove('show');
  }
}

// Calls render(width) now and again whenever the container's width changes.
export function observeWidth(container, render) {
  let frame = null;
  let lastWidth = -1;
  const run = () => {
    frame = null;
    const width = container.clientWidth;
    if (width > 0 && width !== lastWidth) {
      lastWidth = width;
      render(width);
    }
  };
  const observer = new ResizeObserver(() => {
    if (frame == null) frame = requestAnimationFrame(run);
  });
  observer.observe(container);
  run();
  return () => observer.disconnect();
}

// Pushes overlapping labels apart vertically. Items: [{y, ...}], mutated in place.
export function spreadLabels(items, minGap = 12) {
  items.sort((left, right) => left.y - right.y);
  for (let index = 1; index < items.length; index += 1) {
    if (items[index].y - items[index - 1].y < minGap) items[index].y = items[index - 1].y + minGap;
  }
  return items;
}
