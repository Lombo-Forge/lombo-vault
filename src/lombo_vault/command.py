"""Runs the optional commands around a backup (PRE_BACKUP_COMMAND, POST_BACKUP_COMMAND).

No shell: the setting is split into a program and arguments, so nothing in it is ever expanded or piped.
The command's own output goes to this program's output (so a journal or log shows why it failed); stdin is closed.
"""

import subprocess


def run(argv: tuple[str, ...], timeout: int) -> str | None:
    """Run the command. Returns None when it exited 0 (or there is no command), else a short problem text."""
    if not argv:
        return None
    try:
        done = subprocess.run(  # noqa: S603  # no shell; the command is the owner's own setting
            list(argv), stdin=subprocess.DEVNULL, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout} s"
    except OSError as e:
        return f"could not start ({e.strerror or type(e).__name__})"
    return None if done.returncode == 0 else f"exit code {done.returncode}"
