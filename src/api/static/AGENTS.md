# static

Everything the browser loads, served by FastAPI at `/static` with `Cache-Control: no-cache` so a deploy is picked up on the next load.
There is no build step: plain CSS and ES modules.

- `css/`: design tokens, base styles, shared components, one stylesheet per page under `css/pages/`.
- `js/`: shared helpers (`app.js`), the Strava sync progress UI (`sync.js`), the chart module (`js/charts/`), one module per page (`js/pages/`).

Templates in `../templates/` load `tokens.css`, `base.css`, `components.css` and `app.js` on every page, then their own page stylesheet and page module.
