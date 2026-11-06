# email

The HTML versions of the app's emails, rendered with the shared Jinja environment (`../../web.py`, autoescaped) and sent next to a plain text version by `../../services/email.py`.
Mail clients drop most CSS, so the layout is tables and every style is inline; keep it that way.

- `components.html`: the light-theme colours of `static/css/tokens.css` (status tones blended over white, since some clients cannot blend rgba), and the macros every email uses: `text` (type as longhand properties, because Outlook ignores the `font` shorthand), `heading`, `section`, `stat`, `meter`, `badge` and `button`.
- `base.html`: the frame: the hidden preheader the inbox shows next to the subject, the logo from `app_url` (left out without one), the white card and the footer. Emails fill `title`, `content` and `footer`.
- `digest.html`: the weekly digest, filled by `digest_html` in `../../services/digest.py`.
- `password_reset.html`: the password reset, filled by `send_reset_email` in `../../auth_routes.py`.

Before changing a layout, render the email with its service function and look at it in a browser at desktop and phone widths.
The profile page's digest Preview shows the same HTML.
