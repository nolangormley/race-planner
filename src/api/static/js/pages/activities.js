// Activities list (sortable, filterable, deletable) and the workout library.
import { fetchJson, units, fmtDate, fmtDuration, escapeHtml, showToast, qs, qsa, openModal, closeModal } from '../app.js';

const PACE_TYPES = new Set(['Run', 'Walk', 'Hike', 'TrailRun', 'VirtualRun']);

const state = {
  activities: [],
  sort: { key: 'start_date_local', direction: 'desc' },
  type: '',
  workoutsLoaded: false,
};

// ---- Activities ----
function visibleActivities() {
  const rows = state.activities.filter((activity) => !state.type || activity.type === state.type);
  const { key, direction } = state.sort;
  const sign = direction === 'asc' ? 1 : -1;
  rows.sort((left, right) => {
    const a = left[key];
    const b = right[key];
    if (a == null && b == null) return 0;
    if (a == null) return 1;
    if (b == null) return -1;
    if (typeof a === 'string') return sign * a.localeCompare(b);
    return sign * (a - b);
  });
  return rows;
}

function renderActivities() {
  const body = qs('#activities-body');
  const rows = visibleActivities();
  const totalMeters = rows.reduce((sum, activity) => sum + (activity.distance || 0), 0);
  qs('#activities-sub').textContent = rows.length
    ? `${rows.length} ${rows.length === 1 ? 'activity' : 'activities'} · ${units.distance(totalMeters, 0)} total`
    : 'No activities match this filter.';
  qsa('#activities-table th.sortable').forEach((th) => {
    if (th.dataset.sort === state.sort.key) th.setAttribute('aria-sort', state.sort.direction === 'asc' ? 'ascending' : 'descending');
    else th.removeAttribute('aria-sort');
  });
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="8"><div class="empty"><strong>No activities yet</strong>Sync Strava to pull in your training.</div></td></tr>`;
    return;
  }
  body.innerHTML = rows.map((activity) => `
    <tr class="link" data-id="${activity.activity_id}" tabindex="0">
      <td class="nowrap">${fmtDate(activity.start_date_local, { month: 'short', day: 'numeric', year: 'numeric' })}</td>
      <td class="primary">${escapeHtml(activity.name)}<span class="cell-sub">${escapeHtml(activity.type)}</span></td>
      <td class="num">${units.distance(activity.distance, 2)}</td>
      <td class="num">${fmtDuration(activity.moving_time)}</td>
      <td class="num">${PACE_TYPES.has(activity.type) ? units.pace(activity.average_speed) : '–'}</td>
      <td class="num">${activity.average_heartrate ? Math.round(activity.average_heartrate) : '–'}</td>
      <td class="num">${activity.training_load ? Math.round(activity.training_load) : '–'}</td>
      <td class="actions"><button type="button" class="btn btn-ghost btn-sm btn-icon" data-delete="${activity.activity_id}" aria-label="Delete activity">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
      </button></td>
    </tr>`).join('');

  qsa('tr.link', body).forEach((row) => {
    const open = () => { window.location.href = `/workout_streams/${row.dataset.id}`; };
    row.addEventListener('click', open);
    row.addEventListener('keydown', (event) => { if (event.key === 'Enter') open(); });
  });
  qsa('[data-delete]', body).forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      deleteActivity(Number(button.dataset.delete));
    });
  });
}

async function deleteActivity(activityId) {
  const activity = state.activities.find((item) => item.activity_id === activityId);
  if (!window.confirm(`Delete "${activity?.name || 'this activity'}" and its streams from the warehouse? The next sync will pull it back from Strava.`)) return;
  try {
    await fetchJson(`/api/activities/${activityId}`, { method: 'DELETE' });
    state.activities = state.activities.filter((item) => item.activity_id !== activityId);
    renderActivities();
    showToast('Activity deleted');
  } catch (error) {
    showToast(error.message, 'error');
  }
}

async function loadActivities() {
  try {
    state.activities = await fetchJson('/api/activities?limit=1000');
  } catch (error) {
    qs('#activities-body').innerHTML = `<tr><td colspan="8"><div class="empty"><strong>Could not load activities</strong>${escapeHtml(error.message)}</div></td></tr>`;
    return;
  }
  const filter = qs('#type-filter');
  const types = [...new Set(state.activities.map((activity) => activity.type).filter(Boolean))].sort();
  filter.innerHTML = '<option value="">All types</option>' + types.map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`).join('');
  filter.value = state.type;
  renderActivities();
}

qs('#type-filter').addEventListener('change', (event) => {
  state.type = event.target.value;
  renderActivities();
});

qsa('#activities-table th.sortable').forEach((th) => {
  th.addEventListener('click', () => {
    const key = th.dataset.sort;
    if (state.sort.key === key) state.sort.direction = state.sort.direction === 'asc' ? 'desc' : 'asc';
    else state.sort = { key, direction: key === 'name' ? 'asc' : 'desc' };
    renderActivities();
  });
});

// ---- Workout library ----
const modal = qs('#workout-modal');

function openWorkoutModal(workout = null) {
  qs('#workout-modal-title').textContent = workout ? 'Edit workout' : 'Add workout';
  qs('#workout-id').value = workout?.workout_id ?? '';
  qs('#workout-name').value = workout?.name ?? '';
  qs('#workout-category').value = workout?.category ?? 'Aerobic';
  qs('#workout-tags').value = workout?.tags ?? '';
  qs('#workout-desc').value = workout?.description ?? '';
  openModal(modal);
  qs('#workout-name').focus();
}

function renderWorkouts(workouts) {
  const body = qs('#workouts-body');
  if (!workouts.length) {
    body.innerHTML = '<tr><td colspan="5"><div class="empty"><strong>No workouts yet</strong>Add one to build a library you can plan from.</div></td></tr>';
    return;
  }
  body.innerHTML = workouts.map((workout) => `
    <tr>
      <td class="primary">${escapeHtml(workout.name)}</td>
      <td><span class="badge">${escapeHtml(workout.category || 'Uncategorized')}</span></td>
      <td><span class="truncate" style="display:block;max-width:420px" title="${escapeHtml(workout.description || '')}">${escapeHtml(workout.description || '–')}</span></td>
      <td>${escapeHtml(workout.source || 'Manual')}</td>
      <td class="actions">
        <button type="button" class="btn btn-ghost btn-sm" data-edit="${workout.workout_id}">Edit</button>
        <button type="button" class="btn btn-danger btn-sm" data-remove="${workout.workout_id}">Delete</button>
      </td>
    </tr>`).join('');
  qsa('[data-edit]', body).forEach((button) => {
    button.addEventListener('click', () => openWorkoutModal(workouts.find((workout) => String(workout.workout_id) === button.dataset.edit)));
  });
  qsa('[data-remove]', body).forEach((button) => {
    button.addEventListener('click', async () => {
      if (!window.confirm('Delete this workout from the library?')) return;
      try {
        await fetchJson(`/api/workouts/${button.dataset.remove}`, { method: 'DELETE' });
        showToast('Workout deleted');
        loadWorkouts();
      } catch (error) {
        showToast(error.message, 'error');
      }
    });
  });
}

async function loadWorkouts() {
  try {
    const workouts = await fetchJson('/api/workouts');
    renderWorkouts(Array.isArray(workouts) ? workouts : []);
    state.workoutsLoaded = true;
  } catch (error) {
    qs('#workouts-body').innerHTML = `<tr><td colspan="5"><div class="empty"><strong>Could not load workouts</strong>${escapeHtml(error.message)}</div></td></tr>`;
  }
}

qs('#add-workout').addEventListener('click', () => openWorkoutModal());

qs('#workout-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const id = qs('#workout-id').value;
  const payload = {
    name: qs('#workout-name').value.trim(),
    category: qs('#workout-category').value,
    tags: qs('#workout-tags').value.trim(),
    description: qs('#workout-desc').value.trim(),
    source: 'Manual',
  };
  const save = qs('#workout-save');
  save.disabled = true;
  try {
    await fetchJson(id ? `/api/workouts/${id}` : '/api/workouts', { method: id ? 'PUT' : 'POST', body: payload });
    closeModal(modal);
    showToast(id ? 'Workout updated' : 'Workout added');
    loadWorkouts();
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    save.disabled = false;
  }
});

// ---- Tabs ----
qsa('#tabs button').forEach((button) => {
  button.addEventListener('click', () => {
    qsa('#tabs button').forEach((other) => other.setAttribute('aria-pressed', String(other === button)));
    const tab = button.dataset.tab;
    qs('#tab-activities').classList.toggle('hidden', tab !== 'activities');
    qs('#tab-workouts').classList.toggle('hidden', tab !== 'workouts');
    if (tab === 'workouts' && !state.workoutsLoaded) loadWorkouts();
  });
});

document.addEventListener('strava:synced', loadActivities);
loadActivities();
