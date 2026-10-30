// Dashboard: training status tiles, fitness/form chart, weekly volume, HR
// zones, this week's plan, recent activities, pace zones, time by type.
import {
  config, fetchJson, units, fmtDate, fmtDuration, fmtClock, fmtMinutes, parseDate, isoDate, daysUntil,
  escapeHtml, renderEmpty, qs, qsa,
} from '../app.js';
import { panelChart } from '../charts/panels.js';
import { barList, stackedBar, columnChart, sparkline } from '../charts/bars.js';
import { dateTicks, fmtDateTick, fmtTick } from '../charts/core.js';

const athleteId = config.athleteId;
const userId = config.userId;

const state = {
  status: null,
  analytics: null,
  vo2: null,
  paceZones: null,
  activities: [],
  plan: [],
  predictions: null,
  efficiency: null,
  projection: null,
  records: null,
  recovery: null,
  readiness: null,
  pmcDays: Number(localStorage.getItem('pmcWindow') || 42),
};
const charts = {};

const RUN_TYPES = new Set(['Run', 'Walk', 'Hike', 'TrailRun', 'VirtualRun']);

// Shares of heart rate reserve (resting to max), matching the TRIMP model.
const ZONES = [
  { label: 'Zone 1', sub: 'Recovery · under 60%' },
  { label: 'Zone 2', sub: 'Aerobic · 60–70%' },
  { label: 'Zone 3', sub: 'Tempo · 70–80%' },
  { label: 'Zone 4', sub: 'Threshold · 80–90%' },
  { label: 'Zone 5', sub: 'Anaerobic · over 90%' },
];

function readiness(tsb) {
  if (tsb > 10) return { label: 'Rested', badge: 'good', note: 'Rested: ready to perform' };
  if (tsb > -10) return { label: 'Balanced', badge: 'accent', note: 'Balanced: maintain training' };
  if (tsb > -30) return { label: 'Fatigued', badge: 'warning', note: 'Fatigued: load is building' };
  return { label: 'Overreached', badge: 'critical', note: 'Overreached: consider recovery' };
}

function fitnessStanding(ctl) {
  if (ctl > 100) return 'Elite level';
  if (ctl > 70) return 'Advanced';
  if (ctl > 40) return 'Consistent runner';
  return 'Building';
}

function vo2Standing(vo2) {
  const male = (config.sex || '').toLowerCase().startsWith('m');
  const age = config.dob ? new Date().getFullYear() - new Date(config.dob).getFullYear() : 35;
  const bands = male ? [60, 52, 45] : [54, 46, 39];
  let label = 'Average';
  if (vo2 > bands[0]) label = 'Superior';
  else if (vo2 > bands[1]) label = 'Excellent';
  else if (vo2 > bands[2]) label = 'Good';
  return `${label} for ${male ? 'men' : 'women'} around ${age}`;
}

// ---- Stat tiles ----
function fillTile(key, { value, unit, delta, deltaLabel, deltaGood, spark, foot }) {
  const tile = qs(`#stat-${key}`);
  const valueEl = qs('.stat-value', tile);
  valueEl.textContent = value;
  if (unit) {
    const small = document.createElement('small');
    small.textContent = unit;
    valueEl.appendChild(small);
  }
  const deltaEl = qs('.stat-delta', tile);
  deltaEl.className = 'stat-delta';
  if (delta == null) {
    deltaEl.textContent = deltaLabel || '';
  } else if (Math.abs(delta) < 0.05) {
    deltaEl.textContent = `• unchanged ${deltaLabel}`;
  } else {
    const arrow = delta > 0 ? '▲' : '▼';
    const signed = `${delta > 0 ? '+' : ''}${Math.abs(delta) >= 10 ? Math.round(delta) : delta.toFixed(1)}`;
    deltaEl.textContent = `${arrow} ${signed} ${deltaLabel}`;
    if (deltaGood != null && delta !== 0) deltaEl.classList.add(deltaGood === (delta > 0) ? 'good' : 'bad');
  }
  if (spark) sparkline(qs('.stat-spark svg', tile), spark);
  qs('.stat-foot', tile).replaceChildren(foot || '');
}

function badge(label, kind) {
  const span = document.createElement('span');
  span.className = `badge badge-${kind}`;
  span.textContent = label;
  return span;
}

function renderTiles() {
  const status = state.status;
  const history = status.history;
  const weekAgo = history.length > 7 ? history[history.length - 8] : history[0];
  const latest = history[history.length - 1];
  const spark = (key) => history.slice(-42).map((row) => row[key]);

  fillTile('fitness', {
    value: Math.round(status.fitness_ctl),
    delta: latest.fitness - weekAgo.fitness, deltaLabel: 'vs 7 days ago', deltaGood: true,
    spark: spark('fitness'), foot: fitnessStanding(status.fitness_ctl),
  });
  const acwr = status.acwr;
  fillTile('fatigue', {
    value: Math.round(status.fatigue_atl),
    delta: latest.fatigue - weekAgo.fatigue, deltaLabel: 'vs 7 days ago',
    spark: spark('fatigue'),
    foot: acwr == null ? '' : `Acute:chronic ratio ${acwr.toFixed(2)} · ${acwr > 1.5 ? 'high, injury risk rises' : acwr < 0.8 ? 'low, detraining' : 'in the 0.8–1.3 sweet spot'}`,
  });
  const form = status.form_tsb;
  const ready = readiness(form);
  fillTile('form', {
    value: `${form > 0 ? '+' : ''}${Math.round(form)}`,
    delta: latest.form - weekAgo.form, deltaLabel: 'vs 7 days ago',
    spark: spark('form'), foot: badge(ready.label, ready.badge),
  });
}

function renderVo2() {
  const history = state.vo2?.history || [];
  const latest = state.status?.latest_vo2_max ?? state.vo2?.latest_vo2_max;
  if (latest == null) {
    fillTile('vo2', { value: '–', deltaLabel: 'Needs runs with heart rate', foot: '' });
    return;
  }
  const reference = history.length > 1 ? history[0] : null;
  fillTile('vo2', {
    value: Number(latest).toFixed(1),
    unit: 'ml/kg/min',
    delta: reference ? latest - reference.vo2_max_rolling_7d : null,
    deltaLabel: reference ? `since ${fmtDate(reference.date)}` : 'rolling 7-run average',
    deltaGood: true,
    spark: history.map((row) => row.vo2_max_rolling_7d),
    foot: vo2Standing(latest),
  });
}

// ---- Fitness and form chart ----
// The future comes from the saved plan when there is one (fitness, fatigue
// and form from the planned sessions, race days marked); otherwise the
// server's linear fitness forecast.
function futureDays(lastDate) {
  const projection = state.projection;
  if (projection?.has_plan) {
    const nextRace = projection.races.find((race) => race.date > lastDate);
    // Show through the next race (plus a few days) or four weeks, whichever is longer, up to 90 days.
    const raceDays = nextRace ? daysUntil(nextRace.date) + 3 : 0;
    const horizon = Math.min(90, Math.max(28, raceDays));
    return projection.days.filter((day) => day.date > lastDate).slice(0, horizon)
      .map((day) => ({ date: day.date, ctl: day.ctl, atl: day.atl, tsb: day.tsb, load: day.load, workout: day.workout_type }));
  }
  return state.status.forecast.filter((row) => row.date > lastDate).map((row) => ({ date: row.date, ctl: row.projected_ctl }));
}

function pmcSpec() {
  const history = state.status.history.slice(-state.pmcDays);
  const last = history[history.length - 1];
  const future = futureDays(last.date);
  const planned = Boolean(state.projection?.has_plan);
  const x = [...history, ...future].map((row) => parseDate(row.date).getTime());
  const pad = (values) => values.concat(new Array(future.length).fill(null));
  const lead = (value) => new Array(Math.max(0, history.length - 1)).fill(null).concat([value]);
  const form = pad(history.map((row) => row.form));
  const projectedForm = new Array(history.length).fill(null).concat(future.map((day) => day.tsb ?? null));
  const races = (state.projection?.races || []).filter((race) => race.date > last.date);
  const raceByDate = new Map(races.map((race) => [race.date, race]));
  const series = [
    { key: 'fitness', label: 'Fitness (CTL)', color: 'series-1', values: pad(history.map((row) => row.fitness)), area: true, endLabel: true },
    { key: 'fatigue', label: 'Fatigue (ATL)', color: 'series-2', values: pad(history.map((row) => row.fatigue)), endLabel: true },
    { key: 'projected', label: planned ? 'Planned fitness' : 'Projected fitness', color: 'series-1', values: lead(last.fitness).concat(future.map((day) => day.ctl)), dash: true },
  ];
  if (planned) {
    series.push({ key: 'projectedAtl', label: 'Planned fatigue', color: 'series-2', values: lead(last.fatigue).concat(future.map((day) => day.atl)), dash: true });
  }
  return {
    x,
    xTicks: (min, max) => dateTicks(min, max, 7),
    xFormat: fmtDateTick,
    xTitle: (ts) => new Date(ts).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }),
    tableXLabel: 'Date',
    ariaLabel: 'Fitness, fatigue and form over time',
    legend: true,
    table: true,
    markers: races.map((race) => ({ x: parseDate(race.date).getTime(), label: race.name })),
    note: (index) => {
      const date = [...history, ...future][index].date;
      const race = raceByDate.get(date);
      if (index < history.length) return readiness(form[index]).note;
      const day = future[index - history.length];
      const parts = [race ? `Race day: ${race.name}` : day.workout ? `Planned: ${day.workout}` : planned ? 'Planned: rest' : 'Projection'];
      if (day.tsb != null) parts.push(readiness(day.tsb).note);
      return parts.join(' · ');
    },
    panels: [
      {
        kind: 'line', height: 190, yTicks: 4, includeZero: true, yFormat: (value) => String(Math.round(value)),
        series,
      },
      {
        kind: 'columns', height: 84, title: 'Form (TSB)', yTicks: 3,
        yFormat: (value) => `${value > 0 ? '+' : ''}${Math.round(value)}`,
        series: [
          { key: 'form', label: 'Form (TSB)', color: 'series-1', negativeColor: 'series-neg', values: form },
          ...(planned ? [{ key: 'plannedForm', label: 'Planned form', color: 'series-1', negativeColor: 'series-neg', values: projectedForm, opacity: 0.4 }] : []),
        ],
      },
    ],
  };
}

function renderPmc() {
  const container = qs('#pmc-chart');
  if (!state.status?.history?.length) {
    renderEmpty(container, 'No training history yet', 'Sync Strava to build your fitness curve.');
    return;
  }
  qs('#pmc-sub').textContent = state.projection?.has_plan
    ? 'Daily training load rolled up as fitness (42-day) and fatigue (7-day); form is the gap. The dashed lines follow your planned workouts forward (days with nothing planned count as rest), and race days are marked.'
    : 'Daily training load rolled up as fitness (42-day) and fatigue (7-day). Form is the gap between them. The dashed line projects fitness two weeks ahead.';
  if (charts.pmc) charts.pmc.update(pmcSpec());
  else charts.pmc = panelChart(container, pmcSpec());
  qsa('#pmc-range button').forEach((button) => {
    button.setAttribute('aria-pressed', String(Number(button.dataset.days) === state.pmcDays));
  });
}

// ---- Weekly volume, HR zones, type share ----
function renderVolume() {
  const container = qs('#volume-chart');
  const weeks = state.analytics?.weekly_volume || [];
  charts.volume?.destroy();
  container.replaceChildren();
  if (!weeks.some((week) => week.distance > 0)) {
    renderEmpty(container, 'No runs in the last 12 weeks', 'Weekly distance shows up here after a sync.');
    return;
  }
  const values = weeks.map((week) => units.distanceValue(week.distance));
  const maxIndex = values.indexOf(Math.max(...values));
  charts.volume = columnChart(container, {
    ariaLabel: 'Weekly run volume',
    categories: weeks.map((week) => ({ label: fmtDate(week.week_start), sub: `Week of ${fmtDate(week.week_start, { month: 'long', day: 'numeric' })}` })),
    values,
    display: (value) => `${value.toFixed(1)} ${units.distanceLabel()}`,
    tickFormat: fmtTick,
    valueLabel: 'Distance',
    highlightIndex: weeks.length - 1,
    labelIndices: [maxIndex, weeks.length - 1],
    extra: (index) => [
      { color: 'series-muted', label: 'Runs', value: String(weeks[index].runs), square: true },
      { color: 'series-muted', label: 'Time', value: fmtDuration(weeks[index].moving_time), square: true },
    ],
    height: 220,
  });
}

function renderHrZones() {
  const container = qs('#hr-chart');
  const zones = state.analytics?.hr_zones || [];
  const total = zones.reduce((sum, zone) => sum + zone.time_seconds, 0);
  charts.hr?.destroy();
  container.replaceChildren();
  if (!total) {
    renderEmpty(container, 'No heart rate data in the last 30 days', 'Wear a heart rate monitor and sync again.');
    return;
  }
  const byIndex = ZONES.map((zone, index) => zones.find((row) => row.zone.startsWith(`Zone ${index + 1}`)) || { time_seconds: 0, percentage: 0 });
  if (state.analytics.hr_max) {
    qs('#hr-sub').textContent = `Last 30 days, as a share of heart rate reserve (resting ${state.analytics.hr_rest}, max ${Math.round(state.analytics.hr_max)} bpm).`;
  }
  charts.hr = barList(container, {
    ariaLabel: 'Share of time in each heart rate zone',
    labelWidth: 150,
    rows: byIndex.map((row, index) => ({
      label: ZONES[index].label, sub: ZONES[index].sub, value: row.percentage,
      display: `${Math.round(row.percentage)}%`, detail: `${Math.round(row.percentage)}% · ${fmtDuration(row.time_seconds)}`,
      color: `ramp-${index + 1}`,
    })),
    max: Math.max(50, ...byIndex.map((row) => row.percentage)),
    rowHeight: 38,
  });
}

function renderTypeShare() {
  const container = qs('#type-chart');
  const rows = (state.analytics?.load_dist || []).filter((row) => row.moving_time > 0);
  charts.type?.destroy();
  container.replaceChildren();
  if (!rows.length) {
    renderEmpty(container, 'Nothing in the last 30 days', 'Activity time by sport appears after a sync.');
    return;
  }
  const colors = ['series-1', 'series-2', 'series-3', 'series-4'];
  const segments = rows.slice(0, 4).map((row, index) => ({ label: row.type, value: row.moving_time, color: colors[index], display: fmtDuration(row.moving_time) }));
  const rest = rows.slice(4).reduce((sum, row) => sum + row.moving_time, 0);
  if (rest > 0) segments.push({ label: 'Other', value: rest, color: 'series-muted', display: fmtDuration(rest) });
  charts.type = stackedBar(container, { ariaLabel: 'Share of moving time by activity type', segments, valueLabel: 'Moving time' });
}

// ---- Race predictions ----
function renderPredictions(data) {
  const container = qs('#predictions');
  if (!data) {
    renderEmpty(container, 'No prediction yet', 'Predictions need runs with pace data in the last six months.');
    return;
  }
  qs('#pred-sub').textContent = data.vdot
    ? `From your fastest efforts in the last six months, scaled with Riegel's formula. The faint time is the aerobic ceiling from a VO2 max of ${data.vdot.toFixed(1)}: what the engine allows with race-specific training.`
    : "From your fastest efforts in the last six months, scaled with Riegel's formula.";
  const cell = (row) => `
    <td class="num">${fmtClock(row.seconds)}<span class="cell-sub">${units.pace(row.distance_m / row.seconds)}</span></td>
    <td class="muted small">from your ${escapeHtml(row.from.label)} best, ${fmtClock(row.from.seconds)} on ${fmtDate(row.from.date)}</td>
    <td class="num faint small">${row.ceiling_seconds ? fmtClock(row.ceiling_seconds) : ''}</td>`;
  const races = data.races.filter((race) => race.seconds).map((race) => {
    let verdict = '';
    if (race.goal_seconds) {
      const gap = race.goal_seconds - race.seconds;
      verdict = gap >= 0
        ? `<span class="badge badge-good">${fmtClock(Math.abs(gap))} inside goal</span>`
        : `<span class="badge badge-warning">${fmtClock(Math.abs(gap))} outside goal</span>`;
    }
    return `<tr><td class="primary">${escapeHtml(race.name || race.race_length)}<span class="cell-sub">${escapeHtml(race.race_length)} · ${fmtDate(race.race_date)}${race.goal_time ? ` · goal ${escapeHtml(race.goal_time)}` : ''} ${verdict}</span></td>${cell(race)}</tr>`;
  }).join('');
  const standard = data.predictions.map((row) => `<tr><td class="primary">${escapeHtml(row.label)}</td>${cell(row)}</tr>`).join('');
  container.innerHTML = `<table class="table table-compact"><thead><tr><th>Distance</th><th class="num">Predicted</th><th>Based on</th><th class="num">Ceiling</th></tr></thead><tbody>${races}${standard}</tbody></table>`;
}

// ---- Personal records ----
function renderRecords(data) {
  const container = qs('#records');
  if (!data || !data.all_time.length) {
    renderEmpty(container, 'No records yet', 'Records appear once runs with pace data are synced.');
    return;
  }
  const yearByDistance = new Map(data.this_year.map((row) => [row.distance_m, row]));
  const cell = (row) => (row
    ? `<td class="num"><a href="/workout_streams/${row.activity_id}" class="strong">${fmtClock(row.seconds)}</a><span class="cell-sub">${units.pace(row.distance_m / row.seconds)} · ${fmtDate(row.date, { month: 'short', day: 'numeric', year: 'numeric' })}</span></td>`
    : '<td class="num faint">–</td>');
  const rows = data.all_time.map((row) => {
    const year = yearByDistance.get(row.distance_m);
    const isNew = year && year.seconds === row.seconds;
    return `<tr><td class="primary">${escapeHtml(row.label)}${isNew ? ' <span class="badge badge-good">this year</span>' : ''}</td>${cell(row)}${cell(year)}</tr>`;
  }).join('');
  container.innerHTML = `<table class="table table-compact"><thead><tr><th>Distance</th><th class="num">All time</th><th class="num">${new Date().getFullYear()}</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function loadRecords() {
  state.records = await fetchJson(`/api/records/${athleteId}`).catch(() => null);
  renderRecords(state.records);
}

// ---- Aerobic efficiency ----
function renderEfficiency(data) {
  const container = qs('#efficiency-chart');
  charts.efficiency?.destroy();
  container.replaceChildren();
  if (!data || data.points.length < 3) {
    renderEmpty(container, 'Not enough easy runs yet', 'Steady runs of 20 minutes or more between 60% and 82% of max heart rate show up here.');
    return;
  }
  qs('#eff-sub').textContent = `Pace you would hold at ${data.reference_hr} bpm (middle of zone 2) on each easy run, with a 7-run median. Higher on the chart is faster.`;
  const metersPerUnit = units.isMetric() ? 1000 : 1609.344;
  // Efficiency is meters per minute per beat; at the reference heart rate
  // that is a speed, and a speed is a pace.
  const paceAt = (ef) => metersPerUnit / (ef * data.reference_hr);
  // A couple of odd runs (a hike logged as a run, a watch glitch) would
  // stretch the axis and flatten the trend, so the axis stops at the 2nd
  // and 98th percentiles and the chart clips the rest.
  const sortedPaces = data.points.map((point) => paceAt(point.ef)).sort((left, right) => left - right);
  const percentile = (share) => sortedPaces[Math.min(sortedPaces.length - 1, Math.floor(sortedPaces.length * share))];
  charts.efficiency = panelChart(container, {
    x: data.points.map((point) => parseDate(point.date).getTime()),
    xTicks: (min, max) => dateTicks(min, max, 6),
    xFormat: fmtDateTick,
    xTitle: (ts) => new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }),
    tableXLabel: 'Date',
    ariaLabel: 'Pace at an easy heart rate over time',
    legend: true,
    table: true,
    panels: [{
      kind: 'line', height: 200, reverse: true, yTicks: 4, min: percentile(0.02), max: percentile(0.98),
      yFormat: (value) => `${fmtMinutes(value)}${units.paceLabel()}`,
      series: [
        { key: 'runs', label: 'Each run', color: 'series-muted', values: data.points.map((point) => paceAt(point.ef)), points: true, width: 0 },
        { key: 'trend', label: '7-run median', color: 'series-1', values: data.points.map((point) => paceAt(point.trend)), endLabel: true },
      ],
    }],
  });
}

// ---- Pace zones ----
function renderPaceZones() {
  const container = qs('#pace-zones');
  const speeds = state.paceZones?.pace_zones_speeds;
  if (!speeds) {
    renderEmpty(container, 'Not enough running data', 'Pace zones need a few runs with heart rate.');
    return;
  }
  qs('#pace-sub').textContent = `From your recent runs. Max heart rate used: ${Math.round(state.paceZones.hr_max_used)} bpm.`;
  const entries = Object.entries(speeds);
  const rows = entries.map(([zone, range], index) => {
    let text;
    if (index === 0) text = `slower than ${units.pace(range.max, false)}`;
    else if (index === entries.length - 1) text = `faster than ${units.pace(range.min, false)}`;
    else text = `${units.pace(range.max, false)} – ${units.pace(range.min, false)}`;
    return `
    <tr>
      <td><span class="zone-name"><span class="dot" style="background: var(--ramp-${Math.min(5, index + 1)})"></span>${escapeHtml(zone)}</span></td>
      <td class="num">${text} ${units.paceLabel()}</td>
    </tr>`;
  }).join('');
  container.innerHTML = `<table class="table table-compact zone-table"><thead><tr><th>Zone</th><th class="num">Pace</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// ---- This week ----
// Only runs count toward a planned running session; a ride on the same
// day is shown elsewhere but does not complete it.
function activitiesOn(dateString) {
  return state.activities.filter((activity) => RUN_TYPES.has(activity.type) && activity.start_date_local?.slice(0, 10) === dateString);
}

function renderWeek() {
  const container = qs('#week-plan');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  const rows = [];
  let planned = 0;
  let done = 0;
  for (let offset = 0; offset < 7; offset += 1) {
    const day = new Date(monday);
    day.setDate(monday.getDate() + offset);
    const key = isoDate(day);
    const entry = state.plan.find((item) => item.date === key);
    const runs = activitiesOn(key);
    const actual = runs.reduce((sum, activity) => sum + (activity.distance || 0), 0);
    const isToday = day.getTime() === today.getTime();
    const past = day < today;
    const isRest = entry?.workout_type?.toLowerCase() === 'rest';
    if (entry && !isRest) planned += 1;
    let status = '';
    if (entry && !isRest) {
      if (entry.is_completed || runs.length) {
        status = '<span class="badge badge-good">Done</span>';
        done += 1;
      } else if (past) status = '<span class="badge">Missed</span>';
      else if (isToday) status = '<span class="badge badge-accent">Today</span>';
    } else if (isRest) status = '<span class="badge">Rest</span>';
    const hint = isToday && entry && state.readiness?.hint ? `<span class="hint">${escapeHtml(state.readiness.hint)}</span>` : '';
    const plannedText = entry
      ? `<span class="strong">${escapeHtml(entry.workout_type || 'Workout')}</span>${entry.approximate_distance ? ` <span class="faint">${escapeHtml(entry.approximate_distance)}</span>` : ''}${hint}`
      : '<span class="faint">–</span>';
    const actualText = runs.length
      ? `${units.distance(actual)} <span class="cell-sub">${escapeHtml(runs.map((activity) => activity.type).join(', '))}</span>`
      : '<span class="faint">–</span>';
    rows.push(`<tr class="${isToday ? 'today' : ''}"><td>${day.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' })}</td><td>${plannedText}</td><td>${actualText}</td><td class="status">${status}</td></tr>`);
  }
  if (!state.plan.length) {
    renderEmpty(container, 'No training plan yet', 'Build one in the calendar and it shows up here.', { href: '/training-calendar', label: 'Open calendar' });
    return;
  }
  qs('#week-sub').textContent = `${done} of ${planned} planned workouts done.`;
  container.innerHTML = `<table class="table table-compact week-table"><thead><tr><th>Day</th><th>Planned</th><th>Actual</th><th></th></tr></thead><tbody>${rows.join('')}</tbody></table>`;
}

// ---- Recent activities ----
function renderRecent() {
  const container = qs('#recent');
  const recent = state.activities.slice(0, 6);
  if (!recent.length) {
    renderEmpty(container, 'No activities yet', 'Sync Strava to pull in your runs.');
    return;
  }
  const rows = recent.map((activity) => `
    <tr class="link" data-href="/workout_streams/${activity.activity_id}" tabindex="0">
      <td class="nowrap">${fmtDate(activity.start_date_local)}</td>
      <td class="primary"><span class="truncate" style="display:block;max-width:200px">${escapeHtml(activity.name)}</span><span class="cell-sub">${escapeHtml(activity.type)}</span></td>
      <td class="num">${units.distance(activity.distance)}</td>
      <td class="num">${activity.type === 'Run' ? units.pace(activity.average_speed, false) : fmtDuration(activity.moving_time)}</td>
      <td class="num">${activity.average_heartrate ? Math.round(activity.average_heartrate) : '–'}</td>
      <td class="num">${activity.training_load ? Math.round(activity.training_load) : '–'}</td>
    </tr>`).join('');
  container.innerHTML = `<div class="table-wrap"><table class="table table-compact"><thead><tr><th>Date</th><th>Activity</th><th class="num">Dist</th><th class="num">Pace / time</th><th class="num">HR</th><th class="num">Load</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  qsa('tr.link', container).forEach((row) => {
    const go = () => { window.location.href = row.dataset.href; };
    row.addEventListener('click', go);
    row.addEventListener('keydown', (event) => { if (event.key === 'Enter') go(); });
  });
}

// ---- Header, coach's note ----
function renderHeader(races) {
  const sub = qs('#dash-sub');
  const next = races.map((race) => ({ ...race, days: daysUntil(race.race_date) })).filter((race) => race.days >= 0)[0];
  if (!next) {
    sub.innerHTML = 'No upcoming race. <a href="/profile">Add one</a> to train toward a date.';
    return;
  }
  const when = next.days === 0 ? 'today' : next.days === 1 ? 'tomorrow' : `in ${next.days} days`;
  sub.innerHTML = `Training for <span class="strong">${escapeHtml(next.name || next.race_length)}</span><span class="sep">·</span>${escapeHtml(next.race_length)}<span class="sep">·</span>${fmtDate(next.race_date, { month: 'long', day: 'numeric' })}, ${when}${next.goal_time ? `<span class="sep">·</span>goal ${escapeHtml(next.goal_time)}` : ''}<span class="sep">·</span><a href="/races/${next.id}/pacing">Pacing plan</a>`;
}

function renderNote() {
  const note = state.status?.ai_insight;
  const card = qs('#ai-card');
  if (!note || /api key|not set|error/i.test(note)) {
    card.classList.add('hidden');
    return;
  }
  const html = [];
  let list = [];
  const flush = () => { if (list.length) { html.push(`<ul>${list.join('')}</ul>`); list = []; } };
  for (const raw of note.split('\n')) {
    const line = raw.replace(/[*#]/g, '').trim();
    if (!line) continue;
    if (/^-\s+/.test(line)) list.push(`<li>${escapeHtml(line.replace(/^-\s+/, ''))}</li>`);
    else { flush(); html.push(`<p>${escapeHtml(line)}</p>`); }
  }
  flush();
  qs('#ai-note').innerHTML = html.join('');
  card.classList.remove('hidden');
}

// ---- Loading ----
async function loadStatus() {
  try {
    state.status = await fetchJson(`/status/${athleteId}`);
  } catch (error) {
    state.status = { history: [], forecast: [] };
    qsa('#stats .stat-value').forEach((node) => { node.textContent = '–'; });
    renderEmpty(qs('#pmc-chart'), 'No training history yet', error.status === 404 ? 'Sync Strava to build your fitness curve.' : error.message);
    return;
  }
  renderTiles();
  renderPmc();
  renderNote();
}

async function loadAnalytics() {
  try {
    state.analytics = await fetchJson(`/analytics/${athleteId}`);
  } catch {
    state.analytics = null;
  }
  renderVolume();
  renderHrZones();
  renderTypeShare();
}

async function loadVo2() {
  try {
    state.vo2 = await fetchJson(`/vo2max/${athleteId}`);
  } catch {
    state.vo2 = null;
  }
  renderVo2();
}

async function loadPaceZones() {
  try {
    state.paceZones = await fetchJson(`/pace_zones/${athleteId}`);
  } catch {
    state.paceZones = null;
  }
  renderPaceZones();
}

async function loadPlanAndActivities() {
  const [plan, activities] = await Promise.all([
    fetchJson(`/api/training-plan/${userId}`).catch(() => ({ entries: [] })),
    fetchJson('/api/activities?limit=40').catch(() => []),
  ]);
  state.plan = plan.entries || [];
  state.activities = Array.isArray(activities) ? activities : [];
  renderWeek();
  renderRecent();
}

async function loadProjection() {
  state.projection = await fetchJson(`/api/projection/${userId}`).catch(() => null);
  if (state.status?.history?.length) renderPmc();
}

async function loadPredictions() {
  state.predictions = await fetchJson(`/api/predictions/${athleteId}`).catch(() => null);
  renderPredictions(state.predictions);
}

// ---- Recovery (COROS) ----
function hoursMinutes(minutes) {
  if (minutes == null) return '–';
  return `${Math.floor(minutes / 60)}h ${String(Math.round(minutes % 60)).padStart(2, '0')}m`;
}

function recoveryTile(label, value, unit, foot) {
  const node = document.createElement('div');
  node.className = 'recovery-tile';
  node.innerHTML = `<div class="stat-label">${label}</div><div class="stat-value">${value}${unit ? `<small>${unit}</small>` : ''}</div><div class="stat-foot">${foot}</div>`;
  return node;
}

function renderRecovery(data) {
  const container = qs('#recovery-chart');
  const tiles = qs('#recovery-tiles');
  charts.recovery?.destroy();
  container.replaceChildren();
  tiles.replaceChildren();
  if (!data || !data.days.length) {
    renderEmpty(container, 'No COROS data yet', 'Connect COROS on your profile to see overnight HRV, resting heart rate and time in bed here.');
    return;
  }
  const latest = data.latest;
  tiles.append(
    recoveryTile('Overnight HRV', latest.hrv ?? '–', 'ms', latest.hrv_baseline ? `baseline ${latest.hrv_baseline} ms` : ''),
    recoveryTile('Resting heart rate', latest.rhr ?? '–', 'bpm', data.resting_hr_7d ? `7-night average ${data.resting_hr_7d} bpm` : ''),
  );
  if (latest.time_in_bed_min || data.sleep_7d_min) {
    tiles.append(recoveryTile('Time in bed', hoursMinutes(latest.time_in_bed_min), '', data.sleep_7d_min ? `7-night average ${hoursMinutes(data.sleep_7d_min)}` : ''));
  }
  qs('#recovery-sub').textContent = `The last ${data.days.length} nights from COROS. HRV at or above its baseline with a steady resting heart rate means you are absorbing the training.`;
  charts.recovery = panelChart(container, {
    x: data.days.map((night) => parseDate(night.day).getTime()),
    xTicks: (min, max) => dateTicks(min, max, 6),
    xFormat: fmtDateTick,
    xTitle: (ts) => new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }),
    tableXLabel: 'Night',
    ariaLabel: 'Overnight HRV and resting heart rate by night',
    legend: true,
    table: true,
    panels: [
      {
        kind: 'line', height: 140, yTicks: 3, yFormat: (value) => `${Math.round(value)} ms`,
        series: [
          { key: 'hrv', label: 'HRV', color: 'series-1', values: data.days.map((night) => night.hrv), points: true },
          { key: 'baseline', label: 'Baseline', color: 'series-muted', values: data.days.map((night) => night.hrv_baseline), dash: true },
        ],
      },
      {
        kind: 'line', height: 110, yTicks: 3, yFormat: (value) => `${Math.round(value)} bpm`,
        series: [{ key: 'rhr', label: 'Resting heart rate', color: 'series-2', values: data.days.map((night) => night.rhr), points: true }],
      },
    ],
  });
}

function renderReadiness() {
  const container = qs('#readiness');
  const call = state.readiness;
  container.replaceChildren();
  if (!call?.available) return;
  const badgeNode = document.createElement('span');
  badgeNode.className = `badge badge-${call.badge}`;
  badgeNode.textContent = `Today: ${call.label}`;
  badgeNode.title = call.advice;
  const reasons = document.createElement('div');
  reasons.className = 'small faint';
  reasons.textContent = call.reasons.length ? call.reasons.join(' · ') : call.advice;
  container.append(badgeNode, reasons);
  if (state.plan.length) renderWeek();
}

async function loadReadiness() {
  if (!qs('#readiness')) return;
  state.readiness = await fetchJson(`/api/readiness/${userId}`).catch(() => null);
  renderReadiness();
}

async function loadRecovery() {
  // The card is left out when COROS is switched off (COROS_ENABLED=false).
  const card = qs('#recovery-card');
  if (!card) return;
  try {
    state.recovery = await fetchJson(`/api/recovery/${userId}?days=42`);
  } catch (error) {
    state.recovery = null;
    if (error.status === 404) {
      card.classList.add('hidden');
      return;
    }
  }
  renderRecovery(state.recovery);
}

async function loadEfficiency() {
  state.efficiency = await fetchJson(`/api/efficiency/${athleteId}?days=180`).catch(() => null);
  renderEfficiency(state.efficiency);
}

function loadAll() {
  loadStatus().then(loadVo2).then(loadProjection);
  loadAnalytics();
  loadPaceZones();
  loadPlanAndActivities();
  loadPredictions();
  loadEfficiency();
  loadRecords();
  loadRecovery();
  loadReadiness();
  fetchJson(`/api/races/${userId}`).then(renderHeader).catch(() => renderHeader([]));
}

function rerenderUnits() {
  qsa('#units-toggle button').forEach((button) => {
    button.setAttribute('aria-pressed', String((button.dataset.units === 'metric') === units.isMetric()));
  });
  if (state.analytics) renderVolume();
  if (state.paceZones) renderPaceZones();
  if (state.predictions) renderPredictions(state.predictions);
  if (state.efficiency) renderEfficiency(state.efficiency);
  if (state.records) renderRecords(state.records);
  if (state.activities.length) {
    renderWeek();
    renderRecent();
  }
}

qsa('#units-toggle button').forEach((button) => {
  button.addEventListener('click', () => {
    units.set(button.dataset.units);
    rerenderUnits();
  });
});
qsa('#pmc-range button').forEach((button) => {
  button.addEventListener('click', () => {
    state.pmcDays = Number(button.dataset.days);
    localStorage.setItem('pmcWindow', state.pmcDays);
    if (state.status?.history?.length) renderPmc();
  });
});
document.addEventListener('strava:synced', loadAll);

rerenderUnits();
loadAll();
