# AGENTS.md

## Project overview

Python 3.13 CLI `skive` (rich/Textual console) plus operator scripts: a
hand-driven two-box pipeline that rips CDs/DVDs/Blu-rays to NFS staging and
transcodes into Jellyfin/beets libraries. uv + mise tooling, editable install.

## Commands

- Test: `.venv/bin/python -m pytest -q` — full suite, ~65 s, green before done
- Lint: `ruff check . && ruff format --check .` — both must pass clean
- Install (dev machine): `mise run install`
- User update: `mise run update` — the skive user rides `main`; never break it

## Artifacts

- Specs, tickets, planning maps: `.scratch/` (local, untracked)
- `.scratch/INDEX.md` lists every effort with a status (open, done, dropped). Add a
  line when you start one; when its PR merges, mark it done, move the folder to
  `.scratch/done/`, and delete its `orch/` working files
- PR texts: `.scratch/prs/<branch-with-dashes>.md`, title on the first line

## Boundaries

`main` takes no direct pushes: changes reach it only through a merged PR.

### ✅ Always

- Land every change through a PR: branch, verify (tests + lint), write the PR
  text to `.scratch/prs/`, then ask the operator to run `scripts/open-pr.sh`
- `feat`/`fix` PRs add a line under `## Unreleased` in `CHANGELOG.md` — the
  approver checks the entry exists before merging
- Conventional-commit subjects, scopes matching the history

### ⚠️ Ask first

- New runtime dependencies; toolchain changes (mise.toml, pyproject, uv.lock)

### 🚫 Never

- Touch the remote — no push, no PR, no branch delete. The operator does all
  GitHub actions; agents never hold that access
- Commit `.env` or secrets
- Commit to `main` or force-push any branch
- Develop in `windows/` — legacy copies kept for reference; `skive/` is the
  source of truth

## Docs discipline

- New domain terms crystallise into `CONTEXT.md` (glossary only, no process);
  consequential trade-offs become ADRs in `docs/adr/`, following the existing
  numbering and style

## Code style

Self-documenting code, KISS above all. Ruff is the source of truth. Comments
only explain a non-obvious why, a workaround, or a hidden risk — never restate
what the code does.

Workflow: concurrent · tracker: github · main protected · details: .scratch/projectinfo.md
