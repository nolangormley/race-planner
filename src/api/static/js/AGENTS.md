# js

ES modules, no bundler. `base.html` loads `app.js` on every page; each template loads its own module from `pages/`.

- `app.js`: `fetchJson` (throws with the API's `detail`), `units` (mi/km preference from localStorage and all distance/pace formatting), date and duration formatters, toast, theme toggle, modal open/close, `config` (the `data-*` attributes on `<body>`: user id, athlete id, date of birth, sex).
- `sync.js`: the top bar Sync button. Starts `POST /api/sync-strava/{id}`, polls `GET` on the same path, renders the floating progress panel and fires a `strava:synced` event on `document` when a sync finishes. Pages listen for it and reload their data.
- `charts/`: the chart module, see its own AGENTS.md.
- `pages/`: one module per template; they own fetching and rendering for that page.

Untrusted text (activity names, plan notes) goes through `escapeHtml` or `textContent`.
