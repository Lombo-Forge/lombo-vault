"""Round trip with the real `age` program. Skipped unless `age` and `age-keygen` are on PATH."""

import re
import shutil
import subprocess

import pytest

from conftest import at
from lombo_vault import core
from lombo_vault.cli import main

pytestmark = pytest.mark.skipif(
    not (shutil.which("age") and shutil.which("age-keygen")), reason="the real age program is not installed"
)


def make_key(path):
    done = subprocess.run(["age-keygen", "-o", str(path)], capture_output=True, text=True, check=True)  # noqa: S603, S607
    path.chmod(0o600)
    text = done.stderr + done.stdout
    match = re.search(r"age1[a-z0-9]{58}", text)
    assert match
    return match.group(0)


@pytest.fixture
def real(tmp_path, tree):
    pub = make_key(tmp_path / "real-key.txt")
    env = {
        "LOMBO_VAULT_SOURCES": f"{tree['app']},{tree['loose']}",
        "LOMBO_VAULT_BACKUP_DIR": str(tmp_path / "backups"),
        "LOMBO_VAULT_RECIPIENTS": pub,
        "LOMBO_VAULT_SCRATCH_DIR": str(tmp_path / "scratch"),
        "LOMBO_VAULT_KEY_FILE": str(tmp_path / "real-key.txt"),
    }
    return env, tmp_path / "backups", core.stamp_of(at())


def test_doctor_backup_drill_with_the_real_age(real, capsys):
    env, folder, stamp = real
    assert main(["doctor"], env, clock=lambda: at()) == 0
    assert "opens a test message" in capsys.readouterr().out
    assert main(["backup"], env, clock=lambda: at()) == 0
    assert (folder / core.archive_name(stamp)).read_bytes().startswith(core.AGE_HEADER)
    capsys.readouterr()
    assert main(["drill"], env, clock=lambda: at()) == 0
    assert "restore drill: OK" in capsys.readouterr().out


def test_two_recipients_either_key_opens_it(real, tmp_path, capsys):
    env, _folder, _stamp = real
    second = make_key(tmp_path / "second-key.txt")
    env = {**env, "LOMBO_VAULT_RECIPIENTS": f"{env['LOMBO_VAULT_RECIPIENTS']},{second}"}
    assert main(["backup"], env, clock=lambda: at()) == 0
    for key in ("real-key.txt", "second-key.txt"):
        assert main(["drill"], {**env, "LOMBO_VAULT_KEY_FILE": str(tmp_path / key)}, clock=lambda: at()) == 0
    capsys.readouterr()


def test_a_key_for_other_recipients_cannot_open_it(real, tmp_path, capsys):
    env, _folder, _stamp = real
    assert main(["backup"], env, clock=lambda: at()) == 0
    make_key(tmp_path / "stranger.txt")
    capsys.readouterr()
    assert main(["drill"], {**env, "LOMBO_VAULT_KEY_FILE": str(tmp_path / "stranger.txt")}, clock=lambda: at()) == 1
    assert "FAIL" in capsys.readouterr().out


def rewrite(folder, stamp):
    import hashlib

    old = core.parse_manifest((folder / core.manifest_name(stamp)).read_text(), stamp)
    data = (folder / core.archive_name(stamp)).read_bytes()
    text = core.build_manifest(
        stamp, hashlib.sha256(data).hexdigest(), len(data), {n: (h, 1) for n, h in old.files.items()}, ["/x"]
    )
    (folder / core.manifest_name(stamp)).write_text(text)


@pytest.mark.parametrize("damage", ["flip", "truncate"])
def test_damage_that_the_manifest_cannot_see_is_caught_by_age(real, capsys, damage):
    """The manifest is rewritten to match the damaged file, so only age's own authentication can notice."""
    env, folder, stamp = real
    assert main(["backup"], env, clock=lambda: at()) == 0
    archive = folder / core.archive_name(stamp)
    data = bytearray(archive.read_bytes())
    if damage == "flip":
        data[len(data) // 2] ^= 0xFF
    else:
        del data[-200:]
    archive.write_bytes(bytes(data))
    rewrite(folder, stamp)
    capsys.readouterr()
    assert main(["drill"], env, clock=lambda: at()) == 1
    assert "could not be restored" in capsys.readouterr().out
