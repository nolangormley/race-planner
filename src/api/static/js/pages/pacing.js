// Race-day pacing: goal and strategy controls, the course profile with split
// markers, the splits table and a printable pace band.
import { fetchJson, units, qs, qsa, escapeHtml, showToast, renderEmpty, openModal, closeModal } from '../app.js';
import { renderSummary, renderProfile, renderSplits, renderBand } from '../pacing-view.js';

const root = qs('#pacing');
const raceId = root.dataset.raceId;
// defaultGoal is the goal the server chose (race goal or prediction); the
// field only counts as typed when it differs from it. library holds the
// course library's courses of this race's distance once the modal loaded them.
const state = { strategy: 'even', plan: null, defaultGoal: null, library: null };

const GOAL_SOURCES = {
  query: 'Goal as typed. Leave the field empty to go back to the race goal.',
  race: 'Goal from your race. Type another time to see what it takes.',
  prediction: 'No goal on this race, so this is your predicted time from recent best efforts. Type a goal or set one on the race.',
};

function renderCourseControls() {
  const plan = state.plan;
  const hasCourse = Boolean(plan?.course);
  qs('#gpx-remove').classList.toggle('hidden', !hasCourse);
  qs('#gpx-upload').textContent = hasCourse ? 'Replace GPX' : 'Upload GPX';
  const note = qs('#course-note');
  if (plan?.warnings?.length) note.textContent = plan.warnings.join(' ');
  else if (hasCourse) note.textContent = 'Elevation along the course, with each split marked. Pace is adjusted so the effort stays level over the hills.';
  else note.textContent = "Elevation along the course, with each split marked. Find your race's course or upload its GPX, and the splits follow the hills at one effort.";
}

function renderAll() {
  renderSummary(state.plan);
  if (state.plan) qs('#goal-source').textContent = GOAL_SOURCES[state.plan.goal_source] || '';
  renderCourseControls();
  renderProfile(state.plan, {
    title: 'No course yet',
    message: "Find your race's course or upload its GPX to see the elevation profile and let the splits follow the hills.",
  });
  renderSplits(state.plan);
  renderBand(state.plan);
  qsa('#units-toggle button').forEach((button) => {
    button.setAttribute('aria-pressed', String((button.dataset.units === 'metric') === units.isMetric()));
  });
  qsa('#strategy button').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.strategy === state.strategy));
  });
}

// ---- Data ----
async function loadPlan() {
  const goal = qs('#goal').value.trim();
  const params = new URLSearchParams({ strategy: state.strategy, unit: units.isMetric() ? 'km' : 'mi' });
  if (goal && goal !== state.defaultGoal) params.set('goal', goal);
  const status = qs('#pacing-status');
  try {
    state.plan = await fetchJson(`/api/races/${raceId}/pacing?${params}`);
    status.classList.add('hidden');
    if (state.plan.goal_source !== 'query') {
      state.defaultGoal = state.plan.goal;
      qs('#goal').value = state.plan.goal;
    }
  } catch (error) {
    state.plan = null;
    status.textContent = error.message;
    status.classList.remove('hidden');
    qs('#goal-source').textContent = '';
  }
  renderAll();
}

async function uploadCourse(file) {
  const body = new FormData();
  body.append('file', file);
  const response = await fetch(`/api/races/${raceId}/course`, { method: 'POST', body });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail || `Upload failed (${response.status})`);
  return payload;
}

// ---- Course library ----
function libraryRow(course) {
  const warn = course.status === 'warn';
  return `<div class="list-row">
    <div>
      <div class="strong">${escapeHtml(course.name)}</div>
      <div class="small muted">${escapeHtml(course.event_name)} · ${escapeHtml(course.city)}, ${escapeHtml(course.state)} · ${units.distance(course.length_m, 1)} · ${units.elevation(course.ascent_m)} up</div>
      ${warn ? `<div class="small faint">${escapeHtml(course.detail)}</div>` : ''}
    </div>
    <div class="row">${warn ? '<span class="badge badge-warning">Check it</span>' : ''}<button type="button" class="btn btn-sm" data-race="${escapeHtml(course.race)}" data-event="${escapeHtml(course.event)}">Use</button></div>
  </div>`;
}

// Every word typed must appear in the race name, event, city or state.
function renderLibrary() {
  const list = qs('#library-list');
  const words = qs('#library-search').value.toLowerCase().split(/\s+/).filter(Boolean);
  const matches = state.library.filter((course) => {
    const text = `${course.name} ${course.event_name} ${course.city} ${course.state}`.toLowerCase();
    return words.every((word) => text.includes(word));
  });
  if (matches.length) list.innerHTML = matches.map(libraryRow).join('');
  else if (state.library.length) renderEmpty(list, 'No match', 'Try the race name or its city.');
  else renderEmpty(list, 'No course of this distance yet', 'Upload the race GPX instead.');
}

async function openLibrary() {
  openModal(qs('#library-modal'));
  if (!state.library) {
    try {
      state.library = (await fetchJson(`/api/races/${raceId}/course-library`)).courses;
    } catch (error) {
      renderEmpty(qs('#library-list'), 'Could not load the course library', error.message);
      return;
    }
  }
  renderLibrary();
}

async function useLibraryCourse(button) {
  button.disabled = true;
  try {
    const result = await fetchJson(`/api/races/${raceId}/course/library`, {
      method: 'POST', body: { race: button.dataset.race, event: button.dataset.event },
    });
    closeModal(qs('#library-modal'));
    showToast(`Course saved: ${units.distance(result.course.length_m, 1)}, ${units.elevation(result.course.ascent_m)} of climbing`);
    loadPlan();
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

// ---- Events ----
qs('#pacing-form').addEventListener('submit', (event) => {
  event.preventDefault();
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
qs('#course-find').addEventListener('click', openLibrary);
qs('#library-search').addEventListener('input', () => {
  if (state.library) renderLibrary();
});
qs('#library-list').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-race]');
  if (button) useLibraryCourse(button);
});
qs('#gpx-upload').addEventListener('click', () => qs('#gpx-file').click());
qs('#gpx-file').addEventListener('change', async (event) => {
  const [file] = event.target.files;
  if (!file) return;
  try {
    const result = await uploadCourse(file);
    showToast(`Course saved: ${units.distance(result.course.length_m, 1)}, ${units.elevation(result.course.ascent_m)} of climbing`);
    loadPlan();
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    event.target.value = '';
  }
});
qs('#gpx-remove').addEventListener('click', async () => {
  try {
    await fetchJson(`/api/races/${raceId}/course`, { method: 'DELETE' });
    showToast('Course removed');
    loadPlan();
  } catch (error) {
    showToast(error.message, 'error');
  }
});
qs('#print-band').addEventListener('click', () => window.print());

loadPlan();
