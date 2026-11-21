---
name: install-into-my-stack
description: Help a user set up LomboVault for their own media-server stack: make an age key pair, choose sources and a backup folder, write the settings, schedule backups and drills, and verify with doctor. Use when someone asks to set it up, install it or add backups.
---

# Install LomboVault into the user's stack

Read `README.md`, `docs/setup.md` and `docs/security.md` first. Never ask the user to paste a private key; never print one.

1. **Ask**, one short question at a time: where do the app settings and databases live (the folders to back up; not the media)? Which disk could hold backups that is not the disk holding the data?
   Is the `age` program installed (`age --version`), or will it run in the container? Which machine should run the drill, and is the user willing to keep the private key on the server (convenient, weaker)
   or on another machine (stronger, needs copying the backups there)? Do they have a webhook and a healthchecks-style ping URL?
2. **Key pair:** have the user run `age-keygen -o vault-key.txt` themselves, store the private key in their password manager first, and give you only the `age1...` public key. Offer a second recipient
   (a key stored elsewhere) so one lost key is not fatal.
3. **Settings:** copy `.env.example` to `.env` and fill it from their answers: `SOURCES`, `BACKUP_DIR` (outside every source), `RECIPIENTS`, `EXCLUDE` (caches, logs), and the alert URLs. Leave `DRY_RUN=true`.
   Warn about live databases (`docs/how-it-works.md`): offer to stop the app around the backup, or to back up a copy made with the database's own backup command.
4. **Verify:** `lombo-vault doctor` must show no FAIL. `plan`, then `backup`, then `list`.
5. **Drill:** set `KEY_FILE` only on the machine the user chose (mode 600, outside the backup folder), run `lombo-vault drill`, and expect `OK`. If it says `PARTIAL`, explain that nothing was decrypted.
6. **Schedule:** a systemd timer or cron job for `backup` (nightly) and for `drill` (weekly or monthly), each with its own ping URL (examples in `docs/setup.md`).
7. **Retention:** after about a week, show `lombo-vault prune`; only when the list is old runs only, suggest `DRY_RUN=false` and `KEEP` of at least 3.
8. Remind them to copy the encrypted backups to a second place (another disk, another house), and to practise the real restore command from `docs/setup.md` once.
