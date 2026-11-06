# js

ES modules, no bundler. `base.html` loads `app.js` on every page; each template loads its own module from `pages/`.

- `app.js`: `fetchJson` (JSON-encodes object bodies, passes `FormData` through as multipart, throws with the API's `detail`), `units` (mi/km preference from localStorage and all distance/pace formatting), date and duration formatters, toast, theme toggle, modal open/close, `config` (the `data-*` attributes on `<body>`: user id, athlete id, date of birth, sex).
  Planned distances are free text: `plannedMeters` reads the first distance in it the way the server's `planned_distance_km` does (a bare number is miles; a number with a duration, "45 min", is not a distance), and `units.planned` shows a text that is only a number of miles or km in the preferred unit and any other text as typed. The calendar and the dashboard's This week card show planned distances through it.
  `RUN_TYPES` is the one set of activities on foot: they show a pace, and their cadence doubles to steps per minute.
  `applyTheme` also sets the `theme-color` meta to the top bar color, so the browser bar follows the theme.
- `pacing-view.js`: a split plan as the pacing pages draw it: `renderSummary`, `renderProfile` (with the empty state the page passes), `renderSplits` and `renderBand` into the shared element ids, for `pages/pacing.js` and `pages/course.js`.
- `race-search.js`: `attachRaceSearch(input, { onPick, onEdit, source })`, the race name search over `/api/race-catalog`, or `/api/courses` for visitors (each fetched once a page): an ARIA combobox under the input (`<div class="race-search">`) with up to 8 suggestions where every typed word must appear in the race, event, city or state; arrow keys and Enter or a click pick one, Escape and blur close the list. `raceName` is the name a pick stores, `courseNote` the line that says whether its course map comes with it. Used by the profile race form and the sign-up setup page.
- `sync.js`: the top bar Sync button. Starts `POST /api/sync-strava/{id}`, polls `GET` on the same path, renders the floating progress panel and fires a `strava:synced` event on `document` when a sync finishes. Pages listen for it and reload their data.
  A page that starts a job another way (a recompute after a heart rate change) calls `refreshSync(true)`, which shows the result even when the job is already done.
- `charts/`: the chart module, see its own AGENTS.md.
- `pages/`: one module per template; they own fetching and rendering for that page.

Untrusted text (activity names, plan notes) goes through `escapeHtml` or `textContent`.
