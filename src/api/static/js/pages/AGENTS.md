# pages

One module per template. Each reads its ids from `config` in `app.js`, fetches the page's data, renders into the template's placeholders, and listens for `strava:synced` to reload.

- `dashboard.js`: status tiles from `/status`, the fitness/form panel chart, weekly volume, heart-rate zones and time-by-type from `/analytics`, VO2 max from `/vo2max`, pace zones, this week's plan next to actual activities, recent activities, the coach's note, and the unit toggle.
- `streams.js`: an activity's summary strip, stream panels with pace-zone shading from `/pace_zones`, effort metrics and time in zones from `/effectiveness`, optional AI note.
- `activities.js`: the sortable, filterable activity table with delete, and the workout library CRUD behind the second tab.
- `calendar.js`: the month grid of plan entries and Strava activities, the add/edit modal with attach-to-activity, the month summary meter and the upcoming list. `plannedMeters` parses free-text planned distances.
- `profile.js`: athlete details edit, password change, unit and theme preferences, races CRUD, Strava status with last sync, warehouse totals.
