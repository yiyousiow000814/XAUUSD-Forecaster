# PR 35: News Metrics Source Of Truth

Status: placeholder; implementation has not started.

## Scope

- Replace ambiguous labels such as "models available".
- Separate article, independent-event, prediction-use, current-validity, and
  training-record counts.
- Make every page consume one shared metric definition and payload.
- Verify the complete affected flow on desktop and phone Preview surfaces.

## Acceptance Boundary

This PR changes reporting semantics and presentation. It must not change news
admission, model training, or historical evidence.
