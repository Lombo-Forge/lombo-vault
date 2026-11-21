"""The thin layer over the age program, tested with the fake age."""

import pytest

from conftest import at, key_text, recipient
from lombo_vault import agebin
from lombo_vault.cli import main


def encrypt(tmp_path, data: bytes):
    path = tmp_path / "enc"
    with path.open("wb") as out, agebin.encryptor("age", (recipient("q"),), out) as sink:
        sink.write(data)
    return path


@pytest.fixture
def key(tmp_path):
    path = tmp_path / "key.txt"
    path.write_text(key_text("q"))
    return path


def test_encrypt_then_decrypt_returns_the_bytes(fake_age, tmp_path, key):
    path = encrypt(tmp_path, b"hello world")
    with path.open("rb") as src, agebin.decryptor("age", key, src) as plain:
        assert plain.read() == b"hello world"


def test_leaving_the_decryptor_early_still_drains_and_checks(fake_age, tmp_path, key):
    path = encrypt(tmp_path, b"x" * 100)
    with path.open("rb") as src, agebin.decryptor("age", key, src) as plain:
        assert plain.read(2) == b"xx"


def test_a_truncated_file_fails_when_the_block_ends(fake_age, tmp_path, key):
    path = tmp_path / "cut"
    path.write_bytes(b"age-encryption.org/v1\n" + recipient("q").encode() + b"\n" + bytes(b ^ 0x5A for b in b"hello"))
    with (
        pytest.raises(agebin.AgeError, match="truncated"),
        path.open("rb") as src,
        agebin.decryptor("age", key, src) as plain,
    ):
        assert plain.read(5) == b"hello"


def test_the_encryptor_passes_other_errors_through(fake_age, tmp_path):
    with (
        pytest.raises(ValueError, match="boom"),
        (tmp_path / "o").open("wb") as out,
        agebin.encryptor("age", (recipient(),), out),
    ):
        raise ValueError("boom")


def test_version_reports_a_broken_program(tmp_path, monkeypatch):
    script = tmp_path / "age"
    script.write_text("#!/bin/sh\necho broken >&2\nexit 3\n")
    script.chmod(0o755)
    with pytest.raises(agebin.AgeError, match="broken"):
        agebin.version(str(script))


def test_round_trip_errors(fake_age, tmp_path, key, monkeypatch):
    agebin.round_trip("age", (recipient("q"),), key)
    with pytest.raises(agebin.AgeError, match="does not open"):
        agebin.round_trip("age", (recipient("p"),), key)
    with pytest.raises(agebin.AgeError, match="cannot run"):
        agebin.round_trip("no-such-age", (recipient("q"),), key)
    monkeypatch.setenv("FAKE_AGE_FAIL", "1")
    with pytest.raises(agebin.AgeError, match="refused the recipients"):
        agebin.round_trip("age", (recipient("q"),), key)


def test_the_real_clock_is_used_when_none_is_passed(env, capsys):
    assert main(["list"], env) == 0
    assert "No backups" in capsys.readouterr().out


def test_dead_webhook_and_ping_only_warn(env, hooks, capsys):
    cfg = {
        **env,
        "LOMBO_VAULT_WEBHOOK_URL": "http://127.0.0.1:1/h",
        "LOMBO_VAULT_DRILL_PING_URL": "http://127.0.0.1:1/p",
    }
    assert main(["drill"], cfg, clock=lambda: at()) == 1  # no backup yet: the drill fails, and says so on stdout
    err = capsys.readouterr().err
    assert "[warn] webhook failed" in err
    assert "[warn] ping failed" in err
