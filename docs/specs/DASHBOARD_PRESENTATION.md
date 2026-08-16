# Dashboard Presentation Specification

This specification defines the required visual behavior of the dashboard's
data-dense navigation, metric grids, tables, and expandable evidence panels.
It applies to both desktop and phone layouts.

## Grid boundaries

- A bordered grid has one continuous outer boundary and one visible one-pixel
  divider at every logical row and column boundary.
- A full-width row following metric cells, such as a technical-status
  disclosure, has an explicit top divider across the complete grid width.
- Selection, status, and focus accents supplement the grid boundary. They must
  not replace, hide, or change ownership of a structural divider.
- Structural dividers must be explicit borders owned by the relevant cells or
  rows. A grid must not depend on `gap` exposing the container background as
  its only divider mechanism; that approach becomes ambiguous when the number
  of columns, an incomplete row, or a spanning row changes.
- Responsive layouts may reorganize a grid into cards, but each viewport owns
  a complete boundary model. Desktop border assumptions must not leak into the
  phone card layout, and phone overrides must not erase desktop boundaries.

## Change review

When a change adds, removes, reorders, spans, or hides a grid item, review the
complete component rather than only the reported edge. The review includes:

1. every outer edge and internal divider;
2. first, middle, last, incomplete, and full-width rows where applicable;
3. selected, hover, focus, expanded, collapsed, loading, and empty states;
4. desktop, 390x844, and 360x800 deployed Preview layouts; and
5. sibling grids that share the changed selector or presentation rule.

Automated coverage must protect the boundary ownership rule. Deployed Preview
verification remains required because source-level CSS assertions do not prove
that a visible line is continuous after cascade and responsive overrides.
