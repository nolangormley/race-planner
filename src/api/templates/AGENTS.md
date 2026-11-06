# templates

Jinja2 pages rendered by `../routers/pages.py` and `../auth_routes.py`. `base.html` holds the document shell, the top bar (nav, Sync button, theme toggle, user links), the footer (Privacy and source links, on every page) and the `data-*` attributes on `<body>` that the page modules read. Pages fill `title`, `styles`, `content` and `scripts` blocks; auth pages also set `page_class` to `page-narrow`.

- `dashboard.html`, `streams.html` (one activity), `index.html` (activities list and workout library), `training_calendar.html`, `profile.html` (ends with the Delete account card and its modal, which `pages/profile-account.js` drives), `pacing.html` (one race's pacing plan, with the print-only pace band): logged-in pages. Their markup is placeholders that `../static/js/pages/*.js` fill. `data-athlete-id` is `user.athlete_id`, so the pages work for a user who only imports files; `strava_athlete_id` in a template only decides whether Strava controls (Sync button, connect prompt, Strava card) show.
- `privacy.html`: the privacy page, public, plain text in one narrow column (`css/pages/privacy.css`). Every claim on it is checked against the code; change it when the data the app stores or the services it talks to change.
- `login.html`, `register.html`, `setup.html` (first race; `pages/setup.js` turns its name field into the race search and fills the hidden catalog fields), `strava_auth.html`: the sign-up flow. Plain form posts; `register.html` has a small inline module that converts height and weight to meters and kilograms before submit.
- `forgot_password.html`, `reset_password.html`: the emailed password reset. Plain form posts; the reset token rides in a hidden field.

`coros_enabled` (a global set in `../main.py`) leaves the COROS card out of `profile.html` and the Recovery card out of `dashboard.html` when COROS is switched off.

Keep markup here and behaviour in the page modules; the only inline script is the theme bootstrap in `base.html` and the unit conversion in `register.html`.
