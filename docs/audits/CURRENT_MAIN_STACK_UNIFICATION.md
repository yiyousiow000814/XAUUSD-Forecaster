# Current-main stack unification audit

This audit records replacement scope, not production acceptance. The replacement
starts from main `01c51df05ef15db4edc9e73dd97501e0e1cb8616`; historical heads are
intent evidence and are not merged into the new branch. See the
[execution plan](../plans/CURRENT_MAIN_STACK_UNIFICATION.md) for all dispositions.

## Historical input identities

| PR | Original title | Retained head |
| --- | --- | --- |
| [#282](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/282) | docs: establish repository architecture maps and rules | `b629461bc05e4ece845fc87bd51b412e519c738c` |
| [#283](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/283) | refactor: extract dashboard status snapshot cache | `1e75804ef15efbb27b63db0be7f7683a1e2b69e9` |
| [#285](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/285) | refactor: extract dashboard runtime health projection | `4deeac467f9211f64bbb0a071b53e30b84af4106` |
| [#287](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/287) | chore: extract dashboard resource contracts | `98e680adf6193ee9316dacd268c633e6ff07c4f4` |
| [#288](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/288) | chore: extract dashboard news resources | `81bbd98a3d1803018b90f931ec5326d61b912584` |
| [#289](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/289) | chore: extract dashboard market resources | `0921c04f0072ecbc5181817179b9c9bffa252847` |
| [#290](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/290) | chore: extract dashboard status resources | `632a53313365d7dff6ae1c102f43f9fba23c3f16` |
| [#291](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/291) | chore: extract dashboard operator bridge | `d4cfd59647218b19eb3cd423c0b23979c3474466` |
| [#292](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/292) | chore: extract dashboard sync owners | `c12e6e82e87757bcc78368b021e947ffd446b378` |
| [#294](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/294) | chore: extract annotator runtime owners | `5380f23bc6bd76e4c2fff5a58b4d876e9d9a4cb4` |
| [#295](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/295) | refactor(runtime): separate collector and control owners | `9a0bfa4a3419aa3d1e15c6125d802f0e6dd28824` |
| [#296](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/296) | refactor(packages): canonicalize decision and evidence | `3df90c1a412fd1b646e04b45f4818ca28067e322` |
| [#297](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/297) | refactor(training): establish canonical package | `829acea097c57128268aae939c5f05ef37a4487c` |
| [#298](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/298) | refactor(news): establish canonical owner packages | `04757173379dbfcbacb2e9087186d8729a968d83` |
| [#299](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/299) | refactor(packages): close canonical owner layout | `fbc795cc53f9e9642767a5a6a5e3f72d8891e99b` |
| [#301](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/301) | test: organize suites by owner contracts | `11912056bbea24caa56214a644d2971bfe1bfd68` |
| [#304](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/304) | feat: add private architecture explorer | `48cb32a60c803e7f7b97290209933b9041d5750a` |
| [#321](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/321) | feat: compile architecture structure from repository source | `fd57fe7468bb74a8fc35386ac697d0deac69fba2` |
| [#324](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/324) | test: bind critical architecture contracts to executable evidence | `1c8f1fa2d675f0aac14a71ad09d567f15680c889` |
| [#325](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/325) | test: measure critical contract effectiveness with targeted mutations | `056a2bf4421af9a6a9ff46107c3c4ecd0b50dd7b` |
| [#328](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/328) | feat: expose generated architecture evidence and code drill-down | `64ac434d97d557ec9857bb4a465a4de9c5c3554b` |
| [#302](https://github.com/yiyousiow000814/XAUUSD-Forecaster/pull/302) | chore: close repository modularization campaign | `35b906ac3845931a52183601912e3d141066aaa5` |

## Implemented replacement

- Relocate 68 current-main modules into eight canonical ownership packages, without
  old-path shim implementations. Extract five current domain owners from process
  scripts; preserve CLI, thread, retry and shutdown responsibility.
- Move shared projection schema below Dashboard, preserving SQL and startup order.
- Update producer/consumer imports, source identities, actual mutation targets,
  current architecture declarations and package-resource resolution.
- Remove four production script-library import exceptions. Source-selected parity
  now checks the real loaded owner and rejects an unavailable producer root.
- Preserve main-only publication and Assistant PAUSED. Control Center split from
  #295 is retired; scheduler operator authorization from #291 remains in force.
- Keep #301 complete-collection intent through the current test inventory and
  contract families; do not restore superseded release-control test directories.

## Verification evidence

The original base and `7d22dc76b168cb63023294c73c6b00d5d3622f9f` have identical
Git trees. Their real release-fixture builder and the relocated builder produce
19 byte-identical JSON artifacts. A comparison of 791 relocated function/class
ASTs found no body changes after ignoring import nodes. These checks do not prove
all import, source-identity or runtime behavior; real boundary tests are required.

Collection retains all 2,098 original Python cases and adds five package execution
cases. The source-selected parity family now executes a distinct producer root,
unrelated cwd, separate runtime root and missing-producer failure in real Python.
Web validation passed 387 cases with six existing skips. Intermediate path and
fixture failures were retained externally and corrected without removing assertions.

## Honest remaining scope

#324 originally proposed normalized runtime traces for sixteen architecture
contracts. Current main only projects retained, source-bound test/mutation files;
runtime traces remain UNKNOWN. This consolidation retains that requirement in one
place; it does not manufacture trace PASS from a green test or a declared event
sequence. #328 therefore retains the corresponding live evidence-display gap.

The current bounded mutation runner rechecks surviving applicable historical
families; obsolete Preview promotion belongs to retired release coordination.
Historical twelve-mutation results and their three survivors remain historical,
not current exact-source acceptance. No retained evidence is rewritten.

This Draft is the single replacement review surface for the original chain.
Creating or closing PRs does not complete production recovery, Cloudflare capacity,
news backlog, research, or the unresolved runtime evidence requirement. Those
results must keep their own actual acceptance status.
