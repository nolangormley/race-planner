# charts

A small SVG chart module. Colors are CSS tokens, so charts follow the theme without re-rendering; sizes follow the container through a `ResizeObserver`.

- `core.js`: SVG element helpers, linear scale, nice ticks for numbers, dates and elapsed seconds, the hover `Tooltip`, `observeWidth`, `roundedBarPath` (rounded data end, square baseline) and `spreadLabels` (pushes colliding end labels apart).
- `panels.js`: `panelChart` draws stacked panels that share one x-axis and one crosshair. A panel is `kind: 'line'` (several series, optional area wash, dashed projection, end labels, shaded y bands) or `kind: 'columns'` (diverging bars around zero). Chart-wide `markers` (vertical lines) and `xBands` (x ranges shaded behind every panel, such as the reps of an interval session, with an optional label in the first panel). It renders a legend with toggle-to-hide, a table view of the same data, and supports arrow-key hover. Used for fitness/form on the dashboard and the stream panels on an activity page.
  End labels go only on series that reach the right edge, so a history line that stops where a projection starts has none.
  Marker labels sit above the first panel, in a row of their own when that panel has no title, and read leftward from their line in the right half of the chart.
- `bars.js`: `barList` (horizontal bars with the value at the tip), `stackedBar` (one part-to-whole bar with a legend), `columnChart` (a short category series with one emphasized column) and `sparkline`.

Rules the module follows, from the dataviz spec used to design it: one y-axis per panel, 2px lines, bars at most 24px thick, hairline solid gridlines, a 10% area wash at most, labels selective rather than on every point, a legend for two or more series, and every chart readable without hovering (end labels, bar values, or the table view). Series slots: `series-1` blue for the main measure (fitness, pace), `series-2` orange for fatigue and heart rate, `series-neg` red for negative form, the `ramp-1..5` blues for ordinal heart-rate zones, `series-muted` for context.
