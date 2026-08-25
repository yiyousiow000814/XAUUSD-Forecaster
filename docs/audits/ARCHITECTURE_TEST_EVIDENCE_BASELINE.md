# Architecture Test Evidence Baseline

## Scope

This initial audit binds 16 durable architecture contracts to 15 unique tests
across Python, Web, and Windows. Ten production-shaped fixture families emit
normalized asserted event sequences. No provider, production endpoint, or
production store was used.

At source digest `cae83c04e11c07cda2f8114cfedbfc2fa158f389c54e1f0f122edb6d0ba7f793`,
the deterministic collector found 1,646 test functions: 15 explicitly bound
contract tests, 982 owner-touching tests, and 1,631 currently unclassified by
contract class. These sets overlap by design: `TOUCHES` is a relationship,
while classification records audit state. Unclassified does not mean useless.

All 16 pilot contracts currently have their required declaration, binding,
execution, and fixture-runtime categories. This does not yet prove mutation
protection; `MUTATION_KILLED` remains absent until PR C runs valid targeted
breaks. Cloudflare local isolation and Preview isolation are test-executed but
do not claim browser or production observation.
