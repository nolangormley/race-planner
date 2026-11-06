// A split plan as the pacing pages draw it: the summary tiles, the course profile with split markers, the
// splits table and the printable pace band. The race pacing page (pages/pacing.js) and the public course
// page (pages/course.js) render the same plan payload into the same element ids.
import { units, fmtClock, qs, escapeHtml, renderEmpty } from './app.js';
import { panelChart } from './charts/panels.js';

const TICK_STRIDES = [1, 2, 5, 10, 20];
let chart = null;

function isPartial(plan, split) {
  return split.length_m < plan.split_m - 1;
}

function splitName(plan, split) {
  if (!isPartial(plan, split)) return String(split.index);
  return `Finish<span class="cell-sub">${(split.end_m / plan.split_m).toFixed(2)} ${units.distanceLabel()}</span>`;
}

export function paceText(secondsPerKm, secondsPerMile, withUnit = true) {
  return `${fmtClock(units.isMetric() ? secondsPerKm : secondsPerMile)}${withUnit ? units.paceLabel() : ''}`;
}

function gradeCell(split) {
  // Rounding first keeps a slight downhill from printing as "-0.0%".
  const percent = Math.round(split.grade * 1000) / 10;
  return `<td class="num col-grade">${percent > 0 ? '+' : ''}${(percent || 0).toFixed(1)}%</td>`;
}

// The smallest stride from TICK_STRIDES that keeps count labels at least
// gapPx apart on the chart's current width.
function stride(count, gapPx) {
  const plotWidth = Math.max(160, qs('#profile-chart').clientWidth - 70);
  return TICK_STRIDES.find((step) => (count / step) * gapPx <= plotWidth) || TICK_STRIDES[TICK_STRIDES.length - 1];
}

export function renderSummary(plan) {
  const summary = qs('#pacing-summary');
  if (!plan) {
    summary.replaceChildren();
    return;
  }
  const course = plan.course
    ? `${units.distance(plan.course.length_m, 1)} · ${units.elevation(plan.course.ascent_m)} up`
    : 'Flat (no course file)';
  const tile = (label, value, foot) => `<div><div class="stat-label">${label}</div><div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`;
  summary.innerHTML = [
    tile('Goal', plan.goal, `${plan.splits.length} splits`),
    tile('Average pace', paceText(plan.average_seconds_per_km, plan.average_seconds_per_mile), `${units.distance(plan.distance_m, 1)} in ${plan.goal}`),
    tile('Strategy', plan.strategy === 'negative' ? 'Negative split' : 'Even effort', plan.strategy === 'negative' ? 'second half about 2% faster' : 'one effort start to finish'),
    tile('Course', course, plan.course ? `${units.elevation(plan.course.descent_m)} down` : 'find or upload one to pace the hills'),
  ].join('');
}

// empty is the { title, message } shown when the plan has no course profile.
export function renderProfile(plan, empty) {
  const container = qs('#profile-chart');
  chart?.destroy();
  chart = null;
  container.replaceChildren();
  if (!plan || !plan.profile.length) {
    renderEmpty(container, empty.title, empty.message);
    return;
  }
  const unitM = plan.split_m;
  const label = units.distanceLabel();
  const markerStride = stride(plan.splits.length, 20);
  const points = [{ distance_m: 0, elevation_m: plan.profile[0].elevation_m }, ...plan.profile];
  chart = panelChart(container, {
    x: points.map((point) => point.distance_m),
    // Runs on every redraw, so the ticks thin out when the chart narrows.
    xTicks: (min, max) => {
      const step = unitM * stride(Math.floor(max / unitM) + 1, 48);
      const ticks = [];
      for (let value = 0; value <= max + 1; value += step) ticks.push(value);
      return ticks;
    },
    xFormat: (value) => `${Math.round(value / unitM)} ${label}`,
    xTitle: (value) => `${(value / unitM).toFixed(2)} ${label}`,
    tableXLabel: 'Distance',
    ariaLabel: 'Course elevation by distance, with split boundaries marked',
    legend: false,
    table: true,
    markers: plan.splits.slice(0, -1).map((split) => ({ x: split.end_m, label: split.index % markerStride === 0 ? String(split.index) : '' })),
    panels: [{
      kind: 'line', height: 180, yTicks: 4, yFormat: (value) => units.elevation(value),
      series: [{ key: 'elevation', label: 'Elevation', color: 'series-3', values: points.map((point) => point.elevation_m), area: true }],
    }],
  });
}

export function renderSplits(plan) {
  const table = qs('#splits-table');
  const body = qs('tbody', table);
  qs('#split-head').textContent = units.isMetric() ? 'Km' : 'Mile';
  qs('#pace-unit').textContent = units.paceLabel();
  if (!plan) {
    body.innerHTML = '';
    return;
  }
  table.classList.toggle('no-grade', !plan.profile.length);
  body.innerHTML = plan.splits.map((split) => `<tr>
    <td class="primary">${splitName(plan, split)}</td>
    ${gradeCell(split)}
    <td class="num">${paceText(split.seconds_per_km, split.seconds_per_mile, false)}</td>
    <td class="num col-split">${fmtClock(split.split_seconds)}</td>
    <td class="num strong">${fmtClock(split.cumulative_seconds)}</td>
  </tr>`).join('');
  qs('#splits-sub').textContent = plan.profile.length
    ? 'Target pace for each split with its average grade, and the clock time you should see at its end.'
    : 'Target pace for each split and the clock time you should see at its end.';
}

export function renderBand(plan) {
  const band = qs('#pace-band');
  if (!plan) {
    band.innerHTML = '';
    return;
  }
  const rows = plan.splits.map((split) => `<tr><td>${isPartial(plan, split) ? 'Finish' : split.index}</td><td>${fmtClock(split.cumulative_seconds)}</td></tr>`).join('');
  band.innerHTML = `<div class="band-head"><strong>${escapeHtml(plan.race.name || plan.race.race_length)}</strong>`
    + `<span>${plan.goal} · ${plan.strategy === 'negative' ? 'negative split' : 'even effort'} · ${units.distanceLabel()} splits</span></div>`
    + `<table>${rows}</table>`;
}
