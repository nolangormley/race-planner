// Profile: athlete details, password change, preferences, races, Strava status, warehouse totals.
import {
  config, fetchJson, units, fmtDate, relativeTime, daysUntil, escapeHtml, showToast,
  applyTheme, currentTheme, qs, qsa, renderEmpty,
} from '../app.js';
import { refreshSync } from '../sync.js';

const userId = config.userId;
const root = qs('#profile');
const RUN_TYPES = new Set(['Run', 'Walk', 'Hike', 'TrailRun', 'VirtualRun']);
let heightMeters = root.dataset.height ? Number(root.dataset.height) : null;
let weightKg = root.dataset.weight ? Number(root.dataset.weight) : null;
// Accounts made with the old sign-up form stored height in centimeters.
if (heightMeters > 3) heightMeters /= 100;
// Heart rate profile: null fields mean the default/detected value is in use.
let hrProfile = { resting_hr: null, max_hr: null, default_resting_hr: 60, detected_max_hr: null };

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
  qs('#view-rest-hr').textContent = hrProfile.resting_hr
    ? `${hrProfile.resting_hr} bpm`
    : hrProfile.coros_resting_hr
      ? `${hrProfile.coros_resting_hr} bpm (from COROS)`
      : `${hrProfile.default_resting_hr} bpm (default)`;
  qs('#view-max-hr').textContent = hrProfile.max_hr
    ? `${hrProfile.max_hr} bpm`
    : hrProfile.detected_max_hr ? `${Math.round(hrProfile.detected_max_hr)} bpm (highest recorded)` : '–';
}

async function loadHrProfile() {
  try {
    hrProfile = await fetchJson(`/api/hr-profile/${userId}`);
  } catch {
    return;
  }
  renderAthleteView();
  if (hrProfile.detected_max_hr) {
    qs('#max-hr-hint').textContent = `Blank uses the highest heart rate in your activities, currently ${Math.round(hrProfile.detected_max_hr)} bpm.`;
  }
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
  qs('#f-rest-hr').value = hrProfile.resting_hr ?? '';
  qs('#f-max-hr').value = hrProfile.max_hr ?? '';
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
    resting_hr: qs('#f-rest-hr').value ? Number(qs('#f-rest-hr').value) : null,
    max_hr: qs('#f-max-hr').value ? Number(qs('#f-max-hr').value) : null,
  };
  const status = qs('#athlete-form .status');
  status.textContent = 'Saving…';
  try {
    const result = await fetchJson(`/profile/${userId}`, { method: 'POST', body: payload });
    heightMeters = height;
    weightKg = weight;
    hrProfile = { ...hrProfile, resting_hr: payload.resting_hr, max_hr: payload.max_hr };
    qs('#view-name').textContent = payload.name;
    qs('#view-email').textContent = payload.email;
    qs('#view-dob').textContent = payload.date_of_birth || '–';
    qs('#view-sex').textContent = payload.gender;
    qsa('.user-link').forEach((link) => { link.textContent = payload.name; });
    renderAthleteView();
    toggleAthleteForm(false);
    showToast(result.recompute_started ? 'Profile saved, recomputing your metrics' : 'Profile saved');
    if (result.recompute_started) refreshSync();
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
      <div class="row">${when}<a class="btn btn-sm" href="/races/${race.id}/pacing">Pacing</a><button type="button" class="btn btn-ghost btn-icon btn-sm" data-delete-race="${race.id}" aria-label="Delete race">
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
// ---- COROS ----
function renderCoros(status) {
  const linked = qs('#coros-linked');
  const form = qs('#coros-form');
  linked.classList.toggle('hidden', !status.linked);
  form.classList.toggle('hidden', status.linked);
  qs('#coros-cancel').classList.toggle('hidden', !status.linked);
  if (!status.linked) return;
  qs('#coros-status').innerHTML = status.error
    ? '<span class="badge badge-warning">Needs attention</span>'
    : '<span class="badge badge-good">Connected</span>';
  qs('#coros-nights').textContent = status.nights ? `${status.nights} nights` : 'Nothing yet';
  qs('#coros-rhr').textContent = status.resting_hr ? `${status.resting_hr} bpm, median of the last 14 nights` : '–';
  qs('#coros-synced').textContent = status.synced_at ? relativeTime(status.synced_at) : 'Never';
  const error = qs('#coros-error');
  error.textContent = status.error || '';
  error.classList.toggle('hidden', !status.error);
}

async function loadCoros() {
  if (!qs('#coros-form')) return;
  try {
    renderCoros(await fetchJson(`/api/coros/${userId}`));
  } catch {
    // The card keeps its empty state.
  }
}

function afterCorosSync(result) {
  renderCoros(result.status);
  loadHrProfile();
  const nights = result.sync.rows;
  showToast(result.sync.recompute_started
    ? `COROS: ${nights} nights pulled, recomputing your metrics with the new resting heart rate`
    : `COROS: ${nights} nights pulled`);
  if (result.sync.recompute_started) refreshSync();
}

qs('#coros-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const status = qs('#coros-form .status');
  const button = qs('#coros-link');
  button.disabled = true;
  status.textContent = 'Logging in and pulling the last year…';
  try {
    const result = await fetchJson(`/api/coros/${userId}/link`, {
      method: 'POST',
      body: { email: qs('#coros-email').value.trim(), password: qs('#coros-password').value, region: qs('#coros-region').value },
    });
    qs('#coros-password').value = '';
    status.textContent = '';
    afterCorosSync(result);
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
qs('#coros-cancel')?.addEventListener('click', () => {
  qs('#coros-form').classList.add('hidden');
  qs('#coros-linked').classList.remove('hidden');
});
qs('#coros-reconnect')?.addEventListener('click', () => {
  qs('#coros-linked').classList.add('hidden');
  qs('#coros-form').classList.remove('hidden');
  qs('#coros-email').focus();
});
qs('#coros-sync')?.addEventListener('click', async (event) => {
  event.target.disabled = true;
  try {
    afterCorosSync(await fetchJson(`/api/coros/${userId}/sync`, { method: 'POST' }));
  } catch (error) {
    showToast(error.message, 'error');
    loadCoros();
  } finally {
    event.target.disabled = false;
  }
});
qs('#coros-unlink')?.addEventListener('click', async () => {
  if (!window.confirm('Unlink COROS and delete the stored nights?')) return;
  try {
    renderCoros(await fetchJson(`/api/coros/${userId}`, { method: 'DELETE' }));
    loadHrProfile();
    showToast('COROS unlinked');
  } catch (error) {
    showToast(error.message, 'error');
  }
});

// ---- Weekly digest ----
async function loadDigest() {
  const digest = await fetchJson(`/api/digest/${userId}`);
  qs('#digest-enabled').checked = digest.enabled;
  const hint = qs('#digest-hint');
  if (!digest.smtp_configured) {
    hint.textContent = 'Email is not set up on this server (SMTP_HOST); digests are written to the server log instead.';
  } else if (digest.last_sent_at) {
    hint.textContent = `Last sent ${fmtDate(digest.last_sent_at, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}.`;
  } else {
    hint.textContent = 'A Monday morning summary of last week, your plan for this week and upcoming races.';
  }
}

qs('#digest-enabled').addEventListener('change', async (event) => {
  try {
    await fetchJson(`/api/digest/${userId}`, { method: 'POST', body: { enabled: event.target.checked } });
    showToast(event.target.checked ? 'Weekly digest on' : 'Weekly digest off');
  } catch (error) {
    event.target.checked = !event.target.checked;
    showToast(error.message, 'error');
  }
});
qs('#digest-preview').addEventListener('click', async () => {
  const block = qs('#digest-body');
  if (!block.classList.contains('hidden')) {
    block.classList.add('hidden');
    return;
  }
  block.textContent = 'Building…';
  block.classList.remove('hidden');
  try {
    const preview = await fetchJson(`/api/digest/${userId}/preview`);
    block.textContent = `Subject: ${preview.subject}\n\n${preview.body}`;
  } catch (error) {
    block.textContent = error.message;
  }
});
qs('#digest-send').addEventListener('click', async (event) => {
  event.target.disabled = true;
  try {
    const result = await fetchJson(`/api/digest/${userId}/send`, { method: 'POST' });
    showToast(result.delivered ? `Digest sent to ${result.to}` : 'Digest written to the server log (no SMTP configured)');
    loadDigest();
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    event.target.disabled = false;
  }
});

qs('#profile-sync')?.addEventListener('click', () => qs('#sync-btn')?.click());
document.addEventListener('strava:synced', () => {
  loadSyncState();
  loadWarehouseStats();
});

renderPrefs();
renderAthleteView();
loadHrProfile();
loadRaces();
loadSyncState();
loadWarehouseStats();
loadDigest();
loadCoros();
