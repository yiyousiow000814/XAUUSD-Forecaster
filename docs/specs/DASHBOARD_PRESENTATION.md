# Dashboard Presentation Specification

This specification defines the required visual behavior of the dashboard's
data-dense navigation, metric grids, tables, and expandable evidence panels.
It applies to both desktop and phone layouts.

## Article reading status

Article badges describe processing, reference type and the existing validity
window, not model access or confirmed membership in current events. Show an
active assessed article as within its validity window; expired windows as past
their validity window; and duplicate reports, commentary and background as
reference material, without error styling. Pending assessment must not appear
active. Unknown states remain unconfirmed rather than exposing storage codes.
Current events retain their independent consolidation and eligibility rules.
Keep stored visibility codes, APIs and filtering unchanged when changing these
reader-facing labels. An expired window does not claim the real event is over.

## Header status and login placement

The public status link owns hover, current-page and keyboard-focus treatment.
Keep padding around its dot and text; do not draw a tightly fitted pill outline
inside the link. All states retain a 44px interaction height on phones.
The login explanation remains a viewport-centered modal. Its separate login
window requests centering relative to the current browser window, including
negative coordinates on secondary monitors, with dimensions bounded by the
opener. Browser window-placement policy may adjust the requested geometry.
Popup-blocked fallback and authentication authority remain unchanged.

The Admin entry retains the same right-aligned desktop slot and width before
and after authentication; only its label and login/link behavior change. Admin
uses the shared sans-serif typography, light surfaces and teal selection. Its
overview has independent rounded cards, each with a complete border on desktop
and phones. Authentication and private resource permissions do not change.

## Navigation loading

Switching audit subviews keeps the mounted tab bar and content owner visible;
only the selected content changes, with resource-specific loading as needed.
Same-room clicks and history navigation invalidate older room imports without
starting a room skeleton or resetting saved filters and paging.
The global operational warning banner is omitted; the status entry and health
page retain access to operational evidence.

Room imports show a bounded skeleton inside the content area, below the shared
header. Do not use travelling navigation underlines or viewport-height loading
lines. Keep the previous view mounted but hidden until the active navigation
succeeds; restore it with the existing retry action on failure. Superseded
requests cannot clear or replace the current pending state. The initial empty
news read uses list skeletons; cached rows remain readable during refresh.
Skeletons announce loading once, respect reduced motion, and fit phone widths.

## Retry tasks

The retry page shows supported unresolved failed jobs and active manual schedule
adjustments, not ordinary initial work or quota/prerequisite deferrals. The
scheduler automatically retries eligible jobs. Successful and superseded jobs
leave the current list; historical attempts remain evidence. Refresh the live
queue every 15 seconds, with faster refresh while a submitted command is pending.
Bounded results are labelled as the displayed count, not the total backlog.
Decision-news freshness remains bound to its decision. After service recovery,
an expired observation is pending only until the collector's existing first
eligible grid deadline, and only while the collector is healthy. Beyond that
deadline it is stale; never refresh the timestamp of historical evidence.

## Grid boundaries

The overview retains its quote banner and two equal news columns. Each of the
latest three brief items shows its original headline and up to two lines of the
existing reader-safe summary. Current events show up to two headline lines and
their media publication time in the fixed operator timezone; missing or invalid
times remain explicitly unavailable. Neither list uses ranking numbers. Full
text remains available through the existing brief and event destinations.
Phone layouts stack the same sections without adding nested feature cards.
The news panel uses an explicit Chinese sans-serif font stack and compact title,
summary, and metadata sizes. Desktop lists distribute available height across
their rows, keeping dividers aligned when both columns have three items instead
of leaving unused space below the shorter list. Phone rows use natural heights.

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

- The public Health page summarizes the bounded public operational event set.
  It does not fetch private Assistant D1 health or Admin evidence. Preview must
  not present production Assistant D1 events as branch-current evidence.
- Incident cards lead with a human title, action state, root-cause summary,
  bounded metrics, and affected components. Raw codes and evidence remain
  reachable through a `查看技术详情` disclosure instead of leading the layout.
- The Health page order is current problems, system components, news sources,
  then scheduler and technical evidence. Its top summary exposes one dominant
  operator action (`ACTION_REQUIRED`, `AUTO_RECOVERING`, or `MONITORING`) rather
  than presenting mutually exclusive actions as independent counters.
- Abnormal component and source records sort first and remain prominent. Healthy
  records use compact scan rows with redundant symbol, restrained color, and
  text status encoding. Unknown or unavailable states remain neutral and must
  not look healthy.
- Component and source inventories use two compact columns at normal desktop
  widths and one column on phones. Human roles, exact local timestamps, raw
  identifiers/status, errors, transport copy, and evidence counts remain
  reachable through per-record details instead of expanding every healthy row.
- Public local scheduler evidence remains in the secondary
  `调度器与技术状态` disclosure. Private Assistant D1 queue evidence belongs
  in Admin and is never fetched to render public Health.
- Technical disclosure controls have a minimum 44 CSS-pixel target and expose
  native keyboard and `aria-expanded` state. Cards and technical evidence must
  not cause horizontal overflow at desktop, 390x844, or 360x800.

## Architecture graph interaction scale

Graph node buttons retain at least a 44x44 CSS-pixel target after the canvas
transform, including the thicker selected/failure borders. A pre-transform CSS
minimum alone is insufficient. Automatic Fit, manual Fit, user zoom and custom
mobile framing share the same minimum interaction scale; mobile may retain its
higher readability floor.

Large source graphs need not fit completely on one screen at that scale. Keep
panning, zooming, search, keyboard selection and source/detail navigation
available rather than shrinking targets to force the whole graph into view.
Verify actual transformed targets on desktop, 390x844 and 360x800 Preview surfaces
after initial Fit and at minimum user zoom. Source/layout math is supporting
evidence, not a substitute for deployed geometry.

Canvas overlays reserve separate hit regions: desktop zoom/Fit controls use the
top-left, the MiniMap the bottom-right, the legend the bottom-left and keyboard
hints the top-right. Mobile keeps its existing MiniMap exclusion and controls
above the legend. Verify actual control clicks as well as target dimensions;
raising one overlay's z-index must not merely block a different control.

Inspector and Advanced share one header/body layout. Long source identifiers
wrap inside the title's available width without truncating their identity or
pushing the nonshrinking 44x44 close control outside the panel. The scrollable
body consumes the actual space below the header, not a fixed deduction for a
presumed one-line title. Check long function/component names, the final body
control, close and focus return on desktop, both phone widths and short
landscape; wrapping must not trade horizontal clipping for unreachable content.
While either mobile sheet is open, reserve the measured top Preview-banner band
as well as the safe area. The mandatory Preview identity stays visible above the
sheet; neither its height nor the number of banner instances is assumed. Ignore
unrelated in-page banners below that band. Remeasure on viewport/banner changes,
and stop observation on close. On short landscape, verify the entire close target
with corner hit-tests, not only its center.

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

1. The canonical product brand is `黄金资讯` and links to the overview.
   Public overview, news, health and private Admin share the C visual system: gold/navy brand,
   white header, cool light background, sans-serif typography, teal selection
   and rounded white content panels. All route changes preserve header
   geometry and navigation controls. Content density may differ by page.
2. The public global destination order is `总览`, `新闻与事件`, and
   `管理员登录`. The public reading routes are `/` and `/audit?view=news`.
   The shell status link opens `/health`. `管理员登录` first opens a local
   explanation dialog; only its explicit Google login action performs a normal
   browser navigation to the Access-protected `/admin` route.
3. Desktop and mobile navigation derive labels, order, routes, and active
   grouping from the same typed global destination definition. Mobile must not
   maintain a parallel product taxonomy.
4. Individual Views MUST NOT define the global product header, product brand,
   global destination ordering, global system-state placement, or top-level
   shell spacing and borders.
5. Page identity belongs inside page content. A System or Audit page heading
   must not replace the product identity in the global header.
6. Section navigation is subordinate to global navigation. Audit tabs remain
   inside Audit. Public System contains only System Health and therefore has no
   redundant one-item secondary navigation. Admin owns one shared secondary
   destination model: overview (`/admin`), Assistant (`/admin/assistant`),
   Retry Jobs (`/admin/retry-jobs`), and AI Model Usage
   (`/admin/ai-usage`).
7. Global destination labels and order are product contracts. A back-style
   action such as `返回实时室` is not a global destination.
8. The global system-state indicator is shell-owned: public desktop header
   and public phone header. Both consume the same `/api/status` resource and
   interpretation owner; responsive placement must not add a polling owner.
   Public phones use the same native disclosure menu on all reading routes.
   The current destination is marked; Escape closes the menu and returns focus,
   and selecting a destination closes it. Admin uses the same phone menu; its
   secondary workspace controls remain below the shared header. Audit section
   selection remains subordinate inside the page.
9. New top-level Views plug into the canonical shell. Copying an existing
   shell-level structure instead of extending its owner is design drift.
10. Deterministic source and rendered-route contracts must prevent design
    drift; human review and memory are not sufficient enforcement.
11. `重试任务` is a dedicated privileged Admin workspace. System Health does
    not render or anonymously fetch the retry queue or Assistant health state.
    The retry workspace shows
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
14. Public status responses exclude provider quota ledgers and routing fields.
    The full AI-usage payload is read only from the owner-authenticated Admin
    endpoint. Hiding Admin navigation is presentation, never authorization.

## Audit content and navigation

- DashboardApp owns the selected Audit URL and browser history. Audit tabs and
  the phone picker use that navigation owner; refresh and back/forward must
  restore the same selected view.
- Every selected Audit view displays content, an explicit loading state, a
  genuine empty result, a resource-specific error with retry, or an identified
  previous snapshot. Initial idle is pending, never a blank result.
- An unavailable or malformed resource is not an empty result. An empty page
  does not redefine a full-history headline count as zero. Resource failures
  remain independent and preserve previously accepted sibling results.
- The footer reports the selected resource's supplied timestamp. A status
  heartbeat must not advance the timestamp of audit details, news, or learning.
- Collection/training counts describe sample and retraining progress, not
  profitability or model capability. Coverage counts describe configured
  coverage, not proof that every source is currently healthy.

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

## Architecture explorer

Architecture content uses the shared DashboardApp navigation and DashboardShell,
including the active admin destination, and the global paper/ink/type tokens.
Graph relationships remain selectable and available in the relationship list
and inspector. Do not overlay floating text labels on graph edges: selecting a
node with many relationships must not cover nodes or neighboring controls.

Architecture borders use an explicit line token, never the surface token.
Inspect every outer edge and internal divider after palette changes. Mode
switches and breadcrumbs start beneath the title at the left. Advanced evidence
panels use at least 12 CSS pixels for labels and explanatory copy.

The graph groups disclosed source references only when direction, endpoints,
relationship kind and criticality match, retaining all individual source IDs in
its detail view. Hidden references cannot enter a visible group. Arrows denote
source references, not distinct business flows. Unresolved architecture evidence
is labeled as awaiting confirmation, never as incomplete operational work.
The shared admin overview links to every admin tool, including architecture.

## Interactive system map

The private architecture page starts with reviewed product data flows, with
child diagrams for quotes, news, forecasting, training and dashboard sync.
Each node and relationship retains a current-source witness and a commit-bound
source link. These establish configured structure, not live execution or health.
The bounded map is maintained with `SYSTEM_ARCHITECTURE.md`; source-witness drift,
invalid endpoints and unreachable child views fail the web contract tests.
The syntactic index remains optional source inspection and cannot substitute
for the product map or turn unknown dispatch into verified runtime evidence.

Use one compact heading and breadcrumb row. Avoid identical mode switches and
build provenance blocks in the primary flow. Desktop uses connected nodes;
phones preserve explicit outgoing relationships without shrinking labels.
The shared status pill is the health navigation entry; remove its duplicate
System tab. Status labels remain four characters and health axes remain intact.
Align the content and header dividers to the same shell gutters.

The optional source index uses a compact desktop header: identity and summary
on the left, mode and breadcrumbs sharing the available right-hand space.
It inherits the shell gutter without a second inset. Admin navigation bottom
borders belong to the actual tabs, not the empty leading spacer. On phones,
controls wrap while retaining their minimum target size.


## Overview and story reading

The overview presents a prominent quote, a featured brief and a lighter current
news list. Each news section keeps at most three headlines; full prose belongs
in the retained detail pages. Phone layouts stack the sections without reducing
headlines to tiny dashboard labels.

Story cards start collapsed on desktop and phones. The initial card contains
the title, latest development, update time and an explicit disclosure control.
Evidence coverage, attachments and the complete timeline appear on expansion.
Display timeline entries newest first by event time, then publication time or
first-seen time when unavailable; preserve source records and relation labels.
Unknown times sort last. Expanded chains use page scrolling, not a separate
fixed-height scrolling region. Collapsing restores the compact card.

## Public reading density and interaction

The daily brief page title is the selected brief date followed by
`黄金市场简报`, regardless of generation phase or generated editorial title.
Audit pagination uses previous/next chevron icons with accessible names,
disabled boundary states and 44px targets. The redundant statistics-rules
disclosure is omitted from the current-events reading surface.

Public reading pages omit the redundant branded footer; navigation and status
remain in the shared header. On phones, news uses compact divided rows rather
than large individual cards. Collapsed rows show the headline, category and
publication time; opening restores the complete headline, source, receipt time,
processing status and article detail. Filters, paging and disclosures retain
44px interaction targets. Secondary statistics do not dominate the first screen.

Public hover feedback uses a short color transition, without a mouse-only focus
outline. Keyboard focus remains visible inside rounded boundaries. Reduced
motion disables these transitions. Scrollbar chrome is hidden throughout the
app while existing scrolling, keyboard navigation and scroll ownership remain.

## Macro reading and story diagnostics

The retained `/audit?view=coverage` URL is labeled `宏观数据`. It presents six
observed series: two-year Treasury yield, ten-year TIPS yield, the Fed broad
Dollar index, WTI spot, Federal Reserve total assets and VIX. Each card exposes
its precise series identity, value, unit and individual observation date.
WALCL values in USD millions are divided by one million for USD trillions.
Missing, non-finite or incompatible-unit values remain unavailable, never zero.
These are background indicators, not directional signals or a live-data claim.
Collector states and the legacy 11/11 coverage score are not shown here.
Underlying collection and API provenance are unchanged; Preview labels remain.

Story reading omits deployment hashes and version-verdict banners. Release
identity and operational diagnosis belong to their existing owners, not a
story card. Removing the presentation does not fabricate a matching revision
or weaken deployment checks.


## Current news event reading

The retained `当前可用新闻事件` destination presents the existing current,
consolidated event selection as a compact reading list, newest publication
first. Headlines and categories are visible immediately; reporting domains,
independent-source counts and receipt time are available in a native disclosure.
Headlines remain complete on phones. Source disclosures and pagination retain
44px targets and keyboard operation.

This reader omits model use, prediction counts, training statistics, eligibility
badges, historical-use filters and the prediction-audit table. The existing
`eligible` transport selector and retained selection policy remain unchanged;
this presentation change does not change event admission or delete historical
records. `新闻` remains the full article archive. The current event payload does
not supply article summaries or original article URLs, so the reader must not
invent those fields or promise a nonexistent full-article destination.
