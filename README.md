<p align="center"><img src="assets/banner.svg" alt="LomboVault" width="100%"></p>

<p align="center">
  <a href="LICENSE"><img alt="License: GPL-3.0" src="https://img.shields.io/github/license/Lombo-Forge/lombo-vault"></a>
  <a href="https://github.com/Lombo-Forge/lombo-vault/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Lombo-Forge/lombo-vault/ci.yml?branch=main&label=CI"></a>
  <a href="https://github.com/Lombo-Forge/lombo-vault/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://img.shields.io/github/actions/workflow/status/Lombo-Forge/lombo-vault/codeql.yml?branch=main&label=CodeQL"></a>
  <a href="https://github.com/Lombo-Forge/lombo-vault/releases"><img alt="Release" src="https://img.shields.io/github/v/release/Lombo-Forge/lombo-vault?include_prereleases"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue">
  <a href="https://ko-fi.com/datlombardo"><img alt="Support on Ko-fi" src="https://img.shields.io/badge/Ko--fi-support-FF5E5B?logo=ko-fi&logoColor=white"></a>
</p>

# LomboVault

Makes an encrypted ([age](https://age-encryption.org)) backup of the config and databases of a self-hosted media stack, keeps the last few, and **every so often restores the newest one into a scratch folder and checks every file against the checksums written when it was made**. If the restore fails, it tells you. A backup you have never restored is a hope, not a backup; the drill is the point of this tool.

> **Status: not release-ready.** Tested with a stand-in `age` and, in a throwaway container, with the real `age` 1.2.1 (see [what is not verified](#what-is-not-verified)). Not run against a real stack, no independent review. Keep `DRY_RUN=true` (the default) until you have read the output of `plan`.

```text
$ lombo-vault drill
restore drill: OK: backup 20261121T043000Z (0.9 days old) was decrypted and unpacked; all 1432 files (212884710 bytes) match the checksums written at backup time
```

## Contents

- [When to use restic or borg instead](#when-to-use-restic-or-borg-instead)
- [What it does and does not do](#what-it-does-and-does-not-do)
- [Install in a minute](#install-in-a-minute)
- [Commands](#commands)
- [Settings](#settings)
- [Scheduling](#scheduling)
- [What is not verified](#what-is-not-verified)
- [FAQ](#faq)
- [Contributing and licence](#contributing-and-licence)

## When to use restic or borg instead

Use [restic](https://restic.net) or [borg](https://www.borgbackup.org) if you want any of these: deduplication and snapshots of large or many-times-changing data, backing up to cloud or remote storage, mounting an old snapshot, pruning by "daily, weekly, monthly", or a tool with years of field testing and a large community. They are better backup tools than this one will ever be, and both have their own `check` and restore commands.

LomboVault is for a narrower job: **small** data (app settings, databases, compose files; not the media), written as whole encrypted files you can copy anywhere, plus a restore drill that reports by webhook. If you already use restic or borg, you do not need this. If you want a second, simple, boring copy that you can open with one `age` command, or a drill that runs without you remembering, it may fit.

## What it does and does not do

- `backup` packs the folders and files you list into a compressed tar, **pipes it through the `age` program** (never its own crypto), and writes it into the backup folder under a temporary name, then renames it. Next to it goes a manifest: the SHA-256 of the archive and of every file inside.
- The tool holds only age **public** keys (recipients). `backup` never reads a private key. The private key is only named (as a file path) for the drill.
- `drill` restores the newest complete backup into a scratch folder, hashes every restored file, compares with the manifest, deletes the scratch folder and reports `OK`, `PARTIAL` or `FAIL` (exit code 1 on fail) by webhook and ping.
- **Retention** keeps the newest N complete backups. It only ever deletes files whose names match the tool's own pattern (`lombovault_<time>.tar.gz.age`, `.manifest.json` and their old temp files) inside the backup folder; anything else you put there is never touched. It is a **dry run until you set `DRY_RUN=false`**, and a failed backup never deletes anything.
- It **refuses** a backup folder that is inside a source (or a source inside the backup folder), a key file or scratch folder inside a source, and a backup that would hold **no files at all** (a wrong path, a missing mount, an `EXCLUDE` that matches everything). A source that is a symlink is followed. `EXCLUDE` names are matched only below each source, never against the folders above it.
- **Give the backup folder to this tool alone.** Retention counts and deletes every file in it that has the tool's name pattern with a real date, so a second instance (other sources or keys) or backups copied in by hand count towards each other's `KEEP`. Stamps dated more than a day ahead (a wrong clock) are never counted and the drill fails on them.

What it does **not** do: it does not snapshot live databases (see [docs/how-it-works.md](docs/how-it-works.md): stop the app, or back up a copy made with the database's own backup command), does not deduplicate, does not upload anywhere, does not restore over live data for you (the restore is one documented `age` + `tar` command, in [docs/setup.md](docs/setup.md)), and does not store or guard your private key. **If you lose the private key, the backups are unreadable, forever.** Read [docs/security.md](docs/security.md).

A `PARTIAL` drill result means the drill checked that the newest backup is fresh, intact (its checksum matches) and really starts as an age file, but could **not decrypt it** because no key file is set on that machine. It is not a pass: the exit code is 0, but the drill ping goes to the `/fail` address, so a monitor on that ping stays red until a key is set.

## Install in a minute

1. Install [age](https://age-encryption.org) (`apt install age`, or the release from its site). The Docker image has it.
2. Make a key pair **on a machine you trust**, and put the private key in your password manager before anything else:
   ```sh
   age-keygen -o vault-key.txt      # prints the public key (age1...)
   ```
3. Copy `.env.example` to `.env`; fill in `LOMBO_VAULT_SOURCES`, `LOMBO_VAULT_BACKUP_DIR` (outside every source, ideally another disk) and `LOMBO_VAULT_RECIPIENTS` (the public key only).
4. Check and look before doing:
   ```sh
   lombo-vault doctor
   lombo-vault plan
   lombo-vault backup
   ```
5. Prove it: put the private key file where the drill can read it (`LOMBO_VAULT_KEY_FILE`, chmod 600; or run the drill from another machine with the backups copied over) and run `lombo-vault drill`.
6. Schedule both ([below](#scheduling)), then set `LOMBO_VAULT_DRY_RUN=false` when you trust what `plan` says it will delete.

Details: [docs/setup.md](docs/setup.md).

## Commands

| Command | What it does |
|---|---|
| `lombo-vault doctor` | Checks settings, the `age` program, that your recipients are accepted, the folders and (if set) that the key opens a test message. Exit code 1 if something fails. |
| `lombo-vault plan` | Shows what a backup would contain and what retention would delete. Writes nothing. |
| `lombo-vault backup` | Makes one backup, pings, then applies retention (deleting only when `DRY_RUN=false`). |
| `lombo-vault drill` | The restore drill. Exit 0 for `OK` and `PARTIAL`, 1 for `FAIL`. Only `OK` pings the success address. |
| `lombo-vault list` | Lists the backups, newest first, and flags unfinished ones. |
| `lombo-vault prune` | Applies retention alone (deleting only when `DRY_RUN=false`). |

## Settings

Everything is an environment variable with the `LOMBO_VAULT_` prefix, listed with defaults in [.env.example](.env.example). A bad value stops the app at start-up and names the variable. `backup` refuses a backup folder on the same disk as a source (the backup would die with the data) and `doctor` shows FAIL, unless `LOMBO_VAULT_ALLOW_SAME_DISK=true`. Note that `DRY_RUN` only controls **deleting old backups**: a backup is always written.

## Scheduling

The tool runs once and exits; let cron or a systemd timer call it (examples in [docs/setup.md](docs/setup.md)): `backup` nightly, `drill` weekly or monthly. Give each its own ping URL (healthchecks-style) so silence is noticed.

## What is not verified

- Not run against a real media stack or real app databases. SQLite files copied while an app is writing can be inconsistent; the tool does not prevent that (see [docs/how-it-works.md](docs/how-it-works.md)).
- The real `age` 1.2.1 (Debian package) was exercised in a throwaway container: backup, drill, two recipients, wrong key, a flipped byte and a truncated file. No other age version, no `rage`, no SSH-key recipients (not supported), no passphrase mode (not supported).
- The same-disk refusal is tested with made-up device ids, never with two real disks. It compares device ids only, so it cannot see network shares, bind mounts, overlay filesystems or disk pools (see [docs/security.md](docs/security.md)).
- The webhook and ping were tested against a local server, not a real Discord, Slack or healthchecks service.
- The fixes for the third audit (symlinked or empty sources, wrong clocks, junk file names, bad alert addresses, the drill's size and file-type checks) are covered by tests with the stand-in `age`; the real-`age` suite was re-run after them (see FIXES3-REPORT.md in the workspace). Disk-full and power-cut behaviour, two truly simultaneous runs, and a damaged-but-complete newest backup (only the drill, not retention, notices one) were reasoned about, not tested.
- The manifest is not encrypted and holds an unsalted checksum of every file (see [docs/security.md](docs/security.md)).
- Files with extended attributes, ACLs and hard links are not preserved; ownership is stored but only restored by a root restore.
- The container image was not built and the CI workflows were not run.
- No independent review. The threat model is in [docs/security.md](docs/security.md).

## FAQ

**Why a whole new archive every time?** Simplicity: each file stands alone, so deleting an old one cannot break a newer one. It suits data measured in megabytes or a few gigabytes.

**Where should the backups go?** Not on the disk that holds the data they protect, and not only in the same house. Copy the encrypted files elsewhere with your own tool. They are safe to copy because they are encrypted. [docs/setup.md](docs/setup.md#7-copy-the-backups-elsewhere) has an rsync and an rclone recipe, and [who should run it](docs/setup.md#6-who-runs-it).

**Can I use my SSH key?** Not in this version: only `age1...` public keys.

**What does the drill prove?** That the newest backup decrypts with your key and unpacks to files identical to those at backup time. It does not prove the apps start from them. See [docs/security.md](docs/security.md).

## Contributing and licence

Contribution, security and conduct rules come from the [Lombo-Forge organisation](https://github.com/Lombo-Forge/.github). Licensed under [GPL-3.0](LICENSE). Not affiliated with or endorsed by the age, restic, borg or Jellyfin projects.
