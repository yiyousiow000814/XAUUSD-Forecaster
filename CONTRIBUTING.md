# Contributing

Contributions are welcome through pull requests.

1. Create a branch from `main`; do not push directly to `main`.
2. Keep Alpha and model evidence point-in-time, causal, and Shadow-only.
3. Never commit credentials, runtime databases, market/news data, logs, or
   trained model artifacts.
4. Add durable tests for changed contracts and equivalent sibling paths.
5. Run `python -m pytest -q tests`, `npm test` in `web/`, and the relevant
   platform-specific checks before requesting review.

Repository documentation is written in English. Product UI text and immutable
audit evidence may remain in their source language.

Use private vulnerability reporting for security concerns rather than a public
issue.
