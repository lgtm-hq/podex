# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog][kac], and this project adheres to
[Semantic Versioning][semver].

[kac]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/spec/v2.0.0.html

## [Unreleased]

### Added

### Changed

### Deprecated

### Removed

### Fixed

### Security

## [0.8.0] - 2026-07-11

### Added

- **ci**: add merge_group trigger to PR title validation (#148) (ec64c41)

### Changed

- **ci**: adopt canonical emoji check names (#157) (e792512)
- **ci**: adopt lgtm-ci v0.52.3 (#152) (4c2cfd7)
- add frontend CI (astro check + vitest) (#145) (b64beab)
- **deps**: upgrade to Astro 7, React integration 6, Node adapter 11 (#143) (95574fd)
- **deps**: update digest (#141) (582039c)

### Fixed

- **ci**: pass complete egress allowlists to main-only workflows (#161) (b1a6bac)

## [0.7.0] - 2026-07-05

### Features

- **frontend**: scaffold Astro app with a podcasts home page (#138) (88f2d5e)

### Other Changes

- **ci**: add CodeQL code scanning (#137) (60b7b8b)

## [0.6.0] - 2026-07-05

### Features

- **db**: add Alembic migrations and a schema-drift guard (#135) (5a5a679)

## [0.5.0] - 2026-07-05

### Features

- **api**: add mentions linking media to episodes (#133) (8a7c4f9)

## [0.4.0] - 2026-07-05

### Features

- **api**: add media catalog model and read endpoints (#131) (8e02f82)

## [0.3.0] - 2026-07-05

### Features

- **api**: add episodes catalog model and read endpoints (#129) (6a94676)

## [0.2.0] - 2026-07-05

### Features

- **api**: add podcasts catalog model and read endpoints (#127) (21a5c94)

### Other Changes

- **ci**: add action-pinning validation and dependency review (#126) (936a283)
- **deps-dev**: update lintro to 0.64.5 (minor) (#120) (5b0ed52)

## [0.1.0] - 2026-07-05

### Features

- **backend**: add FastAPI application scaffold with health endpoint (#119) (9b81bbe)

### Bug Fixes

- **backend**: use assertpy in tests instead of a blanket bandit ignore (#121) (43117db)

### Other Changes

- **ci**: add release automation (version PR + auto-tag) (#122) (4adcfc3)
- **license**: relicense to AGPL-3.0 and adopt open-core NOTICE (#118) (771f624)
- **ci**: add scorecards and PR labeler (#117) (3ff3eb2)
- **ci**: add PR auto-assign, title validation, and contributor docs (#115) (db1e52f)
- add lintro quality workflow for required checks (#94) (ce3f5a4)
- add org-standard files (#92) (76f0acd)
- add CODEOWNERS for consistent review routing (#93) (abcaf36)
- standardize Renovate config with org-wide shared preset (#22) (d1cad43)
- Initial commit (caf12b7)

### Previously Unreleased

- CI: PR auto-assign and Conventional Commits PR-title validation workflows.

- `CONTRIBUTING.md` documenting the development, lint, and PR workflow.
