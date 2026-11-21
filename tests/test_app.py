"""Settings, the .env.example sync, doctor, plan, list, prune and the command wiring."""

import re
from pathlib import Path

import pytest

from conftest import at, key_text, recipient
from lombo_vault import core, notify
from lombo_vault.cli import main
from lombo_vault.config import PREFIX, SETTINGS, ConfigError, from_env

ROOT = Path(__file__).resolve().parent.parent


def test_defaults_are_safe(env):
    cfg = from_env(env)
    assert cfg.dry_run is True
    assert cfg.keep == 7
    assert cfg.max_age_days == 10
    assert cfg.key_file is None
    assert cfg.webhook_url is None
    assert cfg.exclude == ()
    assert cfg.age_bin == "age"


def test_every_setting_is_read(env, tmp_path):
    cfg = from_env(
        {
            **env,
            "LOMBO_VAULT_KEEP": "3",
            "LOMBO_VAULT_EXCLUDE": "Cache, *.log",
            "LOMBO_VAULT_KEY_FILE": "/keys/k.txt",
            "LOMBO_VAULT_WEBHOOK_URL": "https://hooks.example.com/x",
            "LOMBO_VAULT_PING_URL": "https://ping.example.com/abc",
            "LOMBO_VAULT_DRILL_PING_URL": "https://ping.example.com/def",
            "LOMBO_VAULT_DRY_RUN": "false",
            "LOMBO_VAULT_RECIPIENTS": f"{recipient('q')}, {recipient('p')}",
        }
    )
    assert (cfg.keep, cfg.exclude, cfg.dry_run) == (3, ("Cache", "*.log"), False)
    assert cfg.key_file == Path("/keys/k.txt")
    assert len(cfg.recipients) == 2


BAD = [
    ("SOURCES", ""),
    ("SOURCES", "relative/path"),
    ("SOURCES", "/a/../etc"),
    ("BACKUP_DIR", ""),
    ("BACKUP_DIR", "backups"),
    ("RECIPIENTS", ""),
    ("RECIPIENTS", "not-a-key"),
    ("RECIPIENTS", "age1short"),
    ("RECIPIENTS", "AGE-SECRET-KEY-1QQQQ"),
    ("KEEP", "0"),
    ("KEEP", "many"),
    ("MAX_AGE_DAYS", "0"),
    ("KEY_FILE", "key.txt"),
    ("SCRATCH_DIR", "tmp"),
    ("WEBHOOK_URL", "ftp://x"),
    ("PING_URL", "not a url"),
    ("DRILL_PING_URL", "file:///etc/passwd"),
    ("DRY_RUN", "treu"),
]


@pytest.mark.parametrize(("name", "value"), BAD)
def test_bad_values_name_the_setting(env, name, value):
    with pytest.raises(ConfigError, match=PREFIX + name):
        from_env({**env, PREFIX + name: value})


def test_a_private_key_in_recipients_is_called_out(env):
    with pytest.raises(ConfigError, match="PRIVATE key"):
        from_env({**env, "LOMBO_VAULT_RECIPIENTS": "AGE-SECRET-KEY-1ABCDEF"})


def test_env_example_matches_config():
    text = (ROOT / ".env.example").read_text()
    listed = set(re.findall(rf"^#?\s*{PREFIX}([A-Z_]+)=", text, re.M))
    assert listed == set(SETTINGS)
    source = (ROOT / "src/lombo_vault/config.py").read_text()
    assert set(re.findall(r'get\("([A-Z_]+)"', source)) == set(SETTINGS)


def test_env_example_is_safe_to_copy():
    text = (ROOT / ".env.example").read_text()
    assert f"{PREFIX}DRY_RUN=true" in text
    assert f"{PREFIX}KEY_FILE=\n" in text  # no key path by default
    assert "AGE-SECRET-KEY" not in text.replace("(AGE-SECRET-KEY-...)", "")


def test_bad_settings_exit_2(capsys):
    assert main(["plan"], {}) == 2
    assert "LOMBO_VAULT_SOURCES" in capsys.readouterr().err


def test_notify_posts_and_pings(hooks):
    notify.send(hooks.url + "/hook", "hello")
    assert hooks.posts()[0]["body"] == {"content": "hello", "text": "hello"}
    notify.ping(hooks.url + "/p")
    notify.ping(hooks.url + "/p/", failed=True)
    assert hooks.pings() == ["/p", "/p/fail"]
    with pytest.raises(notify.NotifyError):
        notify.send("http://127.0.0.1:1/hook", "x", timeout=1)
    with pytest.raises(notify.NotifyError):
        notify.ping("http://127.0.0.1:1/p", timeout=1)


# ------------------------------------------------------------------ doctor
def test_doctor_passes_with_a_key(env, key_file, capsys):
    assert main(["doctor"], {**env, "LOMBO_VAULT_KEY_FILE": str(key_file)}, clock=lambda: at()) == 0
    out = capsys.readouterr().out
    assert "[FAIL]" not in out
    assert "opens a test message" in out
    assert "none yet" in out


def test_doctor_without_a_key_warns_that_the_drill_is_partial(env, capsys):
    assert main(["doctor"], env, clock=lambda: at()) == 0
    assert "PARTIAL" in capsys.readouterr().out


def test_doctor_fails_without_the_age_program(env, capsys):
    assert main(["doctor"], {**env, "LOMBO_VAULT_AGE_BIN": "no-such-age"}, clock=lambda: at()) == 1
    assert "Install age" in capsys.readouterr().out


def test_doctor_reports_refused_recipients(env, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_AGE_FAIL", "1")
    assert main(["doctor"], env, clock=lambda: at()) == 1
    assert "[FAIL] recipients" in capsys.readouterr().out


def test_doctor_catches_a_key_that_does_not_match(env, tmp_path, capsys):
    other = tmp_path / "other.txt"
    other.write_text(key_text("p"))
    other.chmod(0o600)
    assert main(["doctor"], {**env, "LOMBO_VAULT_KEY_FILE": str(other)}, clock=lambda: at()) == 1
    assert "no identity matched" in capsys.readouterr().out


def test_doctor_flags_a_missing_or_exposed_key(env, tmp_path, key_file, capsys):
    assert main(["doctor"], {**env, "LOMBO_VAULT_KEY_FILE": str(tmp_path / "nope.txt")}, clock=lambda: at()) == 1
    assert "missing or not readable" in capsys.readouterr().out
    key_file.chmod(0o644)
    assert main(["doctor"], {**env, "LOMBO_VAULT_KEY_FILE": str(key_file)}, clock=lambda: at()) == 0
    assert "chmod 600" in capsys.readouterr().out


def test_doctor_fails_when_the_key_sits_in_the_backup_folder(env, tmp_path, capsys):
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "key.txt").write_text(key_text("q"))
    cfg = {**env, "LOMBO_VAULT_KEY_FILE": str(backups / "key.txt")}
    assert main(["doctor"], cfg, clock=lambda: at()) == 1
    assert "inside the backup folder" in capsys.readouterr().out


def test_doctor_flags_folder_problems(env, tmp_path, tree, capsys):
    cfg = {
        **env,
        "LOMBO_VAULT_BACKUP_DIR": str(tree["app"] / "backups"),
        "LOMBO_VAULT_SOURCES": f"{tree['app']},/no/such/dir",
    }
    assert main(["doctor"], cfg, clock=lambda: at()) == 1
    out = capsys.readouterr().out
    assert "does not exist" in out
    assert "inside the source" in out


def test_doctor_flags_unwritable_backup_parent_and_unreadable_source(env, tmp_path, tree, capsys):
    if Path("/proc/self").exists() and __import__("os").geteuid() == 0:
        pytest.skip("root ignores permissions")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    secret = tree["app"].parent / "secret"
    secret.write_text("x")
    secret.chmod(0)
    cfg = {**env, "LOMBO_VAULT_BACKUP_DIR": str(locked / "b"), "LOMBO_VAULT_SOURCES": str(secret)}
    try:
        assert main(["doctor"], cfg, clock=lambda: at()) == 1
    finally:
        locked.chmod(0o700)
        secret.chmod(0o600)
    out = capsys.readouterr().out
    assert "not writable" in out
    assert "not readable by this user" in out


def test_doctor_reports_newest_backup_age(env, capsys):
    assert main(["backup"], env, clock=lambda: at(20)) == 0
    capsys.readouterr()
    assert main(["doctor"], env, clock=lambda: at()) == 0
    assert "[warn] newest backup" in capsys.readouterr().out
    assert main(["doctor"], {**env, "LOMBO_VAULT_WEBHOOK_URL": "http://h.example.com"}, clock=lambda: at(20)) == 0
    assert "[ ok ] newest backup" in capsys.readouterr().out


def test_doctor_shows_the_live_mode(env, capsys):
    assert main(["doctor"], {**env, "LOMBO_VAULT_DRY_RUN": "false"}, clock=lambda: at()) == 0
    assert "LIVE" in capsys.readouterr().out


# ------------------------------------------------------------------ plan, list, prune
def test_plan_changes_nothing_and_does_not_create_the_folder(env, tmp_path, capsys, hooks):
    assert main(["plan"], {**env, "LOMBO_VAULT_WEBHOOK_URL": hooks.url + "/h"}, clock=lambda: at()) == 0
    out = capsys.readouterr().out
    assert "3 files" in out
    assert "nothing to delete" in out
    assert not (tmp_path / "backups").exists()
    assert hooks.calls == []


def test_plan_lists_skipped_and_what_retention_would_delete(env, tmp_path, tree, capsys):
    import socket

    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(tree["app"] / "s"))
    for day in range(5, 0, -1):
        assert main(["backup"], env, clock=lambda d=day: at(d)) == 0
    capsys.readouterr()
    for i in range(12):
        (tree["app"] / f"cache{i}.log").write_text("x")
    cfg = {**env, "LOMBO_VAULT_KEEP": "2", "LOMBO_VAULT_EXCLUDE": "*.log"}
    assert main(["plan"], cfg, clock=lambda: at()) == 0
    sock.close()
    out = capsys.readouterr().out
    assert "skipped: " in out
    assert "and 3 more skipped" in out
    assert "would delete 6 file(s)" in out


def test_plan_refuses_a_clashing_folder_and_unreadable_trees(env, tree, capsys):
    assert main(["plan"], {**env, "LOMBO_VAULT_BACKUP_DIR": str(tree["app"] / "b")}, clock=lambda: at()) == 1
    assert "inside the source" in capsys.readouterr().err
    sub = tree["app"] / "locked"
    sub.mkdir()
    sub.chmod(0)
    try:
        if not __import__("os").access(sub, __import__("os").R_OK):
            assert main(["plan"], env, clock=lambda: at()) == 1
            assert "cannot read" in capsys.readouterr().err
    finally:
        sub.chmod(0o700)


def test_list_shows_runs_and_incomplete_ones(env, tmp_path, capsys):
    assert main(["list"], env, clock=lambda: at()) == 0
    assert "No backups" in capsys.readouterr().out
    assert main(["backup"], env, clock=lambda: at(2)) == 0
    stamp = core.stamp_of(at(1))
    (tmp_path / "backups" / core.archive_name(stamp)).write_bytes(b"x" * 10)
    (tmp_path / "backups" / core.manifest_name(core.stamp_of(at(3)))).write_text("{}")
    capsys.readouterr()
    assert main(["list"], env, clock=lambda: at()) == 0
    out = capsys.readouterr().out
    assert out.count("INCOMPLETE") == 2
    assert "complete" in out
    assert out.index(core.stamp_of(at(1))) < out.index(core.stamp_of(at(2)))


def test_prune_command_is_dry_by_default(env, tmp_path, capsys):
    for day in (4, 3, 2, 1):
        assert main(["backup"], env, clock=lambda d=day: at(d)) == 0
    cfg = {**env, "LOMBO_VAULT_KEEP": "2"}
    assert main(["prune"], cfg, clock=lambda: at()) == 0
    assert "would delete 4 file(s)" in capsys.readouterr().out
    assert len(list((tmp_path / "backups").iterdir())) == 8
    assert main(["prune"], {**cfg, "LOMBO_VAULT_DRY_RUN": "false"}, clock=lambda: at()) == 0
    assert "deleted 4 file(s)" in capsys.readouterr().out
    assert len(list((tmp_path / "backups").iterdir())) == 4


def test_prune_reports_a_failed_delete(env, monkeypatch, capsys):
    from lombo_vault import store

    for day in (3, 2, 1):
        assert main(["backup"], env, clock=lambda d=day: at(d)) == 0

    def boom(*a):
        raise PermissionError("read-only")

    monkeypatch.setattr(store, "delete_files", boom)
    assert main(["prune"], {**env, "LOMBO_VAULT_KEEP": "1", "LOMBO_VAULT_DRY_RUN": "false"}, clock=lambda: at()) == 1
    assert "[FAIL] retention" in capsys.readouterr().err
