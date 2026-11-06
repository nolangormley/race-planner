// Home page for visitors: the race search takes them to the course page of the race they pick.
import { qs } from '../app.js';
import { attachRaceSearch } from '../race-search.js';

attachRaceSearch(qs('#home-search'), {
  source: '/api/courses',
  onPick: (entry) => window.location.assign(`/courses/${encodeURIComponent(entry.race)}/${encodeURIComponent(entry.event)}`),
  onEdit: () => {},
});
