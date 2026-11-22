# Setup

## Contents

- [Before you start](#before-you-start)
- [1. Make the key pair](#1-make-the-key-pair)
- [2. Settings](#2-settings)
- [3. First backup](#3-first-backup)
- [4. First drill](#4-first-drill)
- [5. Scheduling](#5-scheduling)
- [6. Who runs it](#6-who-runs-it)
- [7. Copy the backups elsewhere](#7-copy-the-backups-elsewhere)
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

- What goes in `SOURCES`: every folder and file you cannot rebuild, including single files such as a `.env` or a compose file. They are stored inside the encrypted archive like everything else, so secrets in them are protected by the key, not by file permissions: the backup holds every token and password those files hold, and so does anyone who holds both the backups and the private key. Keep the key apart from the backups ([security.md](security.md)).
- `LOMBO_VAULT_PRE_BACKUP_COMMAND`, `LOMBO_VAULT_POST_BACKUP_COMMAND`, `LOMBO_VAULT_COMMAND_TIMEOUT_SECONDS`, `LOMBO_VAULT_STATUS_FILE`: optional ([how-it-works.md](how-it-works.md#live-databases), [contracts.md](contracts.md)).

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

If a database needs a consistent copy, or an app must be stopped for the minute the backup takes, use `LOMBO_VAULT_PRE_BACKUP_COMMAND` and `LOMBO_VAULT_POST_BACKUP_COMMAND` with a small script of your own, with recipes in [how-it-works.md](how-it-works.md#live-databases). The post command runs even when the backup failed, so a stopped app is started again. To let a monitor see the result, set `LOMBO_VAULT_STATUS_FILE` ([contracts.md](contracts.md)); give the drill's timer the same value if it runs on the same machine.

## 6. Who runs it

Run the backup as one dedicated user (for example `vault`), not your own login and not an app's user.

- Give that user **read** access to the folders in `SOURCES` only. Do not add it to groups it does not need.
- The backup folder belongs to that user, mode `700`, so no app or other login can read or delete the backups. A
  backup that every app can write to is a backup that a broken app can damage.
- Never put the private key (`vault-key.txt`) on this machine unless you chose to drill on the server (step 4).
- Running as root works and reads everything, but then a bug in the tool is a root bug. Use root only if a source
  cannot be read any other way.

```sh
sudo useradd --system --home /var/lib/lombo-vault --create-home vault
sudo install -d -o vault -g vault -m 700 /var/backups/lombo-vault
```

Use `User=vault` in the systemd service above. Check with `lombo-vault doctor` as that user.

## 7. Copy the backups elsewhere

A backup that sits on the machine it protects is lost with it. The files are encrypted, so copying them is safe.
`lombo-vault` does not copy them for you: use one of these on **another** machine, so a hacked server cannot reach
back and delete the copies. Replace the names in capitals.

**rsync, pulled by the other machine** (the other machine logs in to the server with its own read-only ssh key):

```sh
rsync -a --partial -e ssh VAULT_USER@SERVER:/var/backups/lombo-vault/ /mnt/offsite/lombo-vault/
```

No `--delete`: when Vault's retention removes an old backup, the copy keeps it. Prune the copy yourself (for example
`find /mnt/offsite/lombo-vault -mtime +90 -delete`) when you want it smaller. Run it from a timer after the backup time.

**rclone to a cloud bucket or another remote** (set the remote up first with `rclone config`):

```sh
rclone copy /var/backups/lombo-vault REMOTE:lombo-vault --immutable
```

`copy` never deletes at the far end, and `--immutable` makes it refuse to change a file that is already there (backup
names carry the time, so a changed old file means something is wrong). Give the remote's credentials write access
but not delete access if the provider allows it.

**rsync, pulled by a key that can only read.** In the `vault` user's `~/.ssh/authorized_keys` on the server, one line per
other machine (`rrsync` ships with the rsync package; its path differs between systems):

```text
restrict,command="rrsync -ro /var/backups/lombo-vault" ssh-ed25519 AAAA... offsite-pull
```

That key can read the backup folder with rsync and do nothing else, so a stolen offsite machine cannot change or delete the
server's backups. The files are owned by the backup user and closed to others (folder `700`, files `600`), so the
pull must log in as that user; the tool does not set up a shared group for you.

Nothing tells Vault whether the copy ran. Give the copy job its own ping (healthchecks-style), or check the age of the newest
file at the far end from your monitor ([README](../README.md#plugging-it-into-your-own-monitoring)).

Whichever you pick, **drill the copy**, not just the original: copy the newest file somewhere with the key and run
`lombo-vault drill` there (step 4). A copy nobody has restored is a guess.

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
