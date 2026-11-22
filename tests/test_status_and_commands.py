"""The status file, and the commands run before and after a backup."""

import json
import stat
import sys

import pytest

from conftest import at
from lombo_vault import status
from lombo_vault.cli import main
from lombo_vault.config import ConfigError, from_env


def script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\nimport sys, pathlib\n{body}\n")
    path.chmod(0o755)
    return str(path)


def doc(path):
    return json.loads(path.read_text())


# ---- settings


def test_new_settings_default_to_off(env):
    cfg = from_env(env)
    assert cfg.pre_backup_command == ()
    assert cfg.post_backup_command == ()
    assert cfg.command_timeout == 60
    assert cfg.status_file is None


def test_commands_are_split_without_a_shell(env):
    cfg = from_env({**env, "LOMBO_VAULT_PRE_BACKUP_COMMAND": "/bin/true 'a b' $HOME"})
    assert cfg.pre_backup_command == ("/bin/true", "a b", "$HOME")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PRE_BACKUP_COMMAND", "/bin/true 'unclosed"),
        ("POST_BACKUP_COMMAND", 'a "b'),
        ("COMMAND_TIMEOUT_SECONDS", "0"),
        ("COMMAND_TIMEOUT_SECONDS", "3601"),
        ("COMMAND_TIMEOUT_SECONDS", "soon"),
        ("STATUS_FILE", "status.json"),
        ("STATUS_FILE", "/a/../b.json"),
    ],
)
def test_bad_values_name_the_setting(env, name, value):
    with pytest.raises(ConfigError, match=name):
        from_env({**env, "LOMBO_VAULT_" + name: value})


def test_status_file_inside_a_source_is_refused(env, tree, capsys):
    bad = {**env, "LOMBO_VAULT_STATUS_FILE": str(tree["app"] / "status.json")}
    assert main(["backup"], bad, clock=lambda: at()) == 1
    assert "status file" in capsys.readouterr().err
    assert main(["doctor"], bad, clock=lambda: at()) == 1


# ---- status file


def test_backup_writes_the_status_file(env, tmp_path):
    target = tmp_path / "mon" / "status.json"
    assert main(["backup"], {**env, "LOMBO_VAULT_STATUS_FILE": str(target)}, clock=lambda: at()) == 0
    d = doc(target)
    assert d["version"] == 1
    assert d["backup"]["result"] == "ok"
    assert d["backup"]["files"] == 3
    assert d["backup"]["time"] == d["backup"]["last_success"] == int(at().timestamp())
    assert d["backup"]["stamp"] == "20261121T043000Z"
    assert d["drill"] is None
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert not (tmp_path / "mon" / "status.json.tmp").exists()


def test_no_status_file_unless_asked(env, tmp_path):
    assert main(["backup"], env, clock=lambda: at()) == 0
    assert not list(tmp_path.glob("**/status.json"))


def test_a_failed_backup_is_recorded_and_keeps_the_last_success(env, tmp_path, monkeypatch):
    target = tmp_path / "status.json"
    e = {**env, "LOMBO_VAULT_STATUS_FILE": str(target)}
    assert main(["backup"], e, clock=lambda: at(1)) == 0
    monkeypatch.setenv("FAKE_AGE_FAIL", "1")
    assert main(["backup"], e, clock=lambda: at()) == 1
    d = doc(target)
    assert d["backup"]["result"] == "fail"
    assert d["backup"]["stamp"] is None
    assert d["backup"]["time"] == int(at().timestamp())
    assert d["backup"]["last_success"] == int(at(1).timestamp())
    assert d["updated"] == int(at().timestamp())


def test_drill_writes_its_half_and_keeps_the_backup_half(env, tmp_path, key_file):
    target = tmp_path / "status.json"
    e = {**env, "LOMBO_VAULT_STATUS_FILE": str(target), "LOMBO_VAULT_KEY_FILE": str(key_file)}
    assert main(["backup"], e, clock=lambda: at()) == 0
    assert main(["drill"], e, clock=lambda: at(hour=5)) == 0
    d = doc(target)
    assert d["backup"]["result"] == "ok"
    assert d["drill"]["result"] == "ok"
    assert d["drill"]["last_success"] == d["drill"]["time"] == int(at(hour=5).timestamp())
    assert "match the checksums" in d["drill"]["message"]


def test_a_partial_drill_is_not_a_success(env, tmp_path):
    target = tmp_path / "status.json"
    e = {**env, "LOMBO_VAULT_STATUS_FILE": str(target)}
    assert main(["backup"], e, clock=lambda: at()) == 0
    assert main(["drill"], e, clock=lambda: at()) == 0
    assert doc(target)["drill"]["result"] == "partial"
    assert doc(target)["drill"]["last_success"] is None


def test_a_failed_drill_is_recorded(env, tmp_path, key_file):
    target = tmp_path / "status.json"
    e = {**env, "LOMBO_VAULT_STATUS_FILE": str(target), "LOMBO_VAULT_KEY_FILE": str(key_file)}
    assert main(["drill"], e, clock=lambda: at()) == 1  # no backup at all
    assert doc(target)["drill"]["result"] == "fail"
    assert doc(target)["backup"] is None


def test_a_damaged_or_foreign_file_is_replaced(env, tmp_path):
    target = tmp_path / "status.json"
    e = {**env, "LOMBO_VAULT_STATUS_FILE": str(target)}
    for junk in ("not json", '{"version": 2, "backup": {"result": "ok"}}', "[]"):
        target.write_text(junk)
        assert main(["backup"], e, clock=lambda: at()) in (0, 1)
        assert doc(target)["version"] == 1
        for leftover in (tmp_path / "backups").glob("*"):
            leftover.unlink()


def test_an_unwritable_status_file_warns_and_changes_nothing(env, tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a folder")
    e = {**env, "LOMBO_VAULT_STATUS_FILE": str(blocker / "status.json")}
    assert main(["backup"], e, clock=lambda: at()) == 0
    assert "could not write the status file" in capsys.readouterr().err
    assert not (tmp_path / "blocker" / "status.json").exists()


def test_messages_are_cleaned():
    assert status.clean("a\nb\x00c") == "a?b?c"
    assert len(status.clean("x" * 1000)) == status.MAX_TEXT


def test_plan_writes_no_status_file(env, tmp_path):
    target = tmp_path / "status.json"
    assert main(["plan"], {**env, "LOMBO_VAULT_STATUS_FILE": str(target)}, clock=lambda: at()) == 0
    assert not target.exists()


def test_dry_run_still_only_controls_deleting(env, tmp_path):
    target = tmp_path / "status.json"
    e = {**env, "LOMBO_VAULT_STATUS_FILE": str(target), "LOMBO_VAULT_DRY_RUN": "true"}
    assert main(["backup"], e, clock=lambda: at()) == 0
    assert doc(target)["backup"]["result"] == "ok"


# ---- commands


def test_pre_runs_before_the_scan_and_post_after(env, tmp_path, tree):
    marker = tmp_path / "order.txt"
    pre = script(
        tmp_path,
        "pre.py",
        f"pathlib.Path({str(tree['app'] / 'dump.sql')!r}).write_text('copy')\n"
        f"pathlib.Path({str(marker)!r}).write_text('pre ')",
    )
    post = script(tmp_path, "post.py", f"p = pathlib.Path({str(marker)!r}); p.write_text(p.read_text() + 'post')")
    e = {**env, "LOMBO_VAULT_PRE_BACKUP_COMMAND": pre, "LOMBO_VAULT_POST_BACKUP_COMMAND": post}
    assert main(["backup"], e, clock=lambda: at()) == 0
    assert marker.read_text() == "pre post"
    manifest = (tmp_path / "backups" / "lombovault_20261121T043000Z.manifest.json").read_text()
    assert "dump.sql" in manifest  # the file the pre command made is in the backup


def test_a_failing_pre_command_means_no_backup_but_post_still_runs(env, tmp_path, capsys, hooks):
    marker = tmp_path / "post.txt"
    pre = script(tmp_path, "pre.py", "sys.exit(3)")
    post = script(tmp_path, "post.py", f"pathlib.Path({str(marker)!r}).write_text('started again')")
    target = tmp_path / "status.json"
    e = {
        **env,
        "LOMBO_VAULT_PRE_BACKUP_COMMAND": pre,
        "LOMBO_VAULT_POST_BACKUP_COMMAND": post,
        "LOMBO_VAULT_STATUS_FILE": str(target),
        "LOMBO_VAULT_WEBHOOK_URL": hooks.url + "/h",
        "LOMBO_VAULT_PING_URL": hooks.url + "/p",
    }
    assert main(["backup"], e, clock=lambda: at()) == 1
    assert marker.read_text() == "started again"
    assert not list((tmp_path / "backups").glob("lombovault_*"))
    assert "PRE_BACKUP_COMMAND failed (exit code 3)" in capsys.readouterr().err
    assert hooks.pings() == ["/p/fail"]
    assert "PRE_BACKUP_COMMAND" in hooks.posts()[0]["body"]["content"]
    assert doc(target)["backup"]["result"] == "fail"


def test_post_runs_after_a_failed_backup_too(env, tmp_path, monkeypatch):
    marker = tmp_path / "post.txt"
    post = script(tmp_path, "post.py", f"pathlib.Path({str(marker)!r}).write_text('x')")
    monkeypatch.setenv("FAKE_AGE_FAIL", "1")
    assert main(["backup"], {**env, "LOMBO_VAULT_POST_BACKUP_COMMAND": post}, clock=lambda: at()) == 1
    assert marker.exists()


def test_a_failing_post_command_alerts_and_exits_1_but_the_backup_stands(env, tmp_path, hooks, capsys):
    post = script(tmp_path, "post.py", "sys.exit(1)")
    target = tmp_path / "status.json"
    e = {
        **env,
        "LOMBO_VAULT_POST_BACKUP_COMMAND": post,
        "LOMBO_VAULT_STATUS_FILE": str(target),
        "LOMBO_VAULT_WEBHOOK_URL": hooks.url + "/h",
        "LOMBO_VAULT_PING_URL": hooks.url + "/p",
    }
    assert main(["backup"], e, clock=lambda: at()) == 1
    assert (tmp_path / "backups" / "lombovault_20261121T043000Z.tar.gz.age").exists()
    assert hooks.pings() == ["/p/fail"]
    assert "POST_BACKUP_COMMAND failed" in hooks.posts()[0]["body"]["content"]
    d = doc(target)["backup"]
    assert d["result"] == "fail"
    assert d["stamp"] == "20261121T043000Z"
    assert d["last_success"] == int(at().timestamp())  # the backup exists, so it counts
    assert "POST_BACKUP_COMMAND" in d["message"]


def test_a_command_that_hangs_is_stopped(env, tmp_path, capsys):
    pre = script(tmp_path, "pre.py", "import time; time.sleep(30)")
    e = {**env, "LOMBO_VAULT_PRE_BACKUP_COMMAND": pre, "LOMBO_VAULT_COMMAND_TIMEOUT_SECONDS": "1"}
    assert main(["backup"], e, clock=lambda: at()) == 1
    assert "timed out after 1 s" in capsys.readouterr().err


def test_a_missing_command_fails_the_backup(env, tmp_path, capsys):
    e = {**env, "LOMBO_VAULT_PRE_BACKUP_COMMAND": str(tmp_path / "nope")}
    assert main(["backup"], e, clock=lambda: at()) == 1
    assert "could not start" in capsys.readouterr().err


def test_there_is_no_shell(env, tmp_path):
    marker = tmp_path / "injected"
    e = {**env, "LOMBO_VAULT_PRE_BACKUP_COMMAND": f"{sys.executable} -c pass ; touch {marker}"}
    assert main(["backup"], e, clock=lambda: at()) == 0  # ';' and touch are only extra arguments to python
    assert not marker.exists()


def test_plan_does_not_run_the_commands(env, tmp_path):
    marker = tmp_path / "ran"
    pre = script(tmp_path, "pre.py", f"pathlib.Path({str(marker)!r}).write_text('x')")
    assert main(["plan"], {**env, "LOMBO_VAULT_PRE_BACKUP_COMMAND": pre}, clock=lambda: at()) == 0
    assert not marker.exists()


def test_a_failed_post_command_does_not_skip_retention(env, tmp_path):
    post = script(tmp_path, "post.py", "sys.exit(1)")
    e = {**env, "LOMBO_VAULT_KEEP": "1", "LOMBO_VAULT_DRY_RUN": "false"}
    assert main(["backup"], e, clock=lambda: at(2)) == 0
    assert main(["backup"], {**e, "LOMBO_VAULT_POST_BACKUP_COMMAND": post}, clock=lambda: at()) == 1
    left = sorted(p.name for p in (tmp_path / "backups").iterdir())
    assert left == ["lombovault_20261121T043000Z.manifest.json", "lombovault_20261121T043000Z.tar.gz.age"]


def test_doctor_checks_the_commands_and_the_status_file(env, tmp_path, capsys):
    ok = {
        **env,
        "LOMBO_VAULT_PRE_BACKUP_COMMAND": f"{sys.executable} -c pass",
        "LOMBO_VAULT_STATUS_FILE": str(tmp_path / "s.json"),
    }
    main(["doctor"], ok, clock=lambda: at())
    out = capsys.readouterr().out
    assert "pre-backup command" in out
    assert "status file" in out
    assert "FAIL" not in out
    bad = {**env, "LOMBO_VAULT_POST_BACKUP_COMMAND": "/no/such/program", "LOMBO_VAULT_STATUS_FILE": "/proc/x/s.json"}
    assert main(["doctor"], bad, clock=lambda: at()) == 1
    out = capsys.readouterr().out
    assert "post-backup command: /no/such/program is not found" in out
    assert "cannot be written" in out
