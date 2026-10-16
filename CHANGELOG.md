# Changelog

This changelog records user-facing changes; it does not imply a tagged or published release.

## Unreleased

- Organize the static UI source and build entrypoint under `apps/showcase/` while preserving the frozen eight-file output location and hashes.
- Group tests by purpose and add offline local-Markdown-link verification to CI.
- Clarify historical replay versus deterministic offline and scripted local sandbox modes.
- Retain the coherent-rollback expected failure as a protected-effect release blocker.
