// Dashboard: loads each card's data and hands it to the renderers in
// dashboard-training.js, dashboard-tables.js, dashboard-recovery.js and
// energy.js; also the header and the unit and range toggles.
import { config, fetchJson, units, fmtDate, daysUntil, escapeHtml, qs, qsa } from '../app.js';
import {
  renderTiles, renderVo2, renderPmc, renderPmcEmpty, renderVolume, renderHrZones, renderTypeShare, renderEfficiency,
} from './dashboard-training.js';
import { renderPredictions, renderRecords, renderPaceZones, renderWeek, renderRecent } from './dashboard-tables.js';
import { renderRecovery, renderReadiness } from './dashboard-recovery.js';
import { renderEnergy } from './energy.js';

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

// ---- Header ----
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

// ---- Loading ----
async function loadStatus() {
  try {
    state.status = await fetchJson(`/status/${athleteId}`);
  } catch (error) {
    state.status = { history: [], forecast: [] };
    qsa('#stats .stat-value').forEach((node) => { node.textContent = '–'; });
    renderPmcEmpty(error.status === 404 ? 'Sync Strava to build your fitness curve.' : error.message);
    return;
  }
  renderTiles(state);
  renderPmc(state);
}

async function loadAnalytics() {
  try {
    state.analytics = await fetchJson(`/analytics/${athleteId}`);
  } catch {
    state.analytics = null;
  }
  renderVolume(state);
  renderHrZones(state);
  renderTypeShare(state);
}

async function loadVo2() {
  try {
    state.vo2 = await fetchJson(`/vo2max/${athleteId}`);
  } catch {
    state.vo2 = null;
  }
  renderVo2(state);
}

async function loadPaceZones() {
  try {
    state.paceZones = await fetchJson(`/pace_zones/${athleteId}`);
  } catch {
    state.paceZones = null;
  }
  renderPaceZones(state);
}

async function loadPlanAndActivities() {
  const [plan, activities] = await Promise.all([
    fetchJson(`/api/training-plan/${userId}`).catch(() => ({ entries: [] })),
    fetchJson('/api/activities?limit=40').catch(() => []),
  ]);
  state.plan = plan.entries || [];
  state.activities = Array.isArray(activities) ? activities : [];
  renderWeek(state);
  renderRecent(state);
}

async function loadProjection() {
  state.projection = await fetchJson(`/api/projection/${userId}`).catch(() => null);
  if (state.status?.history?.length) renderPmc(state);
}

async function loadPredictions() {
  state.predictions = await fetchJson(`/api/predictions/${athleteId}`).catch(() => null);
  renderPredictions(state.predictions);
}

async function loadRecords() {
  state.records = await fetchJson(`/api/records/${athleteId}`).catch(() => null);
  renderRecords(state.records);
}

async function loadReadiness() {
  if (!qs('#readiness')) return;
  state.readiness = await fetchJson(`/api/readiness/${userId}`).catch(() => null);
  renderReadiness(state.readiness);
  // Today's planned session shows the call's hint.
  if (state.plan.length) renderWeek(state);
}

async function loadEnergy() {
  if (!qs('#energy')) return;
  renderEnergy(await fetchJson(`/api/energy/${userId}?days=7`).catch(() => null));
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
  loadEnergy();
  loadReadiness();
  fetchJson(`/api/races/${userId}`).then(renderHeader).catch(() => renderHeader([]));
}

function rerenderUnits() {
  qsa('#units-toggle button').forEach((button) => {
    button.setAttribute('aria-pressed', String((button.dataset.units === 'metric') === units.isMetric()));
  });
  if (state.analytics) renderVolume(state);
  if (state.paceZones) renderPaceZones(state);
  if (state.predictions) renderPredictions(state.predictions);
  if (state.efficiency) renderEfficiency(state.efficiency);
  if (state.records) renderRecords(state.records);
  if (state.activities.length || state.plan.length) {
    renderWeek(state);
    renderRecent(state);
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
    if (state.status?.history?.length) renderPmc(state);
  });
});
document.addEventListener('strava:synced', loadAll);

rerenderUnits();
loadAll();
