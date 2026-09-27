# Public dashboard design review

## Latest follow-up: article reading status

Presentation-only scope: NewsRow maps existing visibility and impact codes to
processing/reference/validity labels. The article producer still calls
apply_impact_status; consolidated event membership still uses identity,
timeliness, evidence, topic, semantic relevance and event-clock/lifetime checks
in news/semantics/evidence.py. No producer, stored code, API, review-state
invariant or eligible-event query changes. An article validity badge must not
assert consolidated event membership or real-world event completion. Verify
active, pending, expired, reference and unknown states, unchanged input records,
news expansion/pagination and current-event navigation on desktop and phones.

Verified application revision: `26b266f0`.
Preview: https://7fa118c8-aurum-signal-room.yiyousiow1234.workers.dev/audit?view=news.
Production/Preview builds and 170 rendering, content, event-store and news
projection checks passed. The rendered-row regression covers 18 combinations,
including pending assessment under MODEL_VISIBLE, known reference reasons,
missing content and unknown codes. It verifies unchanged input records and no
public prediction-model permission wording. Architecture source/input and doc
checks passed. Final diff review confirmed all executable changes are confined
to NewsRow presentation and badge CSS; event eligibility and persistence are
unchanged. The brief footer's obsolete training disclaimer is now reading copy.

Deployed Preview checks: desktop 1280x900, phones 390x844 and 360x800, no horizontal
overflow. Inspected the full list's first/last rows and dividers; active, expired,
duplicate, background and commentary labels are present. Expanded article detail
shows News Status with the same label, without model-access copy. Phone flow
included article open, supporting-evidence expand/collapse, close, next/previous
news page, current-events selection and return. The retained event view still
reports 41 events and renders its available snapshot details. A desktop attempt
to select the phone-only supporting-evidence button was corrected after observing
that desktop already displays this section. No browser warnings/errors.
Task tab closed and viewport reset; remaining task-created sessions: 0.
Screenshots: `C:/Users/yiyou/AppData/Local/Temp/news-reading-labels.png` and
`C:/Users/yiyou/AppData/Local/Temp/news-reading-phone.png`.
Production has not been activated.

## Latest follow-up: header status and login placement

Scope: public status-link selected/hover/focus presentation and the geometry
passed to the existing login popup. Authentication, trusted-message checks,
callback URL, popup ownership/polling and blocked-popup fallback are unchanged.
The opener supplies its live window geometry; negative monitor coordinates
remain valid. Check desktop and both phone viewports, active/inactive status,
modal open/cancel/Escape and popup geometry regression.

Verified application revision: `b33861aa`.
Preview: https://36a1cb78-aurum-signal-room.yiyousiow1234.workers.dev/health.
The preceding Preview reproduced the selected-state defect: 58px-wide inner
pill, zero horizontal padding and an inset outline. The inner modal was already
centered at 390x844 and 1280x900; the separate login window had no left/top
features. The public link now owns its selected background and 10px side padding;
the inner pill no longer draws that outline. Private header styling is retained.

Production and Preview builds passed; 105 rendering/auth-session/admin-client
checks passed. Popup tests cover primary and secondary monitors (including
negative coordinates), smaller windows, unchanged callback/target, and blocked
popup fallback. The production caller supplies the current window. Architecture
input/document checks and whitespace checks passed. Final diff review checked
the only popup call site, unchanged trusted-message/session authority, timer and
close handling, and public/private CSS cascade.

On the deployed Preview, checked 1280x900, 390x844 and 360x800. Status target is
78x44 on the 390px phone, with the dot 10px inside its left edge; selected shadow
is none and background is light teal. Verified inactive state, actual mouse hover,
keyboard focus outline, status-page navigation and reduced-motion transition 0s.
No horizontal overflow. Opened login from desktop and phone menu, closed with
Cancel and Escape, and returned to overview. Modal centers measured (640,450),
(195,422) and (180,400), respectively. No browser warnings/errors. Temporary
media and viewport overrides were cleared; all task tabs closed, remaining task
sessions: 0.

The immutable Preview intentionally hides the real Google login action. Native
OS placement of the external login window and a complete authenticated handoff
were not exercised; geometry and fallback are covered by tests, not represented
as a completed production login. Browser policies may adjust window coordinates.
Screenshots: `C:/Users/yiyou/AppData/Local/Temp/status-button-fixed.png` and
`C:/Users/yiyou/AppData/Local/Temp/admin-login-centered.png`.

## Latest follow-up: navigation loading presentation

Boundary: transient dashboard navigation presentation only. DashboardApp remains
the owner of route selection, the navigation sequence and load failure recovery.
Actors: link clicks, preloads, browser popstate and dynamic-import completion.
The existing sequence rejects stale completions. Pending destination is cleared
only by the current success/failure; the prior view stays mounted but hidden so
failure restores its local state. No API, persistence, background owner, schema,
authentication or release authority changes. Reload follows existing routing.
The Suspense fallback and empty initial news read reuse a bounded skeleton;
cached content and explicit failures remain readable. Verify slow import/data,
success, failure, superseded requests, back/forward, reduced motion and phone
layout. Source/test review plus deployed Preview rehearsal are required.

Verified application revision: `ae01138f`.
Preview: https://eeb798c7-aurum-signal-room.yiyousiow1234.workers.dev/.
Production and Preview builds passed; 124 rendering, content and responsive-scroll
checks passed. Generated architecture input and document-link checks passed.
Behavior coverage exercises pending imports, superseded completion, import and
popstate failure, successful recovery, bounded skeleton markup and retained
cached news. Final separate inspection followed links through DashboardApp,
Suspense, the content boundary and AuditView. It found and repaired direct-child
CSS selectors affected by the retained-view wrapper, preserving overview,
audit, admin and retry layout rules. The unrelated Assistant waiting animation
keeps its own animation name. No server/data/release contract changed.

On the immutable Preview, delayed the actual AuditView script request and clicked
News and Events. The six-row skeleton appeared below the shared header on
1280x900, 390x844 and 360x800; no horizontal overflow. Inspected the full list's
outer edges and dividers, including its last row. Reduced-motion emulation
reported animation `none`. Failed the held request: the original overview
returned with a working retry action; retry opened the real news page. Phone
flow included menu open/close, navigation, scrolling, article expand/collapse,
news page 2, event selection, browser back and return to overview. Desktop
reinspection confirmed both overview images loaded and the original 1136px
content width. All request interception, cache and media overrides were cleared.

One React #418 hydration warning was recorded in the first fault-injection
session. It did not reproduce in a fresh direct news navigation, comparison with
the preceding Preview, normal overview navigation or normal reload. No cause is
claimed for this isolated observation; it remains recorded as a limitation rather
than describing all browser runs as console-clean. A summary selector initially
missed the actual disclosure and was corrected using its visible headline.
All task tabs were closed and viewport reset; remaining task sessions: 0.
Screenshots: `C:/Users/yiyou/AppData/Local/Temp/navigation-skeleton-desktop.png`
and `C:/Users/yiyou/AppData/Local/Temp/navigation-skeleton-phone.png`.
Production was not activated.

## Latest follow-up: overview image arrival

Verified application revision: `21f4eb5e`.
Preview: https://ebd010d6-aurum-signal-room.yiyousiow1234.workers.dev/.
On the preceding Preview, navigation from events started two separate image
requests: gold 9,826 bytes / 397ms and waves 32,684 bytes / 394ms in this browser
sample. Both small WebP files now use Vite inline asset imports, so artwork
arrives with the already loaded overview module. Image bytes and layout are
unchanged. This trades approximately 42.5 KB of raw artwork in the module for
removing the navigation-time image round trips; it is not a general policy for
inlining large images.

Production/Preview builds and 118 affected tests passed. Regression coverage
compares both rendered data URLs byte-for-byte with the source images, keeps a
50 KB combined artwork budget and checks the reserved gold dimensions. Generated
source evidence was refreshed and its input check passed. Final review checked
the existing eager LiveRoomView import in DashboardApp and unchanged CSS sizing.

On the deployed Preview, opened events then selected overview: both images were
complete with nonzero natural widths and no `/images/overview-*` resource
requests. Checked desktop 1280x900, phones 390x844 and 360x800, navigated from
overview to events and back and scrolled the phone page. No horizontal overflow
or browser warnings/errors. One navigation wait timed out, but the requested
page had loaded and its exact revision was verified before continuing. The task
tab was closed and viewport reset; remaining task-created browser sessions: 0.
Screenshot: `C:/Users/yiyou/AppData/Local/Temp/overview-inline-images.png`.
Production is unchanged; PR #562 remains a draft.

## Latest follow-up: curated event reading

Verified application revision: `6fcd4a28` (UI implementation `89b60f45`).
Preview: https://8773889f-aurum-signal-room.yiyousiow1234.workers.dev/audit?view=evidence.
The current-events destination is now a compact reading list. Headlines,
categories and publication times appear immediately; native source disclosures
show reporting domains and receipt times. Prediction-use tables, six statistics,
historical-use filters, model badges and their dedicated CSS are removed.
The existing eligible event selection, grouping and bounded cursor transport
are unchanged. No stored records, API contracts or processing owners changed.
The source payload has no summary/article URL; the UI does not fabricate either.

Final checks: full web suite 408 passed / 6 skipped / 0 failed; production and
Preview builds passed. The 117 affected rendering/content tests passed. Generated
architecture evidence was refreshed after the source change and its exact-input
check passed; architecture documentation and whitespace checks passed.
Final diff review traced current-event rendering to the existing paged reader,
confirmed publication ordering and event-key grouping, and checked legacy payload
fallback, absent timestamps/sources, loading/empty notices, retained retry/cursor
handling and shared story/news CSS selectors. No model-use field is rendered by
the current-event reader; retired view-only accounting and reason maps are gone.

Deployed Preview checked at desktop 1280x900, phones 390x844 and 360x800. Complete
outer edges and row dividers were inspected, including expanded first/last rows.
Full headlines wrap without clipping; document widths equal viewport widths.
Source disclosures are 44px high and pagination targets are 44x44. On phone,
expanded a source, closed it with Enter, navigated to news and back, scrolled to
the final row and opened its source. The bounded Preview has five detail rows
against 41 reported events and no next cursor: the displayed partial count and
disabled next button were verified. Another event page was unavailable in this
snapshot. No browser warnings/errors. Task tab closed and viewport reset;
remaining task-created browser sessions: **0**.

Screenshots: `C:/Users/yiyou/AppData/Local/Temp/current-events-desktop.png` and
`C:/Users/yiyou/AppData/Local/Temp/current-events-360.png`.
Production is unchanged. PR #562 remains a draft for visual review.

## Latest follow-up: observed macro data and story diagnostics

Verified application revision: `5a19d234`.
Preview: https://c60a1d1f-aurum-signal-room.yiyousiow1234.workers.dev/audit?view=coverage.
The coverage route is now named `宏观数据`, with six precise series, observed
values, units, source identifiers and individual observation dates. Collector
states, the 11/11 score and training copy are removed. Federal Reserve assets
are converted from USD millions to USD trillions; absent, non-finite and
incompatible-unit values remain unavailable. Collection and APIs are unchanged.

Story reading no longer shows deployment hashes or version-verdict banners.
Investigation of the preceding Preview found identical runtime/expected SHAs;
the unhandled `PREVIEW_SNAPSHOT` status had triggered the unknown-version UI
fallback. This was not evidence of actual revision drift. Backend provenance
and release checks remain intact.

Production/Preview builds and all 115 affected rendering/content tests passed.
Architecture documentation and whitespace checks passed. Final diff review
traced all six cards to `factor_coverage`, inspected loading/error states and
unit conversion, and checked shared CSS selectors and retained news navigation.

Desktop 1280x900 and phones 390x844/360x800 were checked on the deployed Preview.
All six card boundaries, values, units and dates remained visible without
horizontal overflow. Desktop uses three columns; phones use two. At 360x800,
opened stories, expanded the six-step chain, confirmed newest-first order,
collapsed it, returned to macro data and scrolled to the final row. No story
version banner remained. Browser console had no warnings/errors. The task tab
was closed and viewport reset; final task-created browser sessions: **0**.

Captures: `C:/Users/yiyou/AppData/Local/Temp/macro-data-desktop.png` and
`C:/Users/yiyou/AppData/Local/Temp/macro-data-360.png`.
Production is unchanged; PR #562 remains a draft for visual review.

## Latest follow-up: brief headings and pagination

Verified application revision: `0669b123`.
Preview: https://7c152c80-aurum-signal-room.yiyousiow1234.workers.dev/audit?view=briefs.
Both current and completed briefs now use `YYYY-MM-DD 黄金市场简报`.
The statistics-rules disclosure and orphan styles are removed. News, evidence
and search share accessible chevron buttons with the existing page handlers.

The production build, Preview build and all 112 affected rendering/content
tests passed. Obsolete assertions requiring the deleted disclosure were removed;
replacement behavioral coverage verifies its absence, fixed headings and named
disabled icon buttons. Architecture documentation and whitespace checks passed.

Desktop 1280x900 and phones 390x844/360x800: switched September 27/26 briefs,
verified uniform headings and no horizontal overflow, and navigated news page
1 -> 2 -> 1. Pagination targets measured 44x44; visible button text is empty,
accessible names remain. Evidence's bounded Preview has no next cursor, so both
page-one buttons correctly remain disabled; a second evidence page was not
available for browser verification. Search shares the tested button component.
No browser warnings/errors recorded. Final task browser sessions: 0.

Captures: `C:/Users/yiyou/AppData/Local/Temp/brief-unified-title.png` and
`C:/Users/yiyou/AppData/Local/Temp/pagination-icons-360.png`.
The application diff retains original data, cursor handling and brief content.
Production is unchanged and PR #562 remains a draft for visual review.

## Latest follow-up: remove the coverage snapshot banner

Verified application revision: `2c3c85d2`.
Preview: https://67d73004-aurum-signal-room.yiyousiow1234.workers.dev/audit?view=coverage.
The full-width branch snapshot notice is removed. Compact desktop-tab/phone-
selector provenance, resource time, global PR identity and failure notices remain.
The unused notice variant was removed from its shared component.

All 14 content-rendering tests, the Preview build and architecture documentation
check passed. Desktop 1280x900, phone 390x844 and 360x800 confirmed no long notice
or horizontal overflow; phone navigation to news and back worked. Screenshot:
`C:/Users/yiyou/AppData/Local/Temp/coverage-no-banner.png`. The task browser was
closed and viewport reset; remaining task sessions: 0. No production activation.

## Latest review: mobile reading and interaction repair

Verified application revision: `5a6adc12`.
Immutable Preview: https://70f359c6-aurum-signal-room.yiyousiow1234.workers.dev/.
This section supersedes the previous footer, density and Preview conclusions.

- Removed the redundant public footer and its CSS/component. Operational status
  remains in the desktop and phone header; the menu preserves every destination.
- Phone news now uses a divided list, 15px headlines and compact inline metadata.
  Source, receipt time and processing details reappear on expansion, together
  with the complete unclamped headline. The category label stays accessible.
  Brief, story, overview and event typography/spacing were also reduced.
- At 390x844, the first news row moved from y=676 to y=378 CSS pixels; the same
  two-line row shrank from 259px to 95px. Measurements exclude the optional
  runtime incident banner. Real operational warnings remain visible.
- Desktop 1280x900 and phones 390x844/360x800 were inspected on deployed Preview.
  Category filtering, next/previous page, article expand/collapse, menu/Escape,
  overview return, brief date selection, six-node newest-first story expansion,
  evidence metric expansion and 11-card coverage boundaries were exercised.
  No unintended horizontal overflow was found. Menu targets remain 46px high;
  selectors, paging and other primary controls retain at least 44px targets.
- Actual desktop hover is rounded, without an outline, and uses 160ms color
  transitions. Keyboard focus retains a solid 2px indicator. Reduced-motion
  emulation yielded a 0s transition. Hidden scrollbar chrome does not prevent
  scrolling: keyboard End reached scrollY=1686 on a 2586px document at 900px.
- Final screenshots: `C:/Users/yiyou/AppData/Local/Temp/mobile-reading-390.png`
  and `C:/Users/yiyou/AppData/Local/Temp/mobile-reading-desktop.png`.
- Full web suite after the initial implementation: 400 passed, 6 skipped.
  After the final CSS refinement, production build and all 111 affected
  rendering/content/responsive tests passed. Final branch Preview build passed.
  Architecture compile/check, architecture docs and whitespace checks passed.
- An intermediate Preview logged one React hydration error. It did not recur
  on the final Preview's initial navigation, route interactions or cold reload.
  Its cause is not established; this report does not claim it was repaired.
  No final-Preview console warning/error was recorded during these checks.
- Final source review checked footer removal, responsive selector precedence,
  menu/status ownership, restored expanded metadata, focus/reduced motion and
  list/grid boundaries. API and data ownership remain unchanged. Browser tab
  closed, viewport reset; remaining task-created browser sessions: **0**.

Implementation and Preview checks passed within this UI scope. PR #562 remains
a draft; neither production activation nor user visual acceptance is implied.

## Current review: continuity across public pages

Final result: passed for implementation and branch Preview verification.
User visual acceptance and production activation are not implied.

Verified application revision: `2f6b8ec7b204646fe01239d7b986be6dd8cbf4f1`.
Immutable Preview: https://f3ca2187-aurum-signal-room.yiyousiow1234.workers.dev/.
The following overview-only review is retained as historical context; its
Preview and test totals are superseded by this section.

The overview, all six news/event subpages and health now share the selected C
brand, sans typography, white header, cool background, teal navigation and
rounded panels. Public phone navigation and footer reuse the existing route
authority. Private administrator controls retain their existing behavior.

### Responsive and interaction evidence

- Desktop 1280x900, phones 390x844 and 360x800 were inspected in deployed
  branch Previews. The final application revision was rechecked after the
  coverage-grid edge repair. No unintended horizontal overflow was found.
- Overview/news headers have matching desktop geometry: x=92.5, y=36.5,
  width=1080, height=88 CSS pixels. Phone menus retain 44px minimum targets;
  opening, navigation dismissal, Escape and focus return were exercised.
- News: category selection, next/previous pagination and expanded article
  detail were exercised on phones. Daily brief date selection changed the
  displayed brief. Search controls and the explicit empty-result state were
  checked; this does not claim a matching result for the test query.
- Stories remain collapsed initially. Expanded story nodes were confirmed
  newest first, with the complete six-node chain reachable before collapsing.
- Evidence metric boundaries were checked at desktop and phone widths.
  Coverage has 11 cards: the final desktop card spans the remaining columns;
  all phone cards reset to one column with complete internal/outer edges.
  News, brief, story, search, evidence and coverage surfaces were inspected.
- Actual screenshots: `C:/Users/yiyou/AppData/Local/Temp/unified-news-desktop.png`
  and `C:/Users/yiyou/AppData/Local/Temp/unified-news-390.png`, captured from the
  final immutable Preview. Capture scaling excludes the scrollbar; CSS
  viewport dimensions were verified independently using DOM geometry.
- No browser console warnings or errors were recorded. The task tab was
  closed and the viewport override reset. Final task browser session count: **0**.

### Final review and checks

- `npm test`: 399 passed, 6 skipped, 0 failed. Production and Preview builds
  passed. Changed shell lint: 0 errors, 2 existing navigation warnings.
- Architecture compilation/check, architecture documentation check and
  `git diff --check` passed. Architecture digest remained unchanged.
- Final diff inspection covered public/private shell callers, navigation
  active state, native menu event cleanup, shared status subscriptions,
  responsive sibling grid edges and rendered-shell regression coverage.
  No API, data authority, refresh ownership, model or collection behavior changed.
- No unresolved blocking finding remains within this visual-continuity scope.
  Operational warnings, snapshot provenance and existing content remain real.
  Production was not replaced; PR #562 remains a draft for visual review.

## Historical selected C overview fidelity review

final result: passed

## Scope and source

This replaces the earlier report, which incorrectly accepted the omission of
major elements of the selected C design. That interpretation was rejected by
the user. This review evaluates the implemented page against the actual image.

Source: `C:/Users/yiyou/.codex/generated_images/01a0dc7e-55de-7cc3-a2a3-cfeba8d25a8f/exec-9cb92b84-63de-4d1a-81c5-9a26f50c6944.png`.
The 1536x1024 board contains desktop and phone frames. Board labels and device
frames are excluded; application content is compared at corresponding widths.

Verified application revision: `84d2d61bdd3b061fe8afd1068a6814c9b8dab488`.
Immutable Preview: https://13da393b-aurum-signal-room.yiyousiow1234.workers.dev/.
Production was not changed. The PR remains a draft for visual review.

## Captures and comparison

Captures are saved under `C:/Users/yiyou/AppData/Local/Temp/`.

| Capture | CSS viewport | PNG pixels |
| --- | --- | --- |
| c-match-desktop.png | 1120x840 | 1105x829 |
| c-match-390.png | 390x844 | 375x812 |
| c-match-360.png | 360x800 | 345x767 |
| c-match-360-bottom.png | 360x800, scrolled | 345x767 |

The in-app capture excludes the scrollbar and scales the image. Layout metrics
were read separately from the DOM. Reference and final rendered screenshots
were opened in the same comparison input, including desktop and phone content.
The Preview banner and provenance labels are retained and excluded from visual
alignment with the production-oriented concept. Browser density is not an
exposed setting, so this is a composition review, not a pixel-difference score.

## Findings resolved

- P1: missing gold bullion and pale gold landscape. Both now use transparent
  generated raster assets, optimized to 9.8 KB and 32.7 KB WebP files.
- P1: undersized branding and typography. The gold/navy brand, bold sans price,
  navy headings, teal icons and links now follow the reference hierarchy.
- P1: incorrect phone header. The overview uses a single brand/menu row while
  reusing the existing navigation destinations and administrator login.
- P2: phone header and footer inherited shared column direction. Explicit row
  layout fixes both; the footer brand and status share a row. Regression
  assertions extend the existing responsive presentation contract.
- P2: desktop and phone card rhythm. Desktop has one white rounded panel with
  an internal divider; phones have separate white cards. Outer edges, header
  dividers, last rows, links and footer were inspected, including by scrolling.
- P2: decorative arrows without working targets were avoided. Headline rows,
  section links and footer links open existing briefs, events or health pages.
  The complete original headline remains in accessible text and the title;
  overview display is one line with ellipsis.

Remaining P3 differences: generated bullion facets and wave contours are not
pixel-identical to the concept; library icon strokes and system font metrics
also differ slightly. Real headlines are longer than the concept examples.
The application retains actual operational status, administrator access and
Preview labels, and uses real footer destinations instead of invented pages.
No claim of exact pixel reproduction is made. No unresolved P0/P1/P2 finding
remains for this overview correction.

## Interaction and final review

- Desktop, 390x844 and 360x800: document scroll width equals client width
  (1105, 375 and 345 CSS pixels respectively).
- Menu toggle is 44x44; menu links are at least 46px high; headline and section
  links are 48px high; footer links are at least 44px high.
- Open/close, Escape and focus return, administrator dialog open/cancel and
  navigation dismissal were exercised. Event listeners have unmount cleanup.
- Opened full brief and current events, returned to overview, scrolled to the
  footer and opened health. Retained pages use their existing shell and show
  no horizontal overflow at phone widths. Final footer-only repair was then
  checked on the final immutable Preview.
- No browser console errors or warnings were recorded. Browser tab was closed
  and viewport reset. Final task-created browser session count: **0**.
- Final diff review checked DashboardShell callers, DashboardLink navigation,
  OverviewNews resource subscriptions and cleanup, LiveRoomView quote data,
  retained-route CSS scoping, generated assets and existing rendering tests.
  No resource URL, refresh policy, data authority or backend contract changed.

## Checks

- Final revision: `npm test` passed, 398 passed / 6 skipped / 0 failed.
- Production and branch Preview builds passed.
- Changed TSX files: ESLint 0 errors, 4 warnings (two existing navigation
  warnings and two image-component recommendations; images are already sized
  and optimized WebP assets).
- Architecture evidence check and `git diff --check` passed.
- A supplementary test invocation against a branch-Preview build hit the
  existing health fixture/banner assumption. The canonical clean-environment
  suite passed; deployed health navigation and Preview identity were checked
  in the browser. Tests were not weakened to suppress that discrepancy.

This report establishes implementation and Preview evidence. It does not claim
production activation or user visual acceptance.
