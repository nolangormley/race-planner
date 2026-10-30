// Activity detail: summary strip, linked stream panels with pace-zone
// shading, detected intervals or tempo, laps, effort metrics, time in zones
// and the optional coach's note.
import { config, fetchJson, units, fmtDuration, fmtClock, fmtMinutes, escapeHtml, renderEmpty, qs } from '../app.js';
import { panelChart } from '../charts/panels.js';
import { barList } from '../charts/bars.js';
import { secondsTicks, fmtSecondsTick } from '../charts/core.js';

const activityId = qs('#activity').dataset.activityId;
const ZONE_NAMES = ['Recovery', 'Aerobic', 'Tempo', 'Threshold', 'Anaerobic'];
// Shares of heart rate reserve (resting to max), the same model as the dashboard.
const ZONE_RANGES = ['< 60%', '60–70%', '70–80%', '80–90%', '> 90%'];

function setText(id, text) {
  qs(`#${id}`).textContent = text;
}

function withUnit(value, unit) {
  const node = document.createElement('span');
  node.textContent = value;
  const small = document.createElement('small');
  small.textContent = unit;
  node.appendChild(small);
  return node;
}

function renderSummary(activity) {
  setText('act-name', activity.name || 'Unnamed activity');
  const when = activity.start_date_local
    ? new Date(activity.start_date_local).toLocaleString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
    : '';
  qs('#act-sub').innerHTML = `<span class="badge badge-accent">${escapeHtml(activity.type || 'Activity')}</span><span class="sep">·</span>${escapeHtml(when)}${activity.device_name ? `<span class="sep">·</span>${escapeHtml(activity.device_name)}` : ''}`;
  document.title = `${activity.name || 'Activity'} · Race Planner`;

  const isRun = ['Run', 'Walk', 'Hike', 'TrailRun'].includes(activity.type);
  qs('#stat-distance').replaceChildren(withUnit(units.distanceValue(activity.distance || 0).toFixed(2), units.distanceLabel()));
  setText('stat-time', fmtDuration(activity.moving_time));
  qs('#stat-pace').replaceChildren(isRun ? withUnit(units.pace(activity.average_speed, false), units.paceLabel()) : withUnit(((activity.average_speed || 0) * (units.isMetric() ? 3.6 : 2.23694)).toFixed(1), units.isMetric() ? 'km/h' : 'mph'));
  qs('#stat-hr').replaceChildren(activity.average_heartrate ? withUnit(String(Math.round(activity.average_heartrate)), 'bpm') : '–');
  qs('#stat-hrmax').replaceChildren(activity.max_heartrate ? withUnit(String(Math.round(activity.max_heartrate)), 'bpm') : '–');
  qs('#stat-cadence').replaceChildren(activity.average_cadence ? withUnit(String(Math.round(activity.average_cadence * 2)), 'spm') : '–');
  qs('#stat-elevation').replaceChildren(withUnit(String(Math.round(units.isMetric() ? activity.total_elevation_gain || 0 : (activity.total_elevation_gain || 0) * 3.28084)), units.elevationLabel()));
  qs('#stat-calories').replaceChildren(activity.calories ? withUnit(String(Math.round(activity.calories)), 'kcal') : '–');
}

function renderEffort(activity) {
  const container = qs('#effort');
  if (activity.trimp_banister == null) {
    renderEmpty(container, 'Not computed yet', 'Effort metrics need a heart rate stream and a sync.');
    return;
  }
  const zones = [1, 2, 3, 4, 5].map((index) => activity[`zone_${index}_sec`] || 0);
  const top = zones.indexOf(Math.max(...zones));
  const decoupling = activity.aerobic_decoupling;
  let decouplingBadge = '';
  if (decoupling != null) {
    const kind = decoupling < 5 ? 'good' : decoupling <= 10 ? 'warning' : 'serious';
    const word = decoupling < 5 ? 'strong aerobic base' : decoupling <= 10 ? 'some drift' : 'high drift';
    decouplingBadge = `<span class="badge badge-${kind}">${word}</span>`;
  }
  container.innerHTML = `
    <div class="effort-hero">
      <div><div class="value">${Math.round(activity.trimp_banister)}</div><div class="label">Training load (TRIMP)</div></div>
      <span class="badge" style="color: var(--text)">Mostly ${escapeHtml(ZONE_NAMES[top])}</span>
    </div>
    <dl class="kv">
      <dt>Intensity</dt><dd>${Math.round((activity.intensity_factor || 0) * 100)}% of max HR</dd>
      <dt>Efficiency factor</dt><dd>${activity.efficiency_factor ? activity.efficiency_factor.toFixed(2) : '–'}</dd>
      <dt>Aerobic decoupling</dt><dd>${decoupling == null ? '–' : `${decoupling.toFixed(1)}% ${decouplingBadge}`}</dd>
    </dl>`;
  qs('#effort-sub').textContent = 'Load is Banister TRIMP. Decoupling compares pace-to-heart-rate in the second half against the first; under 5% is good.';
}

function renderZones(activity) {
  const container = qs('#zones-chart');
  const zones = [1, 2, 3, 4, 5].map((index) => activity[`zone_${index}_sec`] || 0);
  const total = zones.reduce((sum, value) => sum + value, 0);
  if (!total) {
    renderEmpty(container, 'No heart rate stream', 'Zones need per-second heart rate.');
    return;
  }
  barList(container, {
    ariaLabel: 'Time in each heart rate zone',
    labelWidth: 132,
    rowHeight: 36,
    rows: zones.map((seconds, index) => ({
      label: `Zone ${index + 1}`, sub: `${ZONE_NAMES[index]} · ${ZONE_RANGES[index]}`,
      value: (seconds / total) * 100, display: `${Math.round((seconds / total) * 100)}%`,
      detail: `${fmtDuration(seconds)} · ${Math.round((seconds / total) * 100)}%`, color: `ramp-${index + 1}`,
    })),
    max: Math.max(50, ...zones.map((seconds) => (seconds / total) * 100)),
  });
}

function smooth(values, window) {
  const half = Math.floor(window / 2);
  return values.map((value, index) => {
    if (value == null) return null;
    let sum = 0;
    let count = 0;
    for (let offset = -half; offset <= half; offset += 1) {
      const sample = values[index + offset];
      if (sample != null) {
        sum += sample;
        count += 1;
      }
    }
    return count ? sum / count : null;
  });
}

function paceBands(paceZones, paceValues) {
  if (!paceZones?.pace_zones_speeds) return [];
  const clean = paceValues.filter((value) => value != null);
  if (!clean.length) return [];
  const dataMin = Math.min(...clean) * 0.97;
  const dataMax = Math.max(...clean) * 1.03;
  const bands = [];
  Object.entries(paceZones.pace_zones_speeds).forEach(([zone, range], index) => {
    const fast = range.max ? units.paceMinutes(range.max) : dataMin;
    const slow = range.min ? units.paceMinutes(range.min) : dataMax;
    const from = Math.max(dataMin, fast);
    const to = Math.min(dataMax, slow);
    if (to > from) bands.push({ from, to, color: `ramp-${Math.min(5, index + 1)}`, label: zone });
  });
  return bands;
}

function renderSplits(laps, activity) {
  if (!laps.length) return;
  const card = qs('#splits-card');
  const isRun = ['Run', 'Walk', 'Hike', 'TrailRun'].includes(activity.type);
  const fastest = laps.reduce((best, lap) => ((lap.average_speed || 0) > (best.average_speed || 0) ? lap : best), laps[0]);
  const average = activity.average_speed || 0;
  const rows = laps.map((lap) => {
    const delta = average && lap.average_speed ? units.paceMinutes(lap.average_speed) - units.paceMinutes(average) : null;
    const deltaText = delta == null || Math.abs(delta) < 0.01 ? '' : `<span class="cell-sub ${delta < 0 ? 'faint' : 'faint'}">${delta < 0 ? '−' : '+'}${fmtMinutes(Math.abs(delta))} vs avg</span>`;
    return `<tr>
      <td class="num">${lap.lap_index}${lap === fastest && laps.length > 1 ? ' <span class="badge badge-good">fastest</span>' : ''}</td>
      <td class="num">${units.distance(lap.distance, 2)}</td>
      <td class="num">${fmtClock(lap.moving_time ?? lap.elapsed_time ?? 0)}</td>
      <td class="num">${isRun ? units.pace(lap.average_speed) : `${((lap.average_speed || 0) * (units.isMetric() ? 3.6 : 2.23694)).toFixed(1)} ${units.isMetric() ? 'km/h' : 'mph'}`}${deltaText}</td>
      <td class="num">${lap.average_heartrate ? Math.round(lap.average_heartrate) : '–'}</td>
      <td class="num">${lap.max_heartrate ? Math.round(lap.max_heartrate) : '–'}</td>
      <td class="num">${lap.average_cadence ? Math.round(lap.average_cadence * 2) : '–'}</td>
      <td class="num">${lap.total_elevation_gain != null ? units.elevation(lap.total_elevation_gain) : '–'}</td>
    </tr>`;
  }).join('');
  qs('#splits').innerHTML = `<table class="table table-compact"><thead><tr><th class="num">Lap</th><th class="num">Distance</th><th class="num">Time</th><th class="num">${isRun ? 'Pace' : 'Speed'}</th><th class="num">Avg HR</th><th class="num">Max HR</th><th class="num">Cadence</th><th class="num">Elev</th></tr></thead><tbody>${rows}</tbody></table>`;
  qs('#splits-sub').textContent = `${laps.length} laps as your watch recorded them.`;
  card.classList.remove('hidden');
}

function renderStreams(streams, activity, paceZones, laps = [], structure = null) {
  const container = qs('#streams-chart');
  if (!streams.length) {
    renderEmpty(container, 'No stream data', 'This activity was synced without per-second data.');
    return;
  }
  const x = streams.map((row) => row.time_offset);
  const heartrate = streams.map((row) => row.heartrate ?? null);
  // Standing still reads as an absurd pace, so moving slower than 0.5 m/s is
  // a gap, and anything slower than twice the median pace is treated the same.
  const moving = streams.map((row) => row.velocity != null && row.velocity > 0.5);
  const rawPace = streams.map((row, index) => (moving[index] ? units.paceMinutes(row.velocity) : null));
  const sortedPace = rawPace.filter((value) => value != null).sort((left, right) => left - right);
  const medianPace = sortedPace[Math.floor(sortedPace.length / 2)] || 0;
  const pace = smooth(rawPace.map((value) => (value != null && value <= medianPace * 2 ? value : null)), 15);
  const cadence = streams.map((row, index) => (row.cadence != null && moving[index] ? row.cadence * 2 : null));
  const altitude = streams.map((row) => (row.altitude != null ? (units.isMetric() ? row.altitude : row.altitude * 3.28084) : null));
  const isRun = ['Run', 'Walk', 'Hike', 'TrailRun'].includes(activity.type);
  const panels = [];
  if (heartrate.some((value) => value != null)) {
    panels.push({ kind: 'line', height: 150, title: 'Heart rate (bpm)', yTicks: 4, yFormat: (value) => String(Math.round(value)),
      series: [{ key: 'hr', label: 'Heart rate', color: 'series-2', values: heartrate }] });
  }
  const paceSorted = pace.filter((value) => value != null).sort((left, right) => left - right);
  if (paceSorted.length) {
    // The slowest 2% of samples (walk breaks, stops) would stretch the axis
    // and flatten the run, so the axis stops at the 98th percentile and the
    // chart clips what is slower.
    const slowCap = paceSorted[Math.floor(paceSorted.length * 0.98)];
    panels.push({ kind: 'line', height: 170, title: `Pace (min${units.paceLabel()})`, yTicks: 4, reverse: true,
      min: paceSorted[0], max: slowCap,
      yFormat: (value) => fmtMinutes(value), bands: isRun ? paceBands(paceZones, pace.map((value) => (value != null && value <= slowCap ? value : null))) : [],
      series: [{ key: 'pace', label: 'Pace', color: 'series-1', values: pace }] });
  }
  if (cadence.some((value) => value != null)) {
    panels.push({ kind: 'line', height: 110, title: 'Cadence (spm)', yTicks: 3, yFormat: (value) => String(Math.round(value)),
      series: [{ key: 'cadence', label: 'Cadence', color: 'series-3', values: cadence }] });
  }
  if (altitude.some((value) => value != null)) {
    panels.push({ kind: 'line', height: 110, title: `Elevation (${units.elevationLabel()})`, yTicks: 3, yFormat: (value) => String(Math.round(value)),
      series: [{ key: 'alt', label: 'Elevation', color: 'series-muted', values: altitude, area: true }] });
  }
  if (!panels.length) {
    renderEmpty(container, 'No stream data', 'This activity has no heart rate, pace, cadence or elevation samples.');
    return;
  }
  // Lap starts after the first; numbered when there are few enough to read.
  const markers = laps.filter((lap) => lap.start_offset > 0).map((lap) => ({ x: lap.start_offset, label: laps.length <= 20 ? String(lap.lap_index) : '' }));
  panelChart(container, {
    x,
    xTicks: (min, max) => secondsTicks(max, 7),
    xFormat: fmtSecondsTick,
    xTitle: (seconds) => `${fmtClock(seconds)} elapsed`,
    tableXLabel: 'Elapsed',
    ariaLabel: 'Activity streams over time',
    legend: false,
    table: true,
    markers,
    xBands: (structure?.reps || []).map((rep) => ({ from: rep.start_offset, to: rep.end_offset, label: structure.reps.length <= 20 ? String(rep.index) : '' })),
    panels,
  });
}

// ---- Detected structure ----
function median(values) {
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.floor(sorted.length / 2)];
}

// Track reps read in metres whatever the unit setting: 400 m, 800 m, 1200 m.
function repDistance(meters) {
  if (meters < 1000) return `${Math.round(meters / 50) * 50} m`;
  if (meters < 2000) return `${Math.round(meters / 100) * 100} m`;
  return units.distance(meters, 1);
}

function structureSummary(structure) {
  const reps = structure.reps;
  if (structure.kind === 'tempo') {
    const seconds = reps.reduce((sum, rep) => sum + rep.moving_time, 0);
    const meters = reps.reduce((sum, rep) => sum + rep.distance, 0);
    const blocks = reps.length > 1 ? `${reps.length} × ${Math.round(median(reps.map((rep) => rep.moving_time)) / 60)} min` : `${Math.round(seconds / 60)} min`;
    return `Tempo: ${blocks} at ${units.pace(meters / seconds)}`;
  }
  const recoveries = reps.map((rep) => rep.recovery_time).filter((value) => value != null);
  const rest = recoveries.length ? `, ${fmtClock(median(recoveries))} recoveries` : '';
  return `${reps.length} × ${repDistance(median(reps.map((rep) => rep.distance)))} at ${units.pace(median(reps.map((rep) => rep.average_speed)))}${rest}`;
}

function renderStructure(structure) {
  if (!structure?.kind || !structure.reps.length) return;
  qs('#structure-kind').textContent = structure.kind === 'tempo' ? 'Tempo' : 'Intervals';
  qs('#structure-summary').textContent = structureSummary(structure);
  const rows = structure.reps.map((rep) => `<tr>
      <td class="num">${rep.index}</td>
      <td class="num">${units.distance(rep.distance, 2)}</td>
      <td class="num">${fmtClock(rep.moving_time)}</td>
      <td class="num">${units.pace(rep.average_speed)}</td>
      <td class="num">${rep.average_heartrate ? Math.round(rep.average_heartrate) : '–'}</td>
      <td class="num">${rep.recovery_time != null ? fmtClock(rep.recovery_time) : '–'}</td>
    </tr>`).join('');
  qs('#structure-reps').innerHTML = `<table class="table table-compact"><thead><tr><th class="num">Rep</th><th class="num">Distance</th><th class="num">Time</th><th class="num">Pace</th><th class="num">Avg HR</th><th class="num">Recovery</th></tr></thead><tbody>${rows}</tbody></table>`;
  qs('#structure-card').classList.remove('hidden');
}

async function loadNote() {
  try {
    const data = await fetchJson(`/api/activities/${activityId}/ai_summary`);
    if (!data.summary) return;
    qs('#ai-note').textContent = data.summary;
    qs('#ai-card').classList.remove('hidden');
  } catch {
    // The note is optional; the page is complete without it.
  }
}

async function load() {
  let activity;
  try {
    activity = await fetchJson(`/api/activities/${activityId}/effectiveness`);
  } catch (error) {
    setText('act-name', 'Activity not found');
    qs('#act-sub').textContent = error.message;
    return;
  }
  renderSummary(activity);
  renderEffort(activity);
  renderZones(activity);
  const [streams, paceZones, laps, structure] = await Promise.all([
    fetchJson(`/api/activities/${activityId}/streams`).catch(() => []),
    config.athleteId ? fetchJson(`/pace_zones/${config.athleteId}`).catch(() => null) : null,
    fetchJson(`/api/activities/${activityId}/laps`).catch(() => []),
    fetchJson(`/api/activities/${activityId}/intervals`).catch(() => null),
  ]);
  renderStreams(streams, activity, paceZones, laps, structure);
  renderStructure(structure);
  renderSplits(laps, activity);
  loadNote();
}

load();
