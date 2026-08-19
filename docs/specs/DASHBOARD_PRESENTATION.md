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

## Operational incident hierarchy

- The Health page combines normalized local and current Assistant events into
  one incident summary. Preview must not present production Assistant D1 events
  as branch-current evidence.
- Incident cards lead with a human title, action state, root-cause summary,
  bounded metrics, and affected components. Raw codes and evidence remain
  reachable through a `查看技术详情` disclosure instead of leading the layout.
- Local scheduler and Assistant D1 queue grids remain separate technical
  diagnostics below the incident summary. Unified presentation does not merge
  their execution or storage planes.
- Technical disclosure controls have a minimum 44 CSS-pixel target and expose
  native keyboard and `aria-expanded` state. Cards and technical evidence must
  not cause horizontal overflow at desktop, 390x844, or 360x800.

## Operator-facing time

- Durable records and API payloads retain canonical timezone-aware timestamps.
- Dashboard copy and diagnostic evidence render timestamps as readable local
  date-times in the fixed `Asia/Kuala_Lumpur` (UTC+8) operator zone.
- Raw ISO 8601 values are not a user-facing presentation. Automatic browser
  timezone detection is intentionally avoided because server rendering and
  hydration must produce the same value.

## Canonical system state

- Status copy has one presentation owner shared by Live Room, Audit, Status,
  and Health.
- API read freshness, live-market readiness, and operational health are
  separate axes. A refresh failure must not relabel cached operational or
  market evidence as system offline.
- A failed refresh with a prior snapshot says that the update failed and shows
  the last status time. With no successful snapshot, the factual label is
  status unavailable.
- Market closure is not an operational error. Live quote/decision
  unavailability is labeled as a live-path condition, not as global system
  health.

## Dashboard shell and global navigation contract

The dashboard has one product shell. `DashboardApp` supplies the current
location and page content to that shell; individual Views do not recreate any
part of it.

1. The canonical product brand is `AU`, `AURUM SIGNAL ROOM`, and
   `XAUUSD · Forward-only intelligence`. It is identical on every top-level
   View and links to the realtime room.
2. The canonical global destination order is `总览`, `新闻与决策`,
   `Assistant`, and `系统`. Their stable entry routes are `/`,
   `/audit?view=news`, `/assistant`, and `/health` respectively.
3. Desktop and mobile navigation derive labels, order, routes, and active
   grouping from the same typed global destination definition. Mobile must not
   maintain a parallel product taxonomy.
4. Individual Views MUST NOT define the global product header, product brand,
   global destination ordering, global system-state placement, or top-level
   shell spacing and borders.
5. Page identity belongs inside page content. A System or Audit page heading
   must not replace the product identity in the global header.
6. Section navigation is subordinate to global navigation. Audit tabs remain
   inside Audit. System exposes `系统健康` (`/health`), `重试任务`
   (`/retry-jobs`), and `AI 模型用量` (`/status`) through one shared secondary
   navigation while all three routes keep the top-level `系统` destination
   active.
7. Global destination labels and order are product contracts. A back-style
   action such as `返回实时室` is not a global destination.
8. The global system-state indicator has one shell-owned location and consumes
   the shared `/api/status` dashboard resource. It must not create a competing
   polling or interpretation path.
9. New top-level Views plug into the canonical shell. Copying an existing
   shell-level structure instead of extending its owner is design drift.
10. Deterministic source and rendered-route contracts must prevent design
    drift; human review and memory are not sufficient enforcement.
11. `重试任务` is a dedicated privileged System workspace. System Health does
    not render or anonymously fetch the retry queue. The retry workspace shows
    human-readable task context, state, failure, attempt count, current UTC+8
    schedule, and automatic-versus-operator provenance. Technical job IDs
    remain visually secondary. Desktop, 390x844, and 360x800 layouts keep every
    control reachable without horizontal overflow, and every scheduling control
    has a minimum 44 CSS-pixel target. Checkbox labels must make the complete
    44x44 area interactive; a 20px input centered inside a non-interactive
    layout box does not satisfy this rule.
12. Advancing work uses a lightweight confirmation that states the selected
    count and says the work becomes scheduler-claimable, not immediately
    executed. Custom datetime input is explicitly UTC+8.
13. Retry work is a dense operator queue, not a stack of presentation cards.
    Every collapsed row leads with task, failure, failure time, schedule and
    provenance, latest operator command state, and one compact adjustment entry
    point. Long scheduling descriptions and technical job IDs stay inside the
    single expanded row. The bulk bar remains quiet until selection. Restore-
    original is offered only when an active override exists. `IDLE_CAPACITY`
    copy describes yielding within the scheduler pool for 30 minutes; it never
    claims to sense machine, CPU, or provider idleness.

## OOS chart windows

- The long OOS chart's 24-hour, 7-day, and 30-day ranges are elapsed XAUUSD
  market-open time, not wall-clock time. The labels must state this explicitly.
- The expected weekly closure defined by the forward-only market-session
  contract does not consume a chart window. A 24-hour range opened after the
  weekend therefore carries backward into Friday's open session.
- Missing observations during scheduled open time still consume the window and
  remain visible as data gaps. The UI must not treat every absence as a market
  closure or silently replace missing evidence with older points.
- Counts above the chart describe the full retained history and must be labeled
  as full-history totals rather than current-window totals.
- The 24-open-hour view may show complete version badges. Denser 7-open-day,
  30-open-day, and full-history views use one event rail instead of stacking
  badge lanes above the chart.
- Event-rail clustering is presentational only: every version event remains
  represented in the aggregate event count. Dense rails are intentionally
  non-interactive and must not add hover tooltips or a separate detail strip
  that obscures the chart or requires coordinated hovering and scrolling.
