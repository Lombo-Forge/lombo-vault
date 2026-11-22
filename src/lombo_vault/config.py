"""Settings, read once from environment variables (see .env.example).

Every setting has a LOMBO_VAULT_ prefix so it never collides with another container's variables.
A bad value stops the app at start-up with a message that names the variable.
There is deliberately no setting that holds a private key: only a path to a key file (KEY_FILE),
and only `drill` and `doctor` ever read it.
"""

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

PREFIX = "LOMBO_VAULT_"

SETTINGS = (
    "SOURCES",
    "BACKUP_DIR",
    "RECIPIENTS",
    "KEEP",
    "EXCLUDE",
    "AGE_BIN",
    "KEY_FILE",
    "MAX_AGE_DAYS",
    "SCRATCH_DIR",
    "WEBHOOK_URL",
    "PING_URL",
    "DRILL_PING_URL",
    "DRY_RUN",
    "ALLOW_SAME_DISK",
    "PRE_BACKUP_COMMAND",
    "POST_BACKUP_COMMAND",
    "COMMAND_TIMEOUT_SECONDS",
    "STATUS_FILE",
)

# An age public key: "age1" then 58 lowercase bech32 characters. SSH-key recipients are not accepted (kept simple).
RECIPIENT_RE = re.compile(r"age1[02-9ac-hj-np-z]{58}")


class ConfigError(Exception):
    """A setting is missing or invalid. The message says which one and what to do."""


@dataclass(frozen=True)
class Config:
    sources: tuple[Path, ...]
    backup_dir: Path
    recipients: tuple[str, ...]
    keep: int
    exclude: tuple[str, ...]
    age_bin: str
    key_file: Path | None
    max_age_days: int
    scratch_dir: Path | None
    webhook_url: str | None
    ping_url: str | None
    drill_ping_url: str | None
    dry_run: bool
    allow_same_disk: bool
    pre_backup_command: tuple[str, ...]  # empty means none; run without a shell
    post_backup_command: tuple[str, ...]
    command_timeout: int
    status_file: Path | None


def _truthy(value: str, name: str) -> bool:
    """Strict on/off: a typo such as DRY_RUN=treu is an error, never a silent 'off'."""
    word = value.strip().lower()
    if word in {"1", "true", "yes", "on"}:
        return True
    if word in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{PREFIX}{name} must be true or false (got {value!r})")


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _absolute(text: str, name: str) -> Path:
    path = Path(text)
    if not path.is_absolute():
        raise ConfigError(f"{PREFIX}{name} must be an absolute path (got {text!r})")
    if ".." in path.parts:
        raise ConfigError(f"{PREFIX}{name} must not contain '..' (got {text!r})")
    return path


def _url(value: str, name: str) -> str | None:
    if not value:
        return None
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ConfigError(f"{PREFIX}{name} must not contain spaces or control characters")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{PREFIX}{name} must start with http:// or https:// (or be empty)")
    return value


def _command(value: str, name: str) -> tuple[str, ...]:
    """A program and its arguments, split like a shell would but never run by one. Empty means none."""
    try:
        return tuple(shlex.split(value))
    except ValueError as e:
        raise ConfigError(f"{PREFIX}{name} is not a valid command: {e}") from None


def _whole(value: str, name: str, minimum: int, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ConfigError(f"{PREFIX}{name} must be a whole number") from None
    if number < minimum:
        raise ConfigError(f"{PREFIX}{name} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ConfigError(f"{PREFIX}{name} must be at most {maximum}")
    return number


def from_env(env: dict[str, str]) -> Config:
    def get(name: str, default: str = "") -> str:
        return (env.get(PREFIX + name) or default).strip()

    sources = tuple(_absolute(p, "SOURCES") for p in _split(get("SOURCES")))
    if not sources:
        raise ConfigError(f"{PREFIX}SOURCES is not set. List the folders or files to back up, comma separated.")

    backup_dir_text = get("BACKUP_DIR")
    if not backup_dir_text:
        raise ConfigError(f"{PREFIX}BACKUP_DIR is not set. Name the folder the backups go into.")
    backup_dir = _absolute(backup_dir_text, "BACKUP_DIR")

    recipients = tuple(_split(get("RECIPIENTS")))
    if not recipients:
        raise ConfigError(f"{PREFIX}RECIPIENTS is not set. Put one or more age PUBLIC keys (age1...) here.")
    for r in recipients:
        if r.upper().startswith("AGE-SECRET-KEY-"):
            raise ConfigError(
                f"{PREFIX}RECIPIENTS holds a PRIVATE key. Remove it from this file now and use the public key "
                "(age1...) instead; the private key belongs in your password manager."
            )
        if not RECIPIENT_RE.fullmatch(r):
            raise ConfigError(f"{PREFIX}RECIPIENTS entry {r[:12]!r}... is not an age public key (age1 + 58 characters)")

    key = get("KEY_FILE")
    scratch = get("SCRATCH_DIR")
    status = get("STATUS_FILE")
    return Config(
        sources=sources,
        backup_dir=backup_dir,
        recipients=recipients,
        keep=_whole(get("KEEP", "7"), "KEEP", 1),
        exclude=tuple(_split(get("EXCLUDE"))),
        age_bin=get("AGE_BIN", "age"),
        key_file=_absolute(key, "KEY_FILE") if key else None,
        max_age_days=_whole(get("MAX_AGE_DAYS", "10"), "MAX_AGE_DAYS", 1),
        scratch_dir=_absolute(scratch, "SCRATCH_DIR") if scratch else None,
        webhook_url=_url(get("WEBHOOK_URL"), "WEBHOOK_URL"),
        ping_url=_url(get("PING_URL"), "PING_URL"),
        drill_ping_url=_url(get("DRILL_PING_URL"), "DRILL_PING_URL"),
        # Safe by default: old backups are only deleted after the person sets DRY_RUN=false.
        dry_run=_truthy(get("DRY_RUN", "true") or "true", "DRY_RUN"),
        allow_same_disk=_truthy(get("ALLOW_SAME_DISK", "false") or "false", "ALLOW_SAME_DISK"),
        pre_backup_command=_command(get("PRE_BACKUP_COMMAND"), "PRE_BACKUP_COMMAND"),
        post_backup_command=_command(get("POST_BACKUP_COMMAND"), "POST_BACKUP_COMMAND"),
        command_timeout=_whole(get("COMMAND_TIMEOUT_SECONDS", "60"), "COMMAND_TIMEOUT_SECONDS", 1, 3600),
        status_file=_absolute(status, "STATUS_FILE") if status else None,
    )
