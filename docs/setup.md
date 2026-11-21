# Setup

## Contents

- [Before you start](#before-you-start)
- [1. Make the key pair](#1-make-the-key-pair)
- [2. Settings](#2-settings)
- [3. First backup](#3-first-backup)
- [4. First drill](#4-first-drill)
- [5. Scheduling](#5-scheduling)
- [Running in a container](#running-in-a-container)
- [Restoring for real](#restoring-for-real)
- [Moving to live retention](#moving-to-live-retention)

## Before you start

You need Python 3.12 and the `age` program (`apt install age`, or a release from <https://age-encryption.org>), or the container image, which has both. Read [security.md](security.md) once: the key decisions are yours.

## 1. Make the key pair

On a machine you trust (not necessarily the server):

```sh
age-keygen -o vault-key.txt
```

It prints `Public key: age1...`. The file `vault-key.txt` holds the **private** key.

- Put the private key in your password manager **now**. Without it the backups can never be opened.
- Only the public key (`age1...`) goes into LomboVault's settings.
- You may list several recipients (for example one key on the server for the drill, one in a safe place): any one of the matching private keys can open a backup.

## 2. Settings

Copy `.env.example` to `.env` and set:

- `LOMBO_VAULT_SOURCES`: the folders and files to back up. App settings and databases, compose files; not the media.
- `LOMBO_VAULT_BACKUP_DIR`: on a different disk from the sources if you can. It must not be inside a source.
- `LOMBO_VAULT_RECIPIENTS`: the public key(s).
- `LOMBO_VAULT_EXCLUDE`: caches, logs, transcode folders.
- `LOMBO_VAULT_WEBHOOK_URL`, `LOMBO_VAULT_PING_URL`, `LOMBO_VAULT_DRILL_PING_URL`: optional but recommended.

Then run `lombo-vault doctor`. Every line should say `ok` (the key file line is a `warn` until you set one).

## 3. First backup

```sh
lombo-vault plan      # what would go in, what retention would delete
lombo-vault backup
lombo-vault list
```

## 4. First drill

The drill needs the private key file on the machine that runs it. Choose one:

- **Drill on the server** with `LOMBO_VAULT_KEY_FILE=/path/to/vault-key.txt` (mode 600, outside the backup folder and outside every source). Simple, and the drill runs by itself. The price: **anyone who can read the server's disk can now decrypt the backups.** That removes most of the point of encrypting the backups against a stolen server.
- **Drill elsewhere**: copy the backup folder to another machine that holds the key (rsync, USB drive) and run `lombo-vault drill` there with the same `RECIPIENTS`, `BACKUP_DIR` pointing at the copy, and `KEY_FILE`. The server never holds the key. You have to remember to do it; set `MAX_AGE_DAYS` to match how often you copy.
- **No key file**: the drill reports `PARTIAL` and pings the `/fail` address of `DRILL_PING_URL`. Better than nothing, but it is not a restore test.

One backup folder per tool instance, with nothing else in it: retention counts every file with the tool's name pattern.

```sh
lombo-vault drill
```

## 5. Scheduling

systemd (as a user with read access to the sources), `/etc/systemd/system/lombo-vault-backup.service`:

```ini
[Service]
Type=oneshot
EnvironmentFile=/etc/lombo-vault.env
ExecStart=/usr/local/bin/lombo-vault backup
```

`/etc/systemd/system/lombo-vault-backup.timer`:

```ini
[Timer]
OnCalendar=*-*-* 04:30:00
Persistent=true
[Install]
WantedBy=timers.target
```

Make a second pair for `drill` with `OnCalendar=monthly` (or weekly). Cron works too: `30 4 * * * . /etc/lombo-vault.env && lombo-vault backup` needs the variables exported; a systemd `EnvironmentFile` is simpler.

If an app must be stopped for a consistent copy ([how-it-works.md](how-it-works.md#live-databases)), use `ExecStartPre=` and `ExecStopPost=` lines to stop and start it.

## Running in a container

`compose.example.yaml` has a service that runs one command and exits. Mount the sources **read-only**, the backup folder read-write, and the key file read-only only if the container runs the drill:

```sh
docker compose -f compose.example.yaml run --rm lombo-vault backup
docker compose -f compose.example.yaml run --rm lombo-vault drill
```

Schedule those two lines with cron or a timer on the host.

## Restoring for real

LomboVault has no restore command on purpose. With the private key file:

```sh
mkdir restored
age -d -i vault-key.txt lombovault_<time>.tar.gz.age | tar -xzf - -C restored
```

Look at `restored/` (paths inside are the original absolute paths without the leading slash). Stop the app, copy the files back into place, start the app. Running `tar` as root restores the original owners; otherwise fix ownership with `chown`. Never extract over live data with `-C /` until you have looked.

## Moving to live retention

After a week of backups: `lombo-vault prune` shows what it would delete. If that is only old runs, set `LOMBO_VAULT_DRY_RUN=false`. Keep at least 3 generations: a corrupted source is backed up faithfully, and you want a few older copies to go back to.
