// The first race at sign-up: picking a known race fills the distance and the hidden catalog fields, so the
// race is saved with its course map when the race-courses dataset has one.
import { qs } from '../app.js';
import { attachRaceSearch, courseNote } from '../race-search.js';

attachRaceSearch(qs('#race_name'), {
  onPick: (entry) => {
    qs('#race_length').value = entry.race_length;
    qs('#course_race').value = entry.race;
    qs('#course_event').value = entry.event;
    qs('#race-course-note').textContent = courseNote(entry);
  },
  onEdit: () => {
    qs('#course_race').value = '';
    qs('#course_event').value = '';
    qs('#race-course-note').textContent = '';
  },
});
