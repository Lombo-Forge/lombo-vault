# Contracts other programs may rely on

## Contents

- [The status file](#the-status-file)
- [Exit codes](#exit-codes)
- [Pings and alerts](#pings-and-alerts)
- [The commands around a backup](#the-commands-around-a-backup)
- [What a version bump means](#what-a-version-bump-means)
- [Reading it from a monitor](#reading-it-from-a-monitor)

Everything below is a promise to other programs (a monitor, a dashboard, a script). If you change it, change this file in
the same pull request. Nothing here was tried against a real monitor.

## The status file

Set `LOMBO_VAULT_STATUS_FILE` to an absolute path outside every source. Empty (the default) means no file is written.
`backup` rewrites the `backup` half and `drill` rewrites the `drill` half; each keeps the other half as it was. `plan`,
`doctor`, `list` and `prune` never write it. It is written in dry run too: `DRY_RUN` only controls deleting old backups.

```json
{
  "version": 1,
  "updated": 1800000000,
  "backup": {
    "result": "ok",
    "time": 1800000000,
    "last_success": 1800000000,
    "stamp": "20261121T043000Z",
    "files": 1432,
    "bytes": 212884710,
    "archive_bytes": 98123456,
    "message": "backup 20261121T043000Z written"
  },
  "drill": {
    "result": "ok",
    "time": 1800003600,
    "last_success": 1800003600,
    "message": "backup 20261121T043000Z (0.0 days old) was decrypted and unpacked; all 1432 files (212884710 bytes) match the checksums written at backup time"
  }
}
```

| Field | Meaning |
|---|---|
| `version` | Whole number, `1` today. See [what a version bump means](#what-a-version-bump-means). |
| `updated` | Unix time in seconds of the last write of either half. |
| `backup`, `drill` | The result of the most recent run of that command, or `null` when it never ran (with this file) on this machine. |
| `result` | `backup`: `ok` or `fail`. `drill`: `ok`, `partial` or `fail`, as in [how-it-works.md](how-it-works.md#what-the-drill-results-mean). |
| `time` | Unix time in seconds when that run finished its work. |
| `last_success` | Unix time of the newest run of that command that counts as a success, kept across later failures; `null` when there has been none. For `drill` only `ok` counts (`partial` does not). For `backup` a success is a backup that was written, even if `POST_BACKUP_COMMAND` failed afterwards (then `result` is `fail` and `last_success` still moves, because the backup exists). |
| `stamp` | `backup` only. The backup's time stamp as in its file names, or `null` when the run made none. |
| `files`, `bytes`, `archive_bytes` | `backup` only. Files stored, their size before compression, and the size of the encrypted archive. `null` when the run made none. |
| `message` | One plain sentence, control characters replaced by `?`, at most 300 characters. For people; do not parse it. A failure message may contain paths from your own settings. |

**The file** is written to a temporary name (`<name>.tmp` beside it) and renamed, so a reader sees the old file or the new
one, never half of one. Mode 0644: it holds counts, times and messages, no secrets, and a monitor running as another user
has to be able to read it. If you do not want that, put it in a folder only that monitor can enter. If it cannot be
written, the run still succeeds and prints a warning on stderr. A damaged file, or one with another `version`, is replaced
by the next write. Two runs finishing at the same instant can lose one half; schedule `backup` and `drill` apart.

A file that stops changing is itself a signal: see [reading it from a monitor](#reading-it-from-a-monitor).

## Exit codes

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `backup` | A backup was written, and `POST_BACKUP_COMMAND` (if set) succeeded. | Nothing was written (any reason, including a failed `PRE_BACKUP_COMMAND`), or a backup was written but `POST_BACKUP_COMMAND` failed, or retention failed. | A setting is wrong; nothing ran. |
| `drill` | `OK` or `PARTIAL`. | `FAIL`. | A setting is wrong. |
| `doctor` | No `FAIL` line. | A `FAIL` line. | A setting is wrong. |
| `plan`, `list`, `prune` | Done. | Could not do it. | A setting is wrong. |

## Pings and alerts

- `LOMBO_VAULT_PING_URL` is requested (GET) once per `backup`: the address as set when the run worked, the address with
  `/fail` added to its path when it did not (including a failed `POST_BACKUP_COMMAND`). It is sent after the backup is
  written and before retention, so a failure in retention alone does not turn the ping red; it shows in the exit code.
- `LOMBO_VAULT_DRILL_PING_URL`: the same for `drill`. Only `OK` pings the success address; `PARTIAL` and `FAIL` ping `/fail`.
- `LOMBO_VAULT_WEBHOOK_URL` gets a JSON `POST` with the text under both `content` and `text`. It is sent for every failed
  backup, for a failed `POST_BACKUP_COMMAND`, and for every drill result. The wording is for people and may change;
  do not parse it.
- Nothing is sent when the tool does not run at all (a stopped timer, a dead machine). Only a monitor that notices
  silence can tell: use the ping with a grace period, or the status file's age.

## The commands around a backup

`LOMBO_VAULT_PRE_BACKUP_COMMAND` and `LOMBO_VAULT_POST_BACKUP_COMMAND` are each a program and its arguments, split like
a shell would split them (quotes work) but **never run by a shell**: no `;`, `|`, `$VAR` or `~`. To use those, point the
setting at a script of your own.

**`DRY_RUN` does not stop these commands:** a backup is always written, so they run with `DRY_RUN=true` too. Test a command that stops or changes anything by hand first.

- Order: pre command, then the scan and the backup, then the post command, then pings, alerts and the status file, then retention.
- The pre command runs before the sources are read, so files it makes inside a source are in the backup.
- If the pre command exits non-zero, cannot start or runs longer than `LOMBO_VAULT_COMMAND_TIMEOUT_SECONDS` (default 60,
  at most 3600), **no backup is made** and the run fails like any other failed backup. Older backups are not touched.
- The post command runs whenever it is set, after a successful backup and after any failure, so it can start again what the
  pre command stopped. If it fails, you get an alert and the exit code is 1; the backup that was written stays.
- Both get no input (stdin is closed). Their output goes to this program's output, so a journal or log shows it. Nothing
  is added to their environment.
- `plan`, `list`, `prune`, `drill` and `doctor` do not run them (`doctor` only checks that the program can be found).
- A command that ignores the time limit and starts background children can outlive its own kill: the limit stops the
  program it started, not what that program left running.

## What a version bump means

- **Same version:** a new, optional field may be added to the top level or to either half. A reader must ignore fields it does not know.
- **`version` goes up:** a field was removed or renamed, or its meaning or type changed. A reader that does not know the new number should treat the file as unreadable instead of guessing.

## Reading it from a monitor

1. Read the file. If it is missing, nothing was ever recorded on this machine: treat it as red once a first backup is due.
2. Check `version` is a number you know.
3. **Stale backup:** red when `now - backup.last_success` is more than your limit (for a nightly backup, 36 hours is a fair start), or when `backup` is `null`. Do not rely on `backup.result` alone: if the timer stops, it stays `ok` forever.
4. **Failed last run:** red when `backup.result` is `fail`.
5. **Drill:** red when `drill.result` is `fail`; amber when it is `partial`; red when `now - drill.last_success` is more than your limit (a monthly drill: 45 days).
6. Also check the file's own age (`updated`): a file that has not changed in days while backups should run means the jobs stopped.

For example, with `jq` (exits 0 when the backup is fresh and the last run worked; 36 hours is 129600 seconds):

```sh
jq -e '.version == 1 and .backup.result == "ok" and (now - .backup.last_success) < 129600' /var/lib/lombo-vault/status.json
```
