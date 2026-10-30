// Profile: athlete details, password change, preferences, races, Strava status, warehouse totals.
import {
  config, fetchJson, units, fmtDate, relativeTime, daysUntil, escapeHtml, showToast,
  applyTheme, currentTheme, qs, qsa, renderEmpty,
} from '../app.js';

const userId = config.userId;
const root = qs('#profile');
const RUN_TYPES = new Set(['Run', 'Walk', 'Hike', 'TrailRun', 'VirtualRun']);
let heightMeters = root.dataset.height ? Number(root.dataset.height) : null;
let weightKg = root.dataset.weight ? Number(root.dataset.weight) : null;
// Accounts made with the old sign-up form stored height in centimeters.
if (heightMeters > 3) heightMeters /= 100;

// ---- Athlete ----
function renderAthleteView() {
  const height = qs('#view-height');
  const weight = qs('#view-weight');
  if (heightMeters) {
    const inches = heightMeters * 39.3701;
    height.textContent = units.isMetric() ? `${Math.round(heightMeters * 100)} cm` : `${Math.floor(inches / 12)}′ ${Math.round(inches % 12)}″`;
  } else height.textContent = '–';
  if (weightKg) weight.textContent = units.isMetric() ? `${Math.round(weightKg)} kg` : `${Math.round(weightKg * 2.20462)} lb`;
  else weight.textContent = '–';
}

function fillAthleteForm() {
  const metric = units.isMetric();
  qs('#height-imperial').classList.toggle('hidden', metric);
  qs('#f-height-cm').classList.toggle('hidden', !metric);
  qs('#height-unit').textContent = metric ? '(cm)' : '(ft, in)';
  qs('#weight-unit').textContent = metric ? '(kg)' : '(lb)';
  const sex = qs('#view-sex').textContent.trim();
  const sexSelect = qs('#f-sex');
  const match = [...sexSelect.options].find((option) => option.value.toLowerCase().startsWith(sex.toLowerCase().slice(0, 1)));
  if (match) sexSelect.value = match.value;
  if (heightMeters) {
    const inches = heightMeters * 39.3701;
    qs('#f-height-ft').value = Math.floor(inches / 12);
    qs('#f-height-in').value = Math.round(inches % 12);
    qs('#f-height-cm').value = Math.round(heightMeters * 100);
  }
  if (weightKg) qs('#f-weight').value = metric ? Math.round(weightKg) : Math.round(weightKg * 2.20462);
}

function toggleAthleteForm(open) {
  qs('#athlete-form').classList.toggle('hidden', !open);
  qs('#athlete-view').classList.toggle('hidden', open);
  qs('#edit-athlete').classList.toggle('hidden', open);
  if (open) {
    fillAthleteForm();
    qs('#f-name').focus();
  }
}

async function saveAthlete(event) {
  event.preventDefault();
  const metric = units.isMetric();
  let height = null;
  let weight = null;
  if (metric) {
    const cm = Number(qs('#f-height-cm').value);
    if (cm) height = cm / 100;
    const kg = Number(qs('#f-weight').value);
    if (kg) weight = kg;
  } else {
    const inches = Number(qs('#f-height-ft').value) * 12 + Number(qs('#f-height-in').value);
    if (inches) height = inches * 0.0254;
    const lb = Number(qs('#f-weight').value);
    if (lb) weight = lb / 2.20462;
  }
  const payload = {
    name: qs('#f-name').value.trim(),
    email: qs('#f-email').value.trim(),
    date_of_birth: qs('#f-dob').value || null,
    gender: qs('#f-sex').value,
    height,
    weight,
  };
  const status = qs('#athlete-form .status');
  status.textContent = 'Saving…';
  try {
    await fetchJson(`/profile/${userId}`, { method: 'POST', body: payload });
    heightMeters = height;
    weightKg = weight;
    qs('#view-name').textContent = payload.name;
    qs('#view-email').textContent = payload.email;
    qs('#view-dob').textContent = payload.date_of_birth || '–';
    qs('#view-sex').textContent = payload.gender;
    qsa('.user-link').forEach((link) => { link.textContent = payload.name; });
    renderAthleteView();
    toggleAthleteForm(false);
    showToast('Profile saved');
  } catch (error) {
    status.textContent = error.message;
  }
}

// ---- Password ----
function togglePasswordForm(open) {
  const form = qs('#password-form');
  form.classList.toggle('hidden', !open);
  qs('#edit-password').classList.toggle('hidden', open);
  form.reset();
  qs('#password-form .status').textContent = '';
  if (open) qs('#p-current').focus();
}

async function savePassword(event) {
  event.preventDefault();
  const status = qs('#password-form .status');
  if (qs('#p-new').value !== qs('#p-confirm').value) {
    status.textContent = "The new passwords don't match.";
    return;
  }
  status.textContent = 'Saving…';
  try {
    await fetchJson('/api/password', {
      method: 'POST',
      body: { current_password: qs('#p-current').value, new_password: qs('#p-new').value },
    });
    togglePasswordForm(false);
    showToast('Password changed');
  } catch (error) {
    status.textContent = error.message;
  }
}

// ---- Preferences ----
function renderPrefs() {
  qsa('#pref-units button').forEach((button) => {
    button.setAttribute('aria-pressed', String((button.dataset.units === 'metric') === units.isMetric()));
  });
  qsa('#pref-theme button').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.theme === currentTheme()));
  });
}

// ---- Races ----
async function loadRaces() {
  const container = qs('#races');
  let races;
  try {
    races = await fetchJson(`/api/races/${userId}`);
  } catch (error) {
    renderEmpty(container, 'Could not load races', error.message);
    return;
  }
  if (!races.length) {
    renderEmpty(container, 'No races yet', 'Add one and the dashboard counts down to it.');
    return;
  }
  container.innerHTML = races.map((race) => {
    const days = daysUntil(race.race_date);
    const when = days > 1 ? `<span class="badge badge-accent">in ${days} days</span>` : days === 1 ? '<span class="badge badge-accent">tomorrow</span>' : days === 0 ? '<span class="badge badge-good">today</span>' : `<span class="badge">${Math.abs(days)} days ago</span>`;
    return `<div class="list-row">
      <div>
        <div class="strong">${escapeHtml(race.name || race.race_length)}</div>
        <div class="small muted">${escapeHtml(race.race_length)} · ${fmtDate(race.race_date, { month: 'long', day: 'numeric', year: 'numeric' })}${race.goal_time ? ` · goal ${escapeHtml(race.goal_time)}` : ''}</div>
      </div>
      <div class="row">${when}<button type="button" class="btn btn-ghost btn-icon btn-sm" data-delete-race="${race.id}" aria-label="Delete race">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button></div>
    </div>`;
  }).join('');
  qsa('[data-delete-race]', container).forEach((button) => {
    button.addEventListener('click', async () => {
      if (!window.confirm('Remove this race?')) return;
      try {
        await fetchJson(`/api/races/${button.dataset.deleteRace}`, { method: 'DELETE' });
        showToast('Race removed');
        loadRaces();
      } catch (error) {
        showToast(error.message, 'error');
      }
    });
  });
}

async function saveRace(event) {
  event.preventDefault();
  const payload = {
    name: qs('#r-name').value.trim() || null,
    race_length: qs('#r-length').value,
    race_date: qs('#r-date').value,
    goal_time: qs('#r-goal').value.trim() || null,
  };
  const status = qs('#race-form .status');
  status.textContent = 'Saving…';
  try {
    await fetchJson(`/api/races/${userId}`, { method: 'POST', body: payload });
    qs('#race-form').reset();
    qs('#race-form').classList.add('hidden');
    status.textContent = '';
    showToast('Race added');
    loadRaces();
  } catch (error) {
    status.textContent = error.message;
  }
}

// ---- Strava and warehouse ----
async function loadSyncState() {
  const node = qs('#last-sync');
  if (!node) return;
  try {
    const state = await fetchJson(`/api/sync-strava/${userId}`);
    node.textContent = state.last_synced_at
      ? `${relativeTime(state.last_synced_at)} (${new Date(state.last_synced_at).toLocaleString()})`
      : 'Never';
  } catch {
    node.textContent = '–';
  }
}

async function loadWarehouseStats() {
  let activities;
  try {
    activities = await fetchJson('/api/activities?limit=5000');
  } catch {
    return;
  }
  if (!Array.isArray(activities) || !activities.length) return;
  const runs = activities.filter((activity) => RUN_TYPES.has(activity.type));
  qs('#acct-activities').textContent = String(activities.length);
  qs('#acct-runs').textContent = String(runs.length);
  qs('#acct-distance').textContent = units.distance(runs.reduce((sum, activity) => sum + (activity.distance || 0), 0), 0);
  const dates = activities.map((activity) => activity.start_date_local).filter(Boolean).sort();
  qs('#acct-first').textContent = fmtDate(dates[0], { month: 'short', year: 'numeric' });
}

// ---- Wiring ----
qs('#edit-athlete').addEventListener('click', () => toggleAthleteForm(true));
qs('#cancel-athlete').addEventListener('click', () => toggleAthleteForm(false));
qs('#athlete-form').addEventListener('submit', saveAthlete);
qs('#edit-password').addEventListener('click', () => togglePasswordForm(true));
qs('#cancel-password').addEventListener('click', () => togglePasswordForm(false));
qs('#password-form').addEventListener('submit', savePassword);
qs('#add-race').addEventListener('click', () => {
  qs('#race-form').classList.remove('hidden');
  qs('#r-name').focus();
});
qs('#cancel-race').addEventListener('click', () => qs('#race-form').classList.add('hidden'));
qs('#race-form').addEventListener('submit', saveRace);
qsa('#pref-units button').forEach((button) => {
  button.addEventListener('click', () => {
    units.set(button.dataset.units);
    renderPrefs();
    renderAthleteView();
    loadWarehouseStats();
  });
});
qsa('#pref-theme button').forEach((button) => {
  button.addEventListener('click', () => {
    applyTheme(button.dataset.theme);
    renderPrefs();
  });
});
qs('#profile-sync')?.addEventListener('click', () => qs('#sync-btn')?.click());
document.addEventListener('strava:synced', () => {
  loadSyncState();
  loadWarehouseStats();
});

renderPrefs();
renderAthleteView();
loadRaces();
loadSyncState();
loadWarehouseStats();
