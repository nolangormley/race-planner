// Race name search over the race-courses catalog: suggestions as the runner types, picked with the arrow
// keys and Enter or with a click. The catalog (/api/race-catalog, or the public /api/courses) is fetched
// once per page.
// Markup: <div class="race-search"><input id="…"></div>; the list of suggestions goes after the input.
import { fetchJson, escapeHtml } from './app.js';

const MAX_SHOWN = 8;
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
  'October', 'November', 'December'];
const catalogs = {};

function loadCatalog(source) {
  // A catalog that cannot be read leaves the field a plain text input.
  catalogs[source] ??= fetchJson(source).then((data) => data.races, () => []);
  return catalogs[source];
}

// The name to store: the event is added when the race's name does not already say it.
export function raceName(entry) {
  return entry.name.toLowerCase().includes(entry.event_name.toLowerCase())
    ? entry.name
    : `${entry.name} – ${entry.event_name}`;
}

export function courseNote(entry) {
  return entry.has_course
    ? 'Its course map comes with the race, so the pacing plan follows its hills.'
    : 'No course map for this race yet; you can upload one on its pacing page.';
}

// Every typed word must appear in the race name, event, city or state.
function matching(races, query) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (query.trim().length < 2) return [];
  return races.filter((entry) => {
    const text = `${entry.name} ${entry.event_name} ${entry.city} ${entry.state}`.toLowerCase();
    return words.every((word) => text.includes(word));
  }).slice(0, MAX_SHOWN);
}

// onPick(entry) runs when the runner picks a race, onEdit() when they type, so the page can forget a pick.
export function attachRaceSearch(input, { onPick, onEdit, source = '/api/race-catalog' }) {
  const list = document.createElement('ul');
  list.id = `${input.id}-races`;
  list.className = 'race-search-list';
  list.setAttribute('role', 'listbox');
  list.hidden = true;
  input.after(list);
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-controls', list.id);
  input.setAttribute('aria-expanded', 'false');
  input.autocomplete = 'off';
  let shown = [];
  let active = -1;

  function setActive(index) {
    active = index;
    [...list.children].forEach((option, position) => option.setAttribute('aria-selected', String(position === index)));
    if (index < 0) {
      input.removeAttribute('aria-activedescendant');
      return;
    }
    input.setAttribute('aria-activedescendant', list.children[index].id);
    list.children[index].scrollIntoView({ block: 'nearest' });
  }

  function close() {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    setActive(-1);
  }

  function render() {
    // The event comes first in the meta line: two events of one race share its name when the name says both.
    list.innerHTML = shown.map((entry, index) => `<li role="option" id="${list.id}-${index}" aria-selected="false" data-index="${index}">
      <span class="race-search-name">${escapeHtml(raceName(entry))}</span>
      <span class="race-search-meta">${escapeHtml(entry.event_name)} · ${escapeHtml(entry.city)}, ${escapeHtml(entry.state)}${entry.month ? ` · ${MONTHS[entry.month - 1]}` : ''}${entry.has_course ? ' · course map' : ''}</span>
    </li>`).join('');
    list.hidden = !shown.length;
    input.setAttribute('aria-expanded', String(shown.length > 0));
    setActive(-1);
  }

  function pick(index) {
    const entry = shown[index];
    input.value = raceName(entry);
    close();
    onPick(entry);
  }

  input.addEventListener('input', async () => {
    onEdit();
    const query = input.value;
    const races = await loadCatalog(source);
    if (input.value !== query) return; // a later keystroke renders its own list
    shown = matching(races, query);
    render();
  });
  input.addEventListener('keydown', (event) => {
    if (list.hidden) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive(active < 0 ? (step > 0 ? 0 : shown.length - 1) : (active + step + shown.length) % shown.length);
    } else if (event.key === 'Enter' && active >= 0) {
      event.preventDefault();
      pick(active);
    } else if (event.key === 'Escape') {
      close();
    }
  });
  input.addEventListener('blur', close);
  // mousedown, not click: the input keeps the focus, so its blur does not close the list first.
  list.addEventListener('mousedown', (event) => {
    const option = event.target.closest('[role="option"]');
    if (!option) return;
    event.preventDefault();
    pick(Number(option.dataset.index));
  });
}
