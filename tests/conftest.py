"""Shared test helpers: a fake `age` program on PATH, sample folders, and a fake webhook/ping server."""

import json
import os
import stat
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from lombo_vault import store

# A tiny reversible stand-in for age. NOT encryption: bytes are XOR-ed so that plaintext is not visible.
# Header and key matching mimic the real thing closely enough to test the tool's behaviour:
#   encrypt:  age -r RECIPIENT [-r ...]       (stdin -> stdout)
#   decrypt:  age -d -i KEYFILE               (key "AGE-SECRET-KEY-Q" opens recipients "age1q...")
#   a file missing its end marker fails AFTER writing what it could decode, like age with a truncated file.
FAKE_AGE = r"""#!{python}
import os, sys
args = sys.argv[1:]
log = os.environ.get("FAKE_AGE_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(" ".join(args) + "\n")
if args == ["--version"]:
    print("fake-age 0.0")
    sys.exit(0)
if os.environ.get("FAKE_AGE_FAIL"):
    sys.stderr.write("age: fake failure\n")
    sys.exit(1)
KEY = 0x5A
END = b"AGE-FAKE-END"
HEAD = b"age-encryption.org/v1\n"
if args and args[0] == "-d":
    keyfile = args[args.index("-i") + 1]
    try:
        text = open(keyfile).read()
    except OSError as e:
        sys.stderr.write("age: error: failed to read identity: %s\n" % e)
        sys.exit(1)
    secrets = [l[len("AGE-SECRET-KEY-"):].lower() for l in text.splitlines() if l.startswith("AGE-SECRET-KEY-")]
    data = sys.stdin.buffer.read()
    if not data.startswith(HEAD):
        sys.stderr.write("age: error: failed to read header: not an age file\n")
        sys.exit(1)
    rest = data[len(HEAD):]
    line, _, body = rest.partition(b"\n")
    recips = line.decode().split(",")
    if not any(r[4:].startswith(s) for r in recips for s in secrets):
        sys.stderr.write("age: error: no identity matched any of the file's recipients\n")
        sys.exit(1)
    plain = bytes(b ^ KEY for b in body)
    if plain.endswith(END):
        sys.stdout.buffer.write(plain[: -len(END)])
        sys.exit(0)
    sys.stdout.buffer.write(plain)
    sys.stderr.write("age: error: unexpected EOF (file truncated)\n")
    sys.exit(1)
recips = [args[i + 1] for i, a in enumerate(args) if a == "-r"]
if not recips or not all(r.startswith("age1") for r in recips):
    sys.stderr.write("age: error: invalid recipient\n")
    sys.exit(1)
data = sys.stdin.buffer.read() + END
sys.stdout.buffer.write(HEAD + ",".join(recips).encode() + b"\n" + bytes(b ^ KEY for b in data))
"""

NOW = datetime(2026, 11, 21, 4, 30, 0, tzinfo=UTC)


def recipient(letter: str = "q") -> str:
    """A well-formed recipient string that the fake age ties to the key from key_text(letter)."""
    return "age1" + (letter * 58)[:58]


def key_text(letter: str = "q") -> str:
    return f"# fake identity\nAGE-SECRET-KEY-{letter.upper()}\n"


@pytest.fixture
def fake_age(tmp_path, monkeypatch):
    """Put the fake `age` first on PATH. Returns the argument log file."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "age"
    script.write_text(FAKE_AGE.replace("{python}", sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "age.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_AGE_LOG", str(log))
    return log


@pytest.fixture
def tree(tmp_path):
    """Sample sources: an app folder with a database and config, plus one loose file."""
    app = tmp_path / "srv" / "appdata"
    (app / "sonarr").mkdir(parents=True)
    (app / "sonarr" / "sonarr.db").write_bytes(b"SQLite format 3\x00" + os.urandom(2000))
    (app / "sonarr" / "config.xml").write_text("<Config>secret-api-key-123</Config>")
    (app / "empty-dir").mkdir()
    loose = tmp_path / "srv" / "compose.yaml"
    loose.write_text("services: {}\n")
    return {"app": app, "loose": loose}


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "keys" / "key.txt"
    path.parent.mkdir()
    path.write_text(key_text("q"))
    path.chmod(0o600)
    return path


class Hooks:
    """A local server that records webhook posts and pings."""

    def __init__(self):
        self.calls: list[dict] = []
        self.status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _handle(self):
                size = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(size)
                outer.calls.append(
                    {"method": self.command, "path": self.path, "body": json.loads(body) if body else None}
                )
                self.send_response(outer.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = _handle

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def posts(self):
        return [c for c in self.calls if c["method"] == "POST"]

    def pings(self):
        return [c["path"] for c in self.calls if c["method"] == "GET"]


@pytest.fixture
def hooks():
    h = Hooks()
    yield h
    h.server.shutdown()


@pytest.fixture
def env(tmp_path, tree, fake_age):
    return {
        "LOMBO_VAULT_SOURCES": f"{tree['app']},{tree['loose']}",
        "LOMBO_VAULT_BACKUP_DIR": str(tmp_path / "backups"),
        "LOMBO_VAULT_RECIPIENTS": recipient("q"),
        "LOMBO_VAULT_SCRATCH_DIR": str(tmp_path / "scratch"),
    }


@pytest.fixture(autouse=True)
def _scratch(tmp_path):
    (tmp_path / "scratch").mkdir()


@pytest.fixture(autouse=True)
def _two_disks(tmp_path, monkeypatch):
    """Everything lives on one real disk in tests, so pretend the backup folder is on its own disk."""
    backups = tmp_path / "backups"
    monkeypatch.setattr(store, "device_of", lambda p: 2 if p.is_relative_to(backups) else 1)


def at(days_ago: float = 0, hour: int = 4) -> datetime:
    from datetime import timedelta

    return NOW.replace(hour=hour) - timedelta(days=days_ago)
