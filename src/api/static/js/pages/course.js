// Public course page: the course's elevation profile and a pacing plan for any goal, no account needed.
// It opens with an example goal at 9:00 a mile, so the splits show at once.
import { fetchJson, units, qs, qsa } from '../app.js';
import { renderSummary, renderProfile, renderSplits, renderBand } from '../pacing-view.js';

const METERS_PER_MILE = 1609.344;
const EXAMPLE_SECONDS_PER_MILE = 540;
const root = qs('#course');
const { race, event } = root.dataset;
const state = { strategy: 'even', plan: null, example: null };

function exampleGoal() {
  const minutes = Math.round(((Number(root.dataset.distance) / METERS_PER_MILE) * EXAMPLE_SECONDS_PER_MILE) / 60);
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}:${String(minutes % 60).padStart(2, '0')}:00` : `${minutes}:00`;
}

function renderAll() {
  renderSummary(state.plan);
  renderProfile(state.plan, { title: 'No profile', message: 'This course has no elevation data.' });
  renderSplits(state.plan);
  renderBand(state.plan);
  const example = state.plan && qs('#goal').value.trim() === state.example;
  qs('#goal-source').textContent = example ? 'Example goal at 9:00 a mile (5:35 a km). Type yours to see what it takes on this course.' : '';
  qsa('#units-toggle button').forEach((button) => {
    button.setAttribute('aria-pressed', String((button.dataset.units === 'metric') === units.isMetric()));
  });
  qsa('#strategy button').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.strategy === state.strategy));
  });
}

async function loadPlan() {
  const goal = qs('#goal').value.trim() || state.example;
  const params = new URLSearchParams({ goal, strategy: state.strategy, unit: units.isMetric() ? 'km' : 'mi' });
  const status = qs('#pacing-status');
  try {
    state.plan = await fetchJson(`/api/courses/${encodeURIComponent(race)}/${encodeURIComponent(event)}/pacing?${params}`);
    status.classList.add('hidden');
  } catch (error) {
    state.plan = null;
    status.textContent = error.message;
    status.classList.remove('hidden');
  }
  renderAll();
}

qs('#pacing-form').addEventListener('submit', (submitted) => {
  submitted.preventDefault();
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
qs('#print-band').addEventListener('click', () => window.print());

state.example = exampleGoal();
qs('#goal').value = state.example;
loadPlan();
