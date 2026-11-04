// Dashboard, the table cards: race predictions, personal records, pace
// zones, this week's plan against the runs, and recent activities.
import { units, fmtDate, fmtDuration, fmtClock, isoDate, escapeHtml, renderEmpty, qs, qsa, RUN_TYPES } from '../app.js';

// ---- Race predictions ----
export function renderPredictions(data) {
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
    <td class="muted small">from your ${fmtClock(row.from.seconds)} ${escapeHtml(row.from.label)} on ${fmtDate(row.from.date)}</td>
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
export function renderRecords(data) {
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

// ---- Pace zones ----
export function renderPaceZones(state) {
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
function activitiesOn(state, dateString) {
  return state.activities.filter((activity) => RUN_TYPES.has(activity.type) && activity.start_date_local?.slice(0, 10) === dateString);
}

// A planned intervals or tempo session with runs that day, all of them
// analysed (structure_checked), and none with that structure.
function ranOffSession(entry, runs) {
  const wanted = (entry.workout_type || '').toLowerCase();
  if (wanted !== 'intervals' && wanted !== 'tempo') return false;
  const analysed = runs.filter((activity) => activity.structure_checked);
  const pending = runs.some((activity) => activity.type === 'Run' && !activity.structure_checked);
  return analysed.length > 0 && !pending && !analysed.some((activity) => activity.structure_kind === wanted);
}

export function renderWeek(state) {
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
    const runs = activitiesOn(state, key);
    const actual = runs.reduce((sum, activity) => sum + (activity.distance || 0), 0);
    const isToday = day.getTime() === today.getTime();
    const past = day < today;
    const isRest = entry?.workout_type?.toLowerCase() === 'rest';
    if (entry && !isRest) planned += 1;
    let status = '';
    if (entry && !isRest) {
      if (!entry.is_completed && ranOffSession(entry, runs)) {
        status = '<span class="badge badge-warning">Ran, not the session</span>';
      } else if (entry.is_completed || runs.length) {
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
export function renderRecent(state) {
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
