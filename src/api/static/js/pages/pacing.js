// Race-day pacing: goal and strategy controls, the course profile with split
// markers, the splits table and a printable pace band.
import { fetchJson, units, fmtClock, qs, qsa, escapeHtml, showToast, renderEmpty } from '../app.js';
import { panelChart } from '../charts/panels.js';

const root = qs('#pacing');
const raceId = root.dataset.raceId;
// defaultGoal is the goal the server chose (race goal or prediction); the
// field only counts as typed when it differs from it.
const state = { strategy: 'even', plan: null, chart: null, defaultGoal: null };
const TICK_STRIDES = [1, 2, 5, 10, 20];

const GOAL_SOURCES = {
  query: 'Goal as typed. Leave the field empty to go back to the race goal.',
  race: 'Goal from your race. Type another time to see what it takes.',
  prediction: 'No goal on this race, so this is your predicted time from recent best efforts. Type a goal or set one on the race.',
};

function isPartial(split) {
  return split.length_m < state.plan.split_m - 1;
}

function splitName(split) {
  if (!isPartial(split)) return String(split.index);
  return `Finish<span class="cell-sub">${(split.end_m / state.plan.split_m).toFixed(2)} ${units.distanceLabel()}</span>`;
}

function paceText(secondsPerKm, secondsPerMile, withUnit = true) {
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

// ---- Rendering ----
function renderSummary() {
  const summary = qs('#pacing-summary');
  const plan = state.plan;
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
    tile('Course', course, plan.course ? `${units.elevation(plan.course.descent_m)} down` : 'upload a GPX to pace the hills'),
  ].join('');
  qs('#goal-source').textContent = GOAL_SOURCES[plan.goal_source] || '';
}

function renderCourseControls() {
  const plan = state.plan;
  const hasCourse = Boolean(plan?.course);
  qs('#gpx-remove').classList.toggle('hidden', !hasCourse);
  qs('#gpx-upload').textContent = hasCourse ? 'Replace GPX' : 'Upload GPX';
  const note = qs('#course-note');
  if (plan?.warnings?.length) note.textContent = plan.warnings.join(' ');
  else if (hasCourse) note.textContent = 'Elevation along the course, with each split marked. Pace is adjusted so the effort stays level over the hills.';
  else note.textContent = 'Elevation along the course, with each split marked. Upload the race GPX and the splits follow the hills at one effort.';
}

function renderProfile() {
  const container = qs('#profile-chart');
  state.chart?.destroy();
  state.chart = null;
  container.replaceChildren();
  const plan = state.plan;
  if (!plan || !plan.profile.length) {
    renderEmpty(container, 'No course yet', 'Upload the race GPX to see the elevation profile and let the splits follow the hills.');
    return;
  }
  const unitM = plan.split_m;
  const label = units.distanceLabel();
  const markerStride = stride(plan.splits.length, 20);
  const points = [{ distance_m: 0, elevation_m: plan.profile[0].elevation_m }, ...plan.profile];
  state.chart = panelChart(container, {
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

function renderSplits() {
  const table = qs('#splits-table');
  const body = qs('tbody', table);
  const plan = state.plan;
  qs('#split-head').textContent = units.isMetric() ? 'Km' : 'Mile';
  qs('#pace-unit').textContent = units.paceLabel();
  if (!plan) {
    body.innerHTML = '';
    return;
  }
  table.classList.toggle('no-grade', !plan.profile.length);
  body.innerHTML = plan.splits.map((split) => `<tr>
    <td class="primary">${splitName(split)}</td>
    ${gradeCell(split)}
    <td class="num">${paceText(split.seconds_per_km, split.seconds_per_mile, false)}</td>
    <td class="num col-split">${fmtClock(split.split_seconds)}</td>
    <td class="num strong">${fmtClock(split.cumulative_seconds)}</td>
  </tr>`).join('');
  qs('#splits-sub').textContent = plan.profile.length
    ? 'Target pace for each split with its average grade, and the clock time you should see at its end.'
    : 'Target pace for each split and the clock time you should see at its end.';
}

function renderBand() {
  const band = qs('#pace-band');
  const plan = state.plan;
  if (!plan) {
    band.innerHTML = '';
    return;
  }
  const rows = plan.splits.map((split) => `<tr><td>${isPartial(split) ? 'Finish' : split.index}</td><td>${fmtClock(split.cumulative_seconds)}</td></tr>`).join('');
  band.innerHTML = `<div class="band-head"><strong>${escapeHtml(plan.race.name || plan.race.race_length)}</strong>`
    + `<span>${plan.goal} · ${plan.strategy === 'negative' ? 'negative split' : 'even effort'} · ${units.distanceLabel()} splits</span></div>`
    + `<table>${rows}</table>`;
}

function renderAll() {
  renderSummary();
  renderCourseControls();
  renderProfile();
  renderSplits();
  renderBand();
  qsa('#units-toggle button').forEach((button) => {
    button.setAttribute('aria-pressed', String((button.dataset.units === 'metric') === units.isMetric()));
  });
  qsa('#strategy button').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.strategy === state.strategy));
  });
}

// ---- Data ----
async function loadPlan() {
  const goal = qs('#goal').value.trim();
  const params = new URLSearchParams({ strategy: state.strategy, unit: units.isMetric() ? 'km' : 'mi' });
  if (goal && goal !== state.defaultGoal) params.set('goal', goal);
  const status = qs('#pacing-status');
  try {
    state.plan = await fetchJson(`/api/races/${raceId}/pacing?${params}`);
    status.classList.add('hidden');
    if (state.plan.goal_source !== 'query') {
      state.defaultGoal = state.plan.goal;
      qs('#goal').value = state.plan.goal;
    }
  } catch (error) {
    state.plan = null;
    status.textContent = error.message;
    status.classList.remove('hidden');
  }
  renderAll();
}

async function uploadCourse(file) {
  const body = new FormData();
  body.append('file', file);
  const response = await fetch(`/api/races/${raceId}/course`, { method: 'POST', body });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail || `Upload failed (${response.status})`);
  return payload;
}

// ---- Events ----
qs('#pacing-form').addEventListener('submit', (event) => {
  event.preventDefault();
  loadPlan();
});
qsa('#strategy button').forEach((button) => {
  button.addEventListener('click', () => {
    state.strategy = button.dataset.strategy;
    loadPlan();
  });
});
qsa('#units-toggle button').forEach((button) => {
  button.addEventListener('click', () => {
    units.set(button.dataset.units);
    loadPlan();
  });
});
qs('#gpx-upload').addEventListener('click', () => qs('#gpx-file').click());
qs('#gpx-file').addEventListener('change', async (event) => {
  const [file] = event.target.files;
  if (!file) return;
  try {
    const result = await uploadCourse(file);
    showToast(`Course saved: ${units.distance(result.course.length_m, 1)}, ${units.elevation(result.course.ascent_m)} of climbing`);
    loadPlan();
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    event.target.value = '';
  }
});
qs('#gpx-remove').addEventListener('click', async () => {
  try {
    await fetchJson(`/api/races/${raceId}/course`, { method: 'DELETE' });
    showToast('Course removed');
    loadPlan();
  } catch (error) {
    showToast(error.message, 'error');
  }
});
qs('#print-band').addEventListener('click', () => window.print());

loadPlan();
