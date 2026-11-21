# How it is built

## Contents

- [The shape](#the-shape)
- [Why it is split this way](#why-it-is-split-this-way)
- [Safe by default](#safe-by-default)
- [Files in the backup folder](#files-in-the-backup-folder)
- [Decisions](#decisions)
- [Settings](#settings)
- [Alerts](#alerts)

## The shape

```
sources --> store.py (walk, tar, hash) --> agebin.py --> the age program --> backup folder
                  ^                                                              |
            cli.py (backup, plan, list, prune, doctor)                           v
                  |                                       drill.py <-- agebin.py (decrypt with the key file)
                  +--> core.py (names, retention, manifest, verdicts)
                  +--> notify.py (webhook, ping)
```

| Module | What |
|---|---|
| `core.py` | Pure: no disk, no network, no clock (time is an argument). File naming, which names retention may delete, folder clash rules, exclusion globs, building and checking the manifest, comparing checksums. |
| `agebin.py` | The only place that starts `age`. Feeds bytes in or out and checks the exit status. No crypto. |
| `store.py` | Walks the sources, writes the archive and manifest atomically, lists the folder, deletes the files core allows. |
| `drill.py` | The restore drill, ending in a verdict. |
| `cli.py` | The commands. The only place that prints, reads the clock or sends alerts. |
| `config.py` | Settings from `LOMBO_VAULT_*` variables. |
| `notify.py` | Webhook alerts and healthchecks-style pings. |

## Why it is split this way

Retention is the one place a bug deletes someone's backups, so it is a pure function (`plan_prune`) with the clock passed in, and tests feed it hostile folder listings. The disk and process layers are thin and are tested with a small fake `age` program on `PATH` (a reversible byte transform) plus, when a real `age` is installed, the real thing.

## Safe by default

- `DRY_RUN` is true until set to false; it governs **only the deletion of old backups**. `plan` never writes.
- A backup is written under a temporary name and renamed when complete, archive first and manifest last. A backup without a manifest is "incomplete" and is ignored by the drill and never counts as a kept generation.
- Retention runs only after a backup succeeded. A failed backup removes its own temporary files and touches nothing else.
- Retention can only delete names that match `lombovault_<UTC time>.tar.gz.age` / `.manifest.json` (or the same as a `.tmp` file over a day old). `store.delete_files` re-checks every name and refuses anything else, so even a bug in the planner cannot remove another file.
- The backup folder must not be inside a source, nor a source inside it; paths are resolved first, so a symlink cannot hide it.
- `backup` never opens the private key; the key path setting is read only by `drill` and `doctor`.
- A drill that crashes is reported as a failed drill, never as silence.

## Files in the backup folder

For a run at 04:30:00 UTC on 2026-11-21:

| File | What |
|---|---|
| `lombovault_20261121T043000Z.tar.gz.age` | The compressed tar, encrypted. Mode 0600. |
| `lombovault_20261121T043000Z.manifest.json` | Plain JSON: the archive's SHA-256 and size, and the SHA-256 and size of every file inside, plus the source list. Mode 0600. |

The manifest is **not secret-free**: it lists file paths. Treat the folder as private (it is created with mode 0700). It is not signed; see [security.md](security.md).

## Decisions

1. **One archive per run, no deduplication.** Deleting any generation cannot damage another; each file opens with one `age` command.
2. **tar + gzip inside age**, written by Python's `tarfile` in streaming mode into the age process, so nothing unencrypted ever touches the disk.
3. **Checksums while reading**, from the same bytes that go into the tar, so a file that changes during the backup cannot disagree with its own manifest line (but see the SQLite note in [how-it-works.md](how-it-works.md)).
4. **Drill defaults to honest.** Without a key file the result is `PARTIAL`, never `OK`.
5. **Recipients are age public keys only** (`age1...`), validated; a private key pasted by mistake is refused with a loud message. SSH recipients are left out to keep validation simple.
6. **No scheduler inside.** Commands run once; cron or a timer schedules them. Fewer moving parts, and the ping tells you when the schedule stopped.
7. **No restore command.** Restoring for real is rare and high-stakes; it is one documented `age -d | tar -x`, run by a person who has read the steps.

## Settings

Every setting is an environment variable with the `LOMBO_VAULT_` prefix, validated in `config.py`. To add one: read it in `config.py` with a clear error, add it to `SETTINGS` and `.env.example` (a test keeps them in sync), and add a test.

## Alerts

`notify.send(url, message)` posts JSON with the text under `content` and `text` (Discord, Slack and many bridges accept it). `notify.ping(url, failed)` does a GET on the URL, or `URL/fail` on a failure (healthchecks.io style). Backups alert only on failure; every drill result is sent, so silence means the schedule stopped. A dead webhook or ping prints a warning and never changes the result of a backup.
