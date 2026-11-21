---
name: troubleshoot
description: Diagnose LomboVault problems from doctor, list and drill output. Use when a backup fails, the drill says FAIL or PARTIAL, doctor shows FAIL, retention deleted too much or too little, or alerts do not arrive.
---

# Troubleshoot LomboVault

Start with `lombo-vault doctor`, then `lombo-vault list`. `docs/troubleshooting.md` has a table of messages and fixes; read it rather than guessing.

Rules of thumb:

- A failed backup never deletes older backups. Say so to a worried user, then read the message: unreadable file, file changed while reading (an app was writing; stop it or exclude it),
  age's own error, or no space.
- `PARTIAL` is not a pass: no `KEY_FILE` is set where the drill runs, so nothing was decrypted. Help the user choose between a key on the server and a drill on another machine
  (`docs/setup.md`, `docs/security.md`); do not set one for them.
- `FAIL` with "no identity matched": the key is not one behind `RECIPIENTS`. Do not suggest changing recipients until they know which key they hold; a changed recipient list only affects new backups.
- `FAIL` with "does not match its manifest" or "truncated": the archive was damaged or copied badly. Copy it again from another place; do not just run the backup again and hope.
- Retention: "would delete" with nothing removed is the dry run (`DRY_RUN=true`). Fewer backups than `KEEP` after a crash is normal: unfinished runs do not count. The tool only deletes its own file names;
  never tell the user to `rm` things in the backup folder to "tidy up" without reading `list` first.
- A lost private key cannot be recovered. Never ask the user to paste a private key into the chat; ask for `doctor` output instead.
- A leftover `lombovault-drill-*` folder in the scratch folder is from a crashed drill and can be deleted (it holds decrypted data).
- When the user reports a bug, ask for `doctor` and `list` output with paths, webhook URLs and keys removed.
