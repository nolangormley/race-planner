// Course list: the filter shows only the courses whose race, city or state contains every typed word.
import { qs, qsa } from '../app.js';

const filter = qs('#course-filter');
// Without the course library the page has no list and no filter.
filter?.addEventListener('input', () => {
  const words = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
  let shown = 0;
  qsa('.course-row').forEach((row) => {
    const match = words.every((word) => row.dataset.search.includes(word));
    row.classList.toggle('hidden', !match);
    shown += match ? 1 : 0;
  });
  qs('#course-empty').classList.toggle('hidden', shown > 0);
});
