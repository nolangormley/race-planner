// Dashboard, training side: the fitness, fatigue, form and VO2 max tiles,
// the fitness and form chart, weekly volume, heart-rate zones, time by
// type and aerobic efficiency. dashboard.js loads the data and calls these.
import {
  config, units, fmtDate, fmtDuration, fmtMinutes, parseDate, daysUntil, renderEmpty, qs, qsa,
} from '../app.js';
import { panelChart } from '../charts/panels.js';
import { barList, stackedBar, columnChart, sparkline } from '../charts/bars.js';
import { dateTicks, fmtDateTick, fmtTick } from '../charts/core.js';

const charts = {};

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

export function renderTiles(state) {
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

export function renderVo2(state) {
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
function futureDays(state, lastDate) {
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

function pmcSpec(state) {
  const history = state.status.history.slice(-state.pmcDays);
  const last = history[history.length - 1];
  const future = futureDays(state, last.date);
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
    { key: 'projected', label: planned ? 'Planned fitness' : 'Projected fitness', color: 'series-1', values: lead(last.fitness).concat(future.map((day) => day.ctl)), dash: true, endLabel: future.length > 0 },
  ];
  if (planned) {
    series.push({ key: 'projectedAtl', label: 'Planned fatigue', color: 'series-2', values: lead(last.fatigue).concat(future.map((day) => day.atl)), dash: true, endLabel: future.length > 0 });
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

// The empty state replaces the chart, so a later render starts a new one.
export function renderPmcEmpty(message) {
  charts.pmc?.destroy();
  charts.pmc = null;
  renderEmpty(qs('#pmc-chart'), 'No training history yet', message);
}

export function renderPmc(state) {
  const container = qs('#pmc-chart');
  if (!state.status?.history?.length) {
    renderPmcEmpty('Sync Strava to build your fitness curve.');
    return;
  }
  qs('#pmc-sub').textContent = state.projection?.has_plan
    ? 'Daily training load rolled up as fitness (42-day) and fatigue (7-day); form is the gap. The dashed lines follow your planned workouts forward (days with nothing planned count as rest), and race days are marked.'
    : 'Daily training load rolled up as fitness (42-day) and fatigue (7-day). Form is the gap between them. The dashed line projects fitness two weeks ahead.';
  if (charts.pmc) {
    charts.pmc.update(pmcSpec(state));
  } else {
    container.replaceChildren();
    charts.pmc = panelChart(container, pmcSpec(state));
  }
  qsa('#pmc-range button').forEach((button) => {
    button.setAttribute('aria-pressed', String(Number(button.dataset.days) === state.pmcDays));
  });
}

// ---- Weekly volume, HR zones, type share ----
export function renderVolume(state) {
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

export function renderHrZones(state) {
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

export function renderTypeShare(state) {
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

// ---- Aerobic efficiency ----
export function renderEfficiency(data) {
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
  // stretch the axis and flatten the trend, so the axis stops at Tukey's
  // fences, 1.5 interquartile ranges beyond the quartiles, and the chart
  // clips the runs outside them. The table still lists every run.
  const sortedPaces = data.points.map((point) => paceAt(point.ef)).sort((left, right) => left - right);
  const quartile = (share) => sortedPaces[Math.floor((sortedPaces.length - 1) * share)];
  const spread = quartile(0.75) - quartile(0.25);
  const fastest = Math.max(sortedPaces[0], quartile(0.25) - 1.5 * spread);
  const slowest = Math.min(sortedPaces[sortedPaces.length - 1], quartile(0.75) + 1.5 * spread);
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
      kind: 'line', height: 200, reverse: true, yTicks: 4, min: fastest, max: slowest,
      yFormat: (value) => `${fmtMinutes(value)}${units.paceLabel()}`,
      series: [
        { key: 'runs', label: 'Each run', color: 'series-muted', values: data.points.map((point) => paceAt(point.ef)), points: true, width: 0 },
        { key: 'trend', label: '7-run median', color: 'series-1', values: data.points.map((point) => paceAt(point.trend)), endLabel: true },
      ],
    }],
  });
}
