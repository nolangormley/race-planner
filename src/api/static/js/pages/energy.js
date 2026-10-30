// The energy block of the dashboard's recovery card: the level now, what
// moved it since yesterday, and the last week hour by hour with the nights
// shaded and each activity marked where it drained. Data from /api/energy
// (src/energy.py); dashboard.js loads it.
import { parseDate, qs } from '../app.js';
import { panelChart } from '../charts/panels.js';
import { dateTicks } from '../charts/core.js';

const HOUR_MS = 3600000;
const LABELED_DRAIN = 10;           // smaller drains get a line but no label
const LABEL_SPACE_PX = 44;          // container width one label needs, axis margins included

let chart = null;

function clock(value) {
  return parseDate(value).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

// A line at every activity; labels on the largest drains that have room.
function drainMarkers(drains, gap) {
  const labeled = [];
  for (const drain of [...drains].sort((left, right) => right.cost - left.cost)) {
    if (drain.cost >= LABELED_DRAIN && labeled.every((other) => Math.abs(other.from - drain.from) >= gap)) labeled.push(drain);
  }
  return drains.map((drain) => ({ x: drain.from, label: labeled.includes(drain) ? `-${drain.cost}` : '' }));
}

// What happened in the hour that ends at a point: an activity, else the night.
function hourNote(time, drains, nights) {
  const drain = drains.find((item) => item.from < time && Math.max(item.to, item.from + 1) > time - HOUR_MS);
  if (drain) return `${drain.name}, TRIMP ${drain.trimp}: -${drain.cost}`;
  const night = nights.find((item) => item.from < time && item.to > time - HOUR_MS);
  return night ? `Asleep. +${night.charge} that night: ${night.summary}` : null;
}

export function renderEnergy(data) {
  const section = qs('#energy');
  chart?.destroy();
  chart = null;
  section.classList.toggle('hidden', !data?.available);
  if (!data?.available) return;

  const value = qs('#energy-value');
  value.textContent = String(data.current);
  const scale = document.createElement('small');
  scale.textContent = '/ 100';
  value.append(scale);
  qs('#energy-foot').textContent = `Charged to ${data.morning} by ${clock(data.woke_at)}, usual ${data.usual}`;
  qs('#energy-reasons').replaceChildren(...data.reasons.map((reason) => {
    const item = document.createElement('li');
    item.textContent = reason;
    return item;
  }));

  const x = data.series.map((point) => parseDate(point.t).getTime());
  const drains = data.drains.map((drain) => ({ ...drain, from: parseDate(drain.start).getTime(), to: parseDate(drain.end).getTime() }));
  const nights = data.nights.map((night) => ({ ...night, from: parseDate(night.bed).getTime(), to: parseDate(night.wake).getTime() }));
  const container = qs('#energy-chart');
  const msPerPixel = (x[x.length - 1] - x[0]) / Math.max(1, container.clientWidth);
  chart = panelChart(container, {
    x,
    xTicks: (min, max) => dateTicks(min, max, 7),
    xFormat: (ts) => new Date(ts).toLocaleDateString(undefined, { weekday: 'short' }),
    xTitle: (ts) => new Date(ts).toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' }),
    tableXLabel: 'Hour',
    ariaLabel: 'Energy hour by hour over the last week, with nights shaded and activities marked',
    table: true,
    xBands: nights.map((night) => ({ from: night.from, to: night.to })),
    markers: drainMarkers(drains, LABEL_SPACE_PX * msPerPixel),
    note: (index) => hourNote(x[index], drains, nights),
    panels: [{
      kind: 'line', height: 150, min: 0, max: 100, yTicks: 4, yFormat: (level) => String(Math.round(level)),
      series: [{ key: 'energy', label: 'Energy', color: 'series-1', values: data.series.map((point) => point.level), area: true, endLabel: true }],
    }],
  });
}
