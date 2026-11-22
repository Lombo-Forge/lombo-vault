# How it works

## Contents

- [A backup, step by step](#a-backup-step-by-step)
- [Live databases](#live-databases)
- [Retention](#retention)
- [The restore drill, step by step](#the-restore-drill-step-by-step)
- [What the drill results mean](#what-the-drill-results-mean)
- [What gets stored](#what-gets-stored)

## A backup, step by step

0. If `PRE_BACKUP_COMMAND` is set, run it. If it fails, stop here (no backup, older ones kept).
1. Check that every source exists and that the backup folder does not overlap a source.
2. Walk the sources (not following symlinks) and list files, folders and links. Sockets and pipes are skipped and reported; paths matching `EXCLUDE` are left out and reported.
3. Start `age -r <key> -r <key> ...`. Write a gzip-compressed tar into it. Every file is hashed as it is read. The age output goes into `.lombovault_<time>.tar.gz.age.tmp`.
4. Check the output starts with the age header (so a wrong program in `AGE_BIN` cannot produce a plain "backup"), hash the finished file, write the manifest to a temporary name.
5. `fsync`, then rename the archive, then the manifest. Only now does the backup exist.
6. If `POST_BACKUP_COMMAND` is set, run it (it also runs after a failure).
7. Ping, alert on failure, update the status file if `STATUS_FILE` is set ([contracts.md](contracts.md)), then apply retention.

Any error in steps 0-5 removes the temporary files, alerts and exits 1. Older backups are never touched by a failed run.

## Live databases

A file that is being written while it is read can be copied in an inconsistent state. For SQLite (Sonarr, Radarr, Jellyfin and most other apps) the safe options are:

- stop the app for the minute the backup takes (a `docker stop` before and `docker start` after, in your timer's script), or
- make a consistent copy first with the database's own command (`sqlite3 app.db ".backup /somewhere/app.db"`) and back up the copy.

LomboVault does neither by itself, on purpose: it has no database-specific code and no business stopping your containers. It gives you two hooks instead: `LOMBO_VAULT_PRE_BACKUP_COMMAND` runs before the sources are read, and `LOMBO_VAULT_POST_BACKUP_COMMAND` runs after, also when the backup failed. Both are a program and arguments (no shell; to use pipes or variables, write a small script and name it), with a time limit of `COMMAND_TIMEOUT_SECONDS` (60 by default). A failing pre command means no backup is made, so a failed dump can never be stored as if it were good. Exact rules: [contracts.md](contracts.md#the-commands-around-a-backup). If a file shrinks or vanishes mid-backup, the backup fails instead of storing a damaged file. Use `EXCLUDE` for caches and logs.

Recipes (yours to adapt; names are examples). Put the copies in a folder that is one of your `SOURCES`, on the same disk as the data (not in the backup folder, which a source must not contain), and keep the live files out of the backup with `EXCLUDE` so each database is stored once, as the consistent copy:

```sh
#!/bin/sh
# /usr/local/bin/vault-pre.sh   (LOMBO_VAULT_PRE_BACKUP_COMMAND=/usr/local/bin/vault-pre.sh)
set -eu
stage=/mnt/ssd/db-copies                       # listed in LOMBO_VAULT_SOURCES
mkdir -p "$stage"
for db in /mnt/ssd/appdata/sonarr/sonarr.db /mnt/ssd/appdata/radarr/radarr.db; do
  copy="$stage/$(basename "$db").copy"
  rm -f "$copy"
  sqlite3 "file:$db?mode=ro" ".timeout 20000" ".backup '$copy'"
  [ "$(sqlite3 "$copy" 'PRAGMA integrity_check;')" = ok ]   # a bad copy fails the backup
done
# PostgreSQL in a container: a dump is the consistent copy
# docker exec db pg_dump -U app -Fc app > "$stage/app.dump"
```

With `LOMBO_VAULT_EXCLUDE=*.db,*.db-wal,*.db-shm,*.db-journal` the live files are left out and the `.copy` files are stored. On a restore, rename `x.db.copy` to `x.db` (and make sure no old `-wal` file sits next to it). `sqlite3 .backup` is the database's own safe copy, so apps keep running. Check the copy as the script above does: the drill proves the bytes are the ones written at backup time, not that the database inside is healthy.

To stop an app instead: the pre command runs `docker stop NAME`, the post command runs `docker start NAME`. The post command runs even when the backup failed, so the app does not stay down.

## Retention

Only one rule exists: keep the newest `KEEP` complete backups. There is no daily/weekly/monthly mix. For longer reach, run a second instance with its own settings file and its own backup folder, scheduled monthly with `KEEP=12`; both instances can share the same sources and recipients, and each needs its own folder (see below). For a real grandfather-father-son policy use restic or borg.

After a successful backup (or `prune`), the newest `KEEP` **complete** runs are kept. Older runs, including half-finished ones older than the oldest kept run, are candidates. Fewer than `KEEP` complete runs: nothing is deleted. A run counts as complete from its file names alone (a backup of zero files is refused up front, so an empty one cannot appear), and a run whose date is more than a day ahead of the clock is not counted. Names with digits that are not a real date are not the tool's files and are never touched. Use one backup folder per tool instance and put nothing else of the same name pattern in it. Stale temp files from a crashed run are removed once they are over a day old. Only files matching the tool's own names are ever candidates. While `DRY_RUN=true` they are only listed.

## The restore drill, step by step

1. Find the newest **complete** backup (archive and manifest). None: fail.
2. Older than `MAX_AGE_DAYS`: fail (the backups have stopped).
3. Read the manifest; hash the archive and compare with the manifest; check the age header. Mismatch: fail.
4. No `KEY_FILE`: stop with `PARTIAL`.
5. Check there is room in the scratch folder.
6. Run `age -d -i <key file>` on the archive and unpack the stream into a fresh folder under the scratch folder (paths that would leave the folder are refused; restored files get owner read/write so they can be checked).
7. Read age to the very end: age authenticates the whole file, so a truncated or altered archive fails here even if the manifest was rewritten to match.
8. Hash every restored file and compare with the manifest: missing, extra or changed files fail.
9. Delete the scratch folder (also on failure) and report.

The drill only reads the backup folder and writes only inside its own scratch folder.

## What the drill results mean

| Result | Meaning | Exit |
|---|---|---|
| `OK` | The newest backup decrypted with your key and unpacked to exactly the files and bytes recorded at backup time. | 0 |
| `PARTIAL` | The backup is fresh, intact and an age file, but it was **not decrypted** (no key file set here). The drill ping goes to the `/fail` address. | 0 |
| `FAIL` | No backup, too old or dated in the future, damaged, wrong key, would not unpack, files differ, or the archive holds more than its manifest says or a member that is not a file, folder or link. | 1 |

## What gets stored

File contents, names, modes, owners (numbers) and times, symlinks as links, empty folders. Not stored: extended attributes, ACLs, hard-link relationships, sockets, device files.
