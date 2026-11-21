---
name: adapt
description: Change what LomboVault backs up, how many it keeps, how it is scheduled, or how it alerts, or add a setting, without breaking its safety rules. Use when the user wants different behaviour, a new setting, a different alert target or a change to the drill.
---

# Adapt LomboVault

Read `AGENTS.md` (the rules) and `docs/architecture.md` first.

- **Different sources, retention, schedule, alert URLs:** these are settings only (`.env`); no code change. Explain `DRY_RUN` (it only controls deleting old backups) before touching retention.
- **A new setting:** `config.py` (validated, the message names the variable), add it to `SETTINGS` and `.env.example` (a test keeps them in sync), use it in `cli.py`/`store.py`, add a test.
- **A change to retention:** it lives in `core.plan_prune`, which is pure. Write the test with hostile folder listings first (foreign files, unfinished runs, fewer runs than `KEEP`). It must still return only names matching the tool's own
  pattern, never delete anything when fewer than `KEEP` complete runs exist, and run only after a successful backup. Do not add a flag that skips the dry run.
- **A change to what the drill accepts:** keep it honest: no key file is `PARTIAL`, never `OK`; any exception is `FAIL`. Update `docs/how-it-works.md` and `docs/security.md` in the same change.
- **A new alert target:** keep `notify.py` generic (JSON webhook). Bridges (Apprise and the like) belong outside the tool; do not hard-code a service or a URL.
- **SSH-key recipients or passphrases:** not supported on purpose (validation and key handling would grow). If the user insists, change the recipient check in `config.py`, test it with the real `age`, and update `docs/security.md`.
- **Never** add code that stores or transmits a private key, reads the key during `backup`, writes crypto of its own, deletes files outside the tool's names, or stops/starts the user's containers.
- Run `pre-commit run --all-files` and `pytest -q --cov` (with `age` installed, so the real round trip runs) before saying it is done.
