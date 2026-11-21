"""The only place that talks to the `age` program (https://age-encryption.org). No crypto is written here:
this module starts the binary, feeds it bytes and checks that it exited cleanly.

Encrypting needs only public keys (recipients). Decrypting needs the private key FILE, and only the
drill and doctor ever call it.
"""

import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO


class AgeError(Exception):
    """The age program is missing, refused, or failed."""


def _message(err: IO[bytes]) -> str:
    err.seek(0)
    text = err.read(2000).decode(errors="replace").strip()
    return " ".join(text.split()) or "no message"


def _start(cmd: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(cmd, **kwargs)  # type: ignore[call-overload,no-any-return]  # noqa: S603  # fixed argv, no shell
    except OSError as e:
        raise AgeError(f"cannot start {cmd[0]!r}: {e.strerror or e}. Install age (https://age-encryption.org).") from e


def version(age_bin: str) -> str:
    """The version text of the age program; raises AgeError when it is missing or broken."""
    try:
        done = subprocess.run([age_bin, "--version"], capture_output=True, timeout=20, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AgeError(f"cannot run {age_bin!r}: {e}. Install age (https://age-encryption.org).") from e
    if done.returncode != 0:
        raise AgeError(f"{age_bin} --version failed: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout.decode(errors="replace").strip() or "unknown version"


@contextmanager
def encryptor(age_bin: str, recipients: tuple[str, ...], out: IO[bytes]) -> Iterator[IO[bytes]]:
    """Yields a writable stream; whatever is written comes out of age, encrypted, into `out`."""
    cmd = [age_bin]
    for r in recipients:
        cmd += ["-r", r]
    with tempfile.TemporaryFile() as err:
        proc = _start(cmd, stdin=subprocess.PIPE, stdout=out, stderr=err)
        stdin = proc.stdin
        assert stdin is not None  # noqa: S101  # PIPE was requested
        try:
            yield stdin
            stdin.close()
        except BaseException as e:
            proc.kill()
            proc.wait()
            if isinstance(e, OSError) and proc.returncode not in (0, None):
                raise AgeError(f"age stopped while writing: {_message(err)}") from e
            raise
        if proc.wait() != 0:
            raise AgeError(f"age refused to encrypt: {_message(err)}")


@contextmanager
def decryptor(age_bin: str, key_file: Path, source: IO[bytes]) -> Iterator[IO[bytes]]:
    """Yields a readable stream of the decrypted bytes of `source`. Leaving the block waits for age and
    raises AgeError unless it checked the whole file and succeeded (a truncated or altered file fails here)."""
    with tempfile.TemporaryFile() as err:
        proc = _start([age_bin, "-d", "-i", str(key_file)], stdin=source, stdout=subprocess.PIPE, stderr=err)
        stdout = proc.stdout
        assert stdout is not None  # noqa: S101  # PIPE was requested
        try:
            yield stdout
            while stdout.read(1 << 20):  # age authenticates to the very end: read it all
                pass
        except BaseException as e:
            proc.kill()
            proc.wait()
            said = _message(err)
            if not isinstance(e, AgeError) and said != "no message":
                raise AgeError(f"age could not decrypt: {said}") from e  # age's own words beat a tar error
            raise
        finally:
            stdout.close()
        if proc.wait() != 0:
            raise AgeError(f"age could not decrypt: {_message(err)}")


def round_trip(age_bin: str, recipients: tuple[str, ...], key_file: Path) -> None:
    """Encrypt a few bytes to the recipients and decrypt them with the key file: proves the recipients are
    accepted and that the key matches at least one of them. Raises AgeError otherwise."""
    secret = b"lombo-vault doctor"
    cmd = [age_bin]
    for r in recipients:
        cmd += ["-r", r]
    try:
        enc = subprocess.run(cmd, input=secret, capture_output=True, timeout=30, check=False)  # noqa: S603
        if enc.returncode != 0:
            raise AgeError(f"age refused the recipients: {enc.stderr.decode(errors='replace').strip()}")
        dec = subprocess.run(  # noqa: S603
            [age_bin, "-d", "-i", str(key_file)], input=enc.stdout, capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AgeError(f"cannot run {age_bin!r}: {e}") from e
    if dec.returncode != 0 or dec.stdout != secret:
        said = dec.stderr.decode(errors="replace").strip()
        raise AgeError(f"the key file does not open a backup made for these recipients: {said}")
