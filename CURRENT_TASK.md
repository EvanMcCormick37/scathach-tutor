# All planned phases complete (including review system refactor).

## Recent changes (per user request)
- Split `review` into `review` (levels 1–2) and `super-review` (levels 3–6)
- `super-review` surfaces worst performers first (difficulty ASC, score ASC)
- `super-review` has optional Hydra Protocol (`--hydra` flag or `SCATHACH_HYDRA_IN_SUPER_REVIEW=true`)
- Added `--open-doc` flag + `SCATHACH_OPEN_DOC_ON_SESSION` to open source documents at session start
- FSRS scheduling applies to both review modes via the same queue tables

Next: Phase 6 — Future Extensions (post-MVP). See DEVELOPMENT_ROADMAP.md.
