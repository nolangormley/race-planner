# templates

Jinja2 pages rendered by `../routers/pages.py` and `../auth_routes.py`. `base.html` holds the document shell, the top bar (nav, Sync button, theme toggle, user links) and the `data-*` attributes on `<body>` that the page modules read. Pages fill `title`, `styles`, `content` and `scripts` blocks; auth pages also set `page_class` to `page-narrow`.

- `dashboard.html`, `streams.html` (one activity), `index.html` (activities list and workout library), `training_calendar.html`, `profile.html`, `pacing.html` (one race's pacing plan, with the print-only pace band): logged-in pages. Their markup is placeholders that `../static/js/pages/*.js` fill. `data-athlete-id` is `user.athlete_id`, so the pages work for a user who only imports files; `strava_athlete_id` in a template only decides whether Strava controls (Sync button, connect prompt, Strava card) show.
- `login.html`, `register.html`, `setup.html` (first race), `strava_auth.html`: the sign-up flow. Plain form posts; `register.html` has a small inline module that converts height and weight to meters and kilograms before submit.
- `forgot_password.html`, `reset_password.html`: the emailed password reset. Plain form posts; the reset token rides in a hidden field.

Keep markup here and behaviour in the page modules; the only inline script is the theme bootstrap in `base.html` and the unit conversion in `register.html`.
