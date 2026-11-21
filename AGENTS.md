# AGENTS.md

Notes for an AI coding agent (or a person) working in this repo. `CLAUDE.md` pulls this file in.

## Contents

- [What this is](#what-this-is)
- [Layout](#layout)
- [Rules](#rules)
- [Test](#test)
- [Working efficiently](#working-efficiently)
- [Skills](#skills-claudeskills)

## What this is

LomboVault is a small Python 3.12 program that backs up the **config and databases** of a media-server stack (not the media), encrypted with the `age`
program, keeps N generations, and runs a **restore drill** that proves the newest backup decrypts and unpacks to the files recorded at backup time.
It is deliberately small; restic and borg are better backup tools (the README says when to use them). Standard library only (add a dependency only when it
clearly earns its place). It has not been run against a real stack and has not had an independent review; `docs/security.md` is the threat model.

## Layout

| Path | What |
|---|---|
| `src/lombo_vault/core.py` | The decisions. Pure: no disk, network or clock (time is passed in). Naming, `plan_prune`, folder clash rules, exclusion, manifest build/parse/compare. |
| `src/lombo_vault/agebin.py` | The only place that starts `age`. No crypto here. |
| `src/lombo_vault/store.py` | Walk sources, write archive + manifest atomically, list and delete files. |
| `src/lombo_vault/drill.py` | The restore drill; returns a `core.Verdict`. |
| `src/lombo_vault/cli.py` | `doctor`, `plan`, `backup`, `drill`, `list`, `prune`. The only place that prints, reads the clock or sends alerts. |
| `src/lombo_vault/config.py` | Settings from `LOMBO_VAULT_*` variables (`SETTINGS` lists them). |
| `src/lombo_vault/notify.py` | Webhook alerts and healthchecks-style pings. |
| `src/lombo_vault/jellyfin.py` | Template leftover, unused; delete it. |
| `tests/` | pytest. `conftest.py` writes a fake `age` (a reversible XOR) onto `PATH`; `test_real_age.py` runs only when the real `age` and `age-keygen` exist. |
| `scripts/leak-check.sh` | Fails when private or personal strings appear. CI runs it. |
| `docs/` | One topic per file, each with a `## Contents` list. |

## Rules

- **Never write your own crypto.** Encryption and decryption go through `agebin.py` and the `age` program.
- **Keep `core.py` pure.** If a rule needs data, pass it in (the clock is an argument).
- **The private key is never stored, and `backup` never reads it.** Settings hold only the key file's path, and only `drill` and `doctor` open it. A test checks that `backup` never calls `age -d` or `-i`.
- **Retention only deletes the tool's own names** (`core.is_own_file`), only after a successful backup, only when `DRY_RUN=false`. `store.delete_files` re-checks every name. A failed backup never deletes anything.
- **Never count a bad backup as a good one:** a backup of zero files is refused; stamps must be real dates and not more than a day ahead of the clock (`core.parse_name`, `plan_prune`); a source that is a symlink is followed; `EXCLUDE` is matched only below each source. Alert and ping errors never print the URL (`notify.py`).
- **Write atomically:** temp name, fsync, rename; archive first, manifest last. A backup with no manifest is incomplete and ignored.
- **Refuse overlapping folders:** the backup folder must not be inside a source or the other way round (paths resolved first).
- **The drill must be honest:** no key file means `PARTIAL`, never `OK`; any crash is a `FAIL`. A change to what a result means also changes `docs/how-it-works.md` and `docs/security.md`.
- **A new setting** goes in `config.py` (validated, message names the variable), `SETTINGS`, `.env.example` (a test keeps them in sync).
- **Never put private details in the repo:** real hostnames, IPs, webhook URLs, keys, local paths. Use `/mnt/ssd`, `changeme`, `*.example.com`. `scripts/leak-check.sh` enforces it; patterns live in `scripts/leak-patterns.txt`. Never commit a real age key, not even a test one: tests make theirs.
- Plain language in docs and messages. Never imply the age, restic, borg or Jellyfin projects made this.
- The Lombo look (every icon has the Lombo L) is in `docs/branding.md`.

## Test

```sh
pip install -e '.[dev]'
pre-commit install            # once: runs the checks below before every commit
pre-commit run --all-files    # ruff lint + format, strict mypy, file hygiene, leak check
pytest -q --cov               # tests with coverage (the gate is `fail_under` in pyproject.toml)
```

These are the same checks CI runs (one config: `.pre-commit-config.yaml` and `pyproject.toml`), so what passes
locally passes there. The tooling is the same in every Lombo-Forge repo: ruff for lint and format, strict mypy,
pytest. Do not loosen a rule to make a change pass; fix the code, or say why in a comment next to a narrow `noqa`.

Install `age` (`apt install age`) to run the real-age tests too; without it they are skipped, and the fake-age tests still cover the logic.
Without Python installed: `docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c "pip install -q -e '.[dev]' && pytest -q"`.

## Working efficiently

- Optional tools that live in your own profile, not in this repo. Skip any you do not have.
- Graphify: build the code-only graph with `graphify update .` (never `/graphify .`, which costs tokens) and run
  `graphify query "<question>"` before grepping. `graphify-out/` is generated and gitignored; INFERRED edges are guesses.
- Ponytail: the smallest change that fully solves the task. Keep diffs small and focused.

## Skills (`.claude/skills/`)

- `install-into-my-stack`: help a user set up keys, settings and schedules, and run `doctor`.
- `troubleshoot`: read `doctor`, `drill` and `list` output.
- `adapt`: change what is backed up, retention or alerts safely.
- `ship-a-change`, `release`: get a change into `main`; cut a release.
