# Troubleshooting

## Contents

- [Start here](#start-here)
- [Settings errors (exit code 2)](#settings-errors-exit-code-2)
- [doctor failures](#doctor-failures)
- [Backup failures](#backup-failures)
- [Drill failures](#drill-failures)
- [Retention](#retention)
- [Alerts and pings](#alerts-and-pings)

## Start here

Run `lombo-vault doctor`, then `lombo-vault list`. Messages say which setting or file is the problem. Exit codes: 0 fine, 1 a check or job failed, 2 a setting is wrong.

## Settings errors (exit code 2)

| Message | Fix |
|---|---|
| `LOMBO_VAULT_SOURCES is not set` | List absolute folders or files, comma separated. |
| `must be an absolute path` / `must not contain '..'` | Use full paths. |
| `RECIPIENTS holds a PRIVATE key` | Remove it from the file and from any shell history or chat where it was pasted; treat that key as exposed and make a new pair. |
| `is not an age public key` | It must be `age1` plus 58 characters. SSH keys are not accepted. |
| `DRY_RUN must be true or false` | A typo is an error, not "off". |

## doctor failures

| Line | Meaning and fix |
|---|---|
| `age program: cannot start 'age'` | Install age (`apt install age`) or set `LOMBO_VAULT_AGE_BIN` to its full path. |
| `recipients: age refused...` | age did not accept a public key. Copy it again from `age-keygen` output. |
| `folders: ... inside the source` | Move `BACKUP_DIR` outside every source. |
| `sources: ... not readable` | Run as a user that can read it, or fix the permissions. |
| `backup folder: not writable` | Fix the owner or mode of the folder (or of its nearest existing parent). |
| `key file: ... inside the backup folder` | Move the key file elsewhere. |
| `key file: no identity matched` | This key is not one of the keys behind `RECIPIENTS`. Use the right key or add its public key to `RECIPIENTS` (it applies to new backups only). |
| `[warn] newest backup` | Older than `MAX_AGE_DAYS`: the schedule has stopped. |

## Backup failures

Older backups are never deleted by a failed run. The message names the cause:

- `cannot read <path>`: a permission problem or a vanished file. Add it to `EXCLUDE` if it is a cache.
- `unexpected end of data` / a file changed while reading: an app wrote to it during the backup. Stop the app first or exclude it ([how-it-works.md](how-it-works.md#live-databases)).
- `age stopped while writing` / `age refused to encrypt`: age's own message follows.
- `a backup named ... already exists`: two runs started in the same second. Fix the schedule.
- No space: the temporary file is removed; free space and run again.

## Drill failures

| Message | Meaning |
|---|---|
| `no backup has ever run` / `no complete backup` | Nothing finished. Run `backup`. "unfinished files" means a backup crashed. |
| `N days old (limit M)` | Backups stopped, or the folder copy the drill sees is stale. |
| `does not match its manifest` | The archive changed or was damaged after it was written (disk fault, partial copy). Copy it again from another place. |
| `is not an age file` | The archive is not encrypted. Do not trust the folder. |
| `could not be restored: age could not decrypt: no identity matched` | Wrong key. |
| `could not be restored: ... truncated` | The archive is cut short or altered: age caught it. |
| `files differ from the manifest` | Something restored differently from what was recorded: investigate before trusting any backup from that run. |
| `not enough room in ...` | Point `SCRATCH_DIR` at a bigger disk. |
| `KEY_FILE ... missing or not readable` | Fix the path or the permission for the user running the drill. |
| `PARTIAL` | Not a failure; no key is set, so nothing was decrypted. |

A leftover `lombovault-drill-*` folder in the scratch folder is from a crashed drill: delete it.

## Retention

- "would delete" lines but nothing is removed: that is the dry run. Set `DRY_RUN=false`.
- Fewer backups than expected remain: retention keeps `KEEP` **complete** runs; unfinished ones do not count.
- A file of yours in the backup folder was not touched: correct, only the tool's own names are ever deleted.

## Alerts and pings

A dead webhook or ping prints `[warn] ...` and does not change the result. A ping only fires after the job, so a job that never started is noticed by the ping service, not by this tool: set a grace time there.
