# How it works

## Contents

- [A backup, step by step](#a-backup-step-by-step)
- [Live databases](#live-databases)
- [Retention](#retention)
- [The restore drill, step by step](#the-restore-drill-step-by-step)
- [What the drill results mean](#what-the-drill-results-mean)
- [What gets stored](#what-gets-stored)

## A backup, step by step

1. Check that every source exists and that the backup folder does not overlap a source.
2. Walk the sources (not following symlinks) and list files, folders and links. Sockets and pipes are skipped and reported; paths matching `EXCLUDE` are left out and reported.
3. Start `age -r <key> -r <key> ...`. Write a gzip-compressed tar into it. Every file is hashed as it is read. The age output goes into `.lombovault_<time>.tar.gz.age.tmp`.
4. Check the output starts with the age header (so a wrong program in `AGE_BIN` cannot produce a plain "backup"), hash the finished file, write the manifest to a temporary name.
5. `fsync`, then rename the archive, then the manifest. Only now does the backup exist.
6. Ping, then apply retention.

Any error in steps 1-5 removes the temporary files, alerts and exits 1. Older backups are never touched by a failed run.

## Live databases

A file that is being written while it is read can be copied in an inconsistent state. For SQLite (Sonarr, Radarr, Jellyfin and most other apps) the safe options are:

- stop the app for the minute the backup takes (a `docker stop` before and `docker start` after, in your timer's script), or
- make a consistent copy first with the database's own command (`sqlite3 app.db ".backup /somewhere/app.db"`) and back up the copy.

LomboVault does neither for you, on purpose: it has no business stopping your containers. If a file shrinks or vanishes mid-backup, the backup fails instead of storing a damaged file. Use `EXCLUDE` for caches and logs.

## Retention

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
