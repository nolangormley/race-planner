# css

- `tokens.css`: every color, radius and font as a CSS custom property. Dark is the default; `[data-theme="light"]` on `<html>` swaps the values. Series and status colors come from a palette validated for color-vision deficiency; do not tune them by eye.
- `base.css`: reset, typography, top bar, page head, layout helpers (`grid-2`, `grid-main`, `stack`, `row`), auth page frame.
- `components.css`: cards, buttons, segmented controls, forms, tables, badges, stat tiles, progress/meter, the floating sync panel, modal, toast, skeleton and empty states, and the chart chrome classes the chart module uses.
- `pages/`: rules that only one page needs, named after the template.

Add a token before adding a literal color anywhere else.
