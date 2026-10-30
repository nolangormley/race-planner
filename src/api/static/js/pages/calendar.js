// Training plan calendar: a month grid of planned workouts next to synced
// Strava activities, with add/edit/delete and attach-to-activity.
import {
  config, fetchJson, units, fmtDate, parseDate, isoDate, escapeHtml, showToast,
  qs, qsa, openModal, closeModal, renderEmpty, RUN_TYPES,
} from '../app.js';

const userId = config.userId;
const CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
const BOLT = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></svg>';

const state = {
  month: new Date(),
  entries: [],
  activities: [],
};
state.month.setDate(1);
state.month.setHours(0, 0, 0, 0);

const todayKey = isoDate(new Date());

// "6 miles", "8 km", "10K", "5 mi" -> meters, or null.
export function plannedMeters(text) {
  if (!text) return null;
  const match = String(text).match(/(\d+(?:\.\d+)?)\s*(k(?:m|ilomet\w*)?|mi(?:les?)?|m\b)?/i);
  if (!match) return null;
  const amount = Number(match[1]);
  const unit = (match[2] || '').toLowerCase();
  if (unit.startsWith('k')) return amount * 1000;
  if (unit === 'm') return amount;
  return amount * 1609.344;
}

function activitiesOn(dateKey) {
  return state.activities.filter((activity) => activity.start_date_local?.slice(0, 10) === dateKey);
}

function entryOn(dateKey) {
  return state.entries.find((entry) => entry.date === dateKey);
}

function isRest(entry) {
  return (entry.workout_type || '').toLowerCase() === 'rest';
}

function entryDone(entry) {
  return entry.is_completed || entry.strava_activity_id != null || activitiesOn(entry.date).some((activity) => RUN_TYPES.has(activity.type));
}

// ---- Month grid ----
function renderMonth() {
  const year = state.month.getFullYear();
  const month = state.month.getMonth();
  qs('#cal-month').textContent = state.month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  qs('#month-title').textContent = state.month.toLocaleDateString(undefined, { month: 'long' });

  const first = new Date(year, month, 1);
  const start = new Date(first);
  start.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  const grid = qs('#cal-grid');
  grid.replaceChildren();
  for (let offset = 0; offset < 42; offset += 1) {
    const day = new Date(start);
    day.setDate(start.getDate() + offset);
    const key = isoDate(day);
    const cell = document.createElement('div');
    cell.className = 'cal-cell';
    cell.setAttribute('role', 'gridcell');
    cell.tabIndex = 0;
    cell.dataset.date = key;
    if (day.getMonth() !== month) cell.classList.add('other');
    if (key === todayKey) cell.classList.add('today');
    cell.setAttribute('aria-label', day.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }));

    const number = document.createElement('span');
    number.className = 'cal-day';
    number.textContent = day.getDate();
    cell.appendChild(number);

    const entry = entryOn(key);
    if (entry) cell.appendChild(entryChip(entry, key));
    for (const activity of activitiesOn(key)) {
      if (entry && entry.strava_activity_id === activity.activity_id) continue;
      cell.appendChild(activityChip(activity));
    }

    const open = () => openEntryModal(key, entry);
    cell.addEventListener('click', (event) => {
      if (event.target.closest('.chip')) return;
      open();
    });
    cell.addEventListener('keydown', (event) => {
      if ((event.key === 'Enter' || event.key === ' ') && !event.target.closest('.chip')) {
        event.preventDefault();
        open();
      }
    });
    grid.appendChild(cell);
  }
}

function entryChip(entry, key) {
  const chip = document.createElement('button');
  chip.type = 'button';
  const done = !isRest(entry) && entryDone(entry);
  const missed = !isRest(entry) && !done && key < todayKey;
  chip.className = `chip${isRest(entry) ? ' rest' : done ? ' done' : missed ? ' missed' : ''}`;
  chip.innerHTML = `${done ? CHECK : ''}<span class="label">${escapeHtml(entry.workout_type || 'Workout')}</span>${entry.approximate_distance ? `<span class="dist">${escapeHtml(entry.approximate_distance)}</span>` : ''}`;
  chip.setAttribute('aria-label', `${entry.workout_type || 'Workout'} on ${fmtDate(key, { month: 'long', day: 'numeric' })}${done ? ', done' : ''}`);
  chip.addEventListener('click', () => openEntryModal(key, entry));
  return chip;
}

function activityChip(activity) {
  const chip = document.createElement('a');
  chip.className = 'chip activity';
  chip.href = `/workout_streams/${activity.activity_id}`;
  chip.innerHTML = `${BOLT}<span class="label">${escapeHtml(activity.type || 'Activity')}</span><span class="dist">${units.distance(activity.distance)}</span>`;
  chip.title = activity.name || '';
  return chip;
}

// ---- Side cards ----
function renderSummary() {
  const container = qs('#month-summary');
  const year = state.month.getFullYear();
  const month = state.month.getMonth();
  const inMonth = (dateString) => {
    const date = parseDate(dateString);
    return date.getFullYear() === year && date.getMonth() === month;
  };
  const entries = state.entries.filter((entry) => inMonth(entry.date));
  const workouts = entries.filter((entry) => !isRest(entry));
  const planned = workouts.reduce((sum, entry) => sum + (plannedMeters(entry.approximate_distance) || 0), 0);
  const actual = state.activities
    .filter((activity) => RUN_TYPES.has(activity.type) && inMonth(activity.start_date_local.slice(0, 10)))
    .reduce((sum, activity) => sum + (activity.distance || 0), 0);
  const done = workouts.filter(entryDone).length;
  if (!entries.length && !actual) {
    renderEmpty(container, 'Nothing planned this month', 'Click a day to add a workout.');
    return;
  }
  const share = planned > 0 ? Math.min(100, Math.round((actual / planned) * 100)) : 0;
  container.innerHTML = `
    <div class="month-meter">
      <div class="row spread"><span class="strong">${units.distance(actual, 1)} run</span><span class="muted">${planned ? `of ${units.distance(planned, 0)} planned` : 'no distance planned'}</span></div>
      <div class="meter mt-2"><span style="width:${share}%"></span></div>
    </div>
    <dl class="kv">
      <dt>Workouts done</dt><dd>${done} of ${workouts.length}</dd>
      <dt>Rest days</dt><dd>${entries.length - workouts.length}</dd>
      <dt>Runs logged</dt><dd>${state.activities.filter((activity) => RUN_TYPES.has(activity.type) && inMonth(activity.start_date_local.slice(0, 10))).length}</dd>
    </dl>`;
}

function renderUpcoming() {
  const container = qs('#upcoming');
  const upcoming = state.entries.filter((entry) => entry.date >= todayKey && !isRest(entry)).slice(0, 6);
  if (!upcoming.length) {
    renderEmpty(container, 'No upcoming workouts', 'Add sessions to the calendar and they appear here.');
    return;
  }
  container.replaceChildren();
  for (const entry of upcoming) {
    const row = document.createElement('div');
    row.className = 'upcoming-row';
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    const done = entryDone(entry);
    row.innerHTML = `<span class="date">${entry.date === todayKey ? 'Today' : fmtDate(entry.date, { weekday: 'short', day: 'numeric' })}</span><span class="label">${escapeHtml(entry.workout_type || 'Workout')}${entry.training_focus ? ` <span class="muted">· ${escapeHtml(entry.training_focus)}</span>` : ''}</span><span class="dist">${escapeHtml(entry.approximate_distance || '')}</span>${done ? '<span class="badge badge-good">Done</span>' : ''}`;
    const open = () => openEntryModal(entry.date, entry);
    row.addEventListener('click', open);
    row.addEventListener('keydown', (event) => { if (event.key === 'Enter') open(); });
    container.appendChild(row);
  }
}

function renderAll() {
  renderMonth();
  renderSummary();
  renderUpcoming();
}

// ---- Modal ----
const modal = qs('#entry-modal');

function openEntryModal(dateKey, entry = null) {
  qs('#entry-modal-title').textContent = entry ? 'Edit workout' : 'Add workout';
  qs('#entry-modal-date').textContent = parseDate(dateKey).toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  qs('#entry-id').value = entry?.id ?? '';
  qs('#entry-date').value = dateKey;
  const typeSelect = qs('#entry-type');
  const type = entry?.workout_type || 'Easy Run';
  if (![...typeSelect.options].some((option) => option.value === type)) typeSelect.add(new Option(type, type));
  typeSelect.value = type;
  qs('#entry-distance').value = entry?.approximate_distance ?? '';
  qs('#entry-focus').value = entry?.training_focus ?? '';
  qs('#entry-desc').value = entry?.description ?? '';
  qs('#entry-completed').checked = Boolean(entry?.is_completed);
  qs('#entry-delete').classList.toggle('hidden', !entry);

  const attachField = qs('#entry-attach-field');
  const attach = qs('#entry-attach');
  const candidates = activitiesOn(dateKey);
  attachField.classList.toggle('hidden', !candidates.length);
  attach.innerHTML = '<option value="">Not attached</option>' + candidates.map((activity) => `<option value="${activity.activity_id}">${escapeHtml(activity.name)} · ${units.distance(activity.distance)}</option>`).join('');
  attach.value = entry?.strava_activity_id && candidates.some((activity) => activity.activity_id === entry.strava_activity_id) ? String(entry.strava_activity_id) : '';

  qs('#entry-form .status').textContent = '';
  openModal(modal);
  typeSelect.focus();
}

async function saveEntry(event) {
  event.preventDefault();
  const id = qs('#entry-id').value;
  const attach = qs('#entry-attach').value;
  // Empty strings, not nulls: the update endpoint treats null as "unchanged".
  const payload = {
    date: qs('#entry-date').value,
    workout_type: qs('#entry-type').value,
    training_focus: qs('#entry-focus').value.trim(),
    approximate_distance: qs('#entry-distance').value.trim(),
    description: qs('#entry-desc').value.trim(),
    is_completed: qs('#entry-completed').checked,
    strava_activity_id: attach ? Number(attach) : null,
  };
  const save = qs('#entry-save');
  save.disabled = true;
  try {
    if (id) await fetchJson(`/api/training-plan/entry/${id}`, { method: 'PUT', body: payload });
    else await fetchJson(`/api/training-plan/${userId}/entry`, { method: 'POST', body: payload });
    closeModal(modal);
    showToast(id ? 'Workout updated' : 'Workout added');
    await loadPlan();
  } catch (error) {
    qs('#entry-form .status').textContent = error.message;
  } finally {
    save.disabled = false;
  }
}

async function deleteEntry() {
  const id = qs('#entry-id').value;
  if (!id || !window.confirm('Remove this workout from the plan?')) return;
  try {
    await fetchJson(`/api/training-plan/entry/${id}`, { method: 'DELETE' });
    closeModal(modal);
    showToast('Workout removed');
    await loadPlan();
  } catch (error) {
    showToast(error.message, 'error');
  }
}

// ---- Build a plan ----
const buildModal = qs('#build-modal');
const PLAN_RACES = new Set(['5k', '5 mile', '10k', '10 mile', 'half marathon', 'marathon']);

async function openBuildModal() {
  const metric = units.isMetric();
  qs('#build-weekly-unit').textContent = metric ? '(km)' : '(mi)';
  qs('#build-form .status').textContent = '';
  const select = qs('#build-race');
  select.innerHTML = '<option value="">Loading races…</option>';
  openModal(buildModal);

  const [races, analytics] = await Promise.all([
    fetchJson(`/api/races/${userId}`).catch(() => []),
    config.athleteId ? fetchJson(`/analytics/${config.athleteId}`).catch(() => null) : null,
  ]);
  const upcoming = races.filter((race) => race.race_date > todayKey && PLAN_RACES.has((race.race_length || '').toLowerCase()));
  if (!upcoming.length) {
    select.innerHTML = '<option value="">No upcoming race yet</option>';
    qs('#build-race-hint').innerHTML = 'Add a 5K to marathon race on your <a href="/profile">profile</a> first.';
    qs('#build-save').disabled = true;
  } else {
    select.innerHTML = upcoming.map((race) => `<option value="${race.id}">${escapeHtml(race.name || race.race_length)} · ${escapeHtml(race.race_length)} · ${fmtDate(race.race_date, { month: 'short', day: 'numeric' })}</option>`).join('');
    qs('#build-race-hint').textContent = '';
    qs('#build-save').disabled = false;
  }
  // Prefill from the last four complete weeks of running.
  const weeks = (analytics?.weekly_volume || []).slice(-5, -1);
  const average = weeks.length ? weeks.reduce((sum, week) => sum + week.distance, 0) / weeks.length : 0;
  qs('#build-weekly').value = average ? units.distanceValue(average).toFixed(1) : '';
  qs('#build-weekly-hint').textContent = average ? `Your last four weeks averaged ${units.distance(average, 1)}.` : 'No recent runs to go on; enter a number.';
}

async function submitBuild(event) {
  event.preventDefault();
  const status = qs('#build-form .status');
  const save = qs('#build-save');
  const metric = units.isMetric();
  const weeklyValue = Number(qs('#build-weekly').value);
  const payload = {
    race_id: Number(qs('#build-race').value),
    days_per_week: Number(qs('#build-days').value),
    long_run_day: Number(qs('#build-long').value),
    quality_sessions: Number(qs('#build-quality').value),
    weekly_distance_m: weeklyValue ? weeklyValue * (metric ? 1000 : 1609.344) : null,
    metric,
    replace_future: qs('#build-replace').checked,
  };
  save.disabled = true;
  status.textContent = 'Building…';
  try {
    const result = await fetchJson(`/api/training-plan/${userId}/build`, { method: 'POST', body: payload });
    closeModal(buildModal);
    const peak = units.distance(result.summary.peak_weekly_m, 0);
    showToast(`Plan built: ${result.summary.weeks} weeks, peaking at ${peak} a week`);
    state.entries = (result.entries || []).sort((left, right) => left.date.localeCompare(right.date));
    renderAll();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    save.disabled = false;
  }
}

// ---- Data ----
async function loadPlan() {
  const data = await fetchJson(`/api/training-plan/${userId}`).catch(() => ({ entries: [] }));
  state.entries = (data.entries || []).sort((left, right) => left.date.localeCompare(right.date));
  renderAll();
}

async function loadActivities() {
  const data = await fetchJson(`/api/user-activities/${userId}?limit=1000`).catch(() => []);
  state.activities = Array.isArray(data) ? data : [];
  renderAll();
}

function shiftMonth(delta) {
  state.month.setMonth(state.month.getMonth() + delta);
  renderAll();
}

qs('#cal-prev').addEventListener('click', () => shiftMonth(-1));
qs('#cal-next').addEventListener('click', () => shiftMonth(1));
qs('#cal-today').addEventListener('click', () => {
  state.month = new Date();
  state.month.setDate(1);
  state.month.setHours(0, 0, 0, 0);
  renderAll();
});
qs('#cal-add').addEventListener('click', () => openEntryModal(todayKey, entryOn(todayKey)));
qs('#cal-build').addEventListener('click', openBuildModal);
qs('#build-form').addEventListener('submit', submitBuild);
qs('#entry-form').addEventListener('submit', saveEntry);
qs('#entry-delete').addEventListener('click', deleteEntry);
document.addEventListener('strava:synced', loadActivities);

renderAll();
loadPlan();
loadActivities();
