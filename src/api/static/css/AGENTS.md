# css

- `tokens.css`: every color, radius and font as a CSS custom property. Dark is the default; `[data-theme="light"]` on `<html>` swaps the values. Series and status colors come from a palette validated for color-vision deficiency; do not tune them by eye.
- `base.css`: reset, typography, top bar, page head, layout helpers (`grid-2`, `grid-main`, `stack`, `row`), auth page frame.
- `components.css`: cards, buttons, segmented controls, forms, tables, badges, stat tiles, progress/meter, the floating sync panel, modal, toast, skeleton and empty states, and the chart chrome classes the chart module uses.
- `pages/`: rules that only one page needs, named after the template.

Add a token before adding a literal color anywhere else.

## Phones and touch screens

Every page fits a 360 px screen; `e2e/test_layout.py` checks the logged-in pages at 390 and 360 px.

- Grid columns are `minmax(0, 1fr)`, never a bare `1fr`.
  A bare `1fr` track cannot get narrower than its widest content, so one wide table widens the whole page.
- A table that can be wider than a phone goes inside `.table-wrap`, which scrolls sideways inside its card.
  Put the class on the element the page module renders the table into, as `#predictions` and `#splits` do.
- Touch sizes go in a `@media (pointer: coarse)` block, so the mouse layout stays the same.
  Controls are at least 36 px (chart legend toggles 32 px).
  A link inside text gets a taller target from padding and an equal negative margin.
- At 560 px and below, card actions wrap under the title when they would squeeze it under 160 px, and a segmented control always takes its own line.
- At 1000 px and below the top bar drops the brand name and the sync time.
  At 720 px and below it shows the three nav links, an icon-only Sync button and an icon-only profile link; the theme toggle and Log out move to the profile page's Settings card.
