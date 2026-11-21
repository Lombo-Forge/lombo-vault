"""The same-disk refusal, with injected device ids (no second real disk needed)."""

import pytest

from conftest import at
from lombo_vault import store
from lombo_vault.cli import main
from lombo_vault.config import ConfigError, from_env

REAL_DEVICE_OF = store.device_of  # the autouse fixture patches the module attribute per test


def one_disk(monkeypatch):
    monkeypatch.setattr(store, "device_of", lambda p: 7)


def test_backup_refuses_the_same_disk_and_writes_nothing(env, tmp_path, monkeypatch, capsys):
    one_disk(monkeypatch)
    assert main(["backup"], env, clock=lambda: at()) == 1
    err = capsys.readouterr().err
    assert "same disk" in err
    assert "lost together with the data" in err
    assert not (tmp_path / "backups").exists() or not list((tmp_path / "backups").iterdir())


def test_doctor_shows_fail_and_plan_refuses(env, monkeypatch, capsys):
    one_disk(monkeypatch)
    assert main(["doctor"], env, clock=lambda: at()) == 1
    assert "[FAIL] folders" in capsys.readouterr().out
    assert main(["plan"], env, clock=lambda: at()) == 1
    assert "same disk" in capsys.readouterr().err


def test_the_override_allows_it(env, monkeypatch):
    one_disk(monkeypatch)
    allowed = {**env, "LOMBO_VAULT_ALLOW_SAME_DISK": "true"}
    assert main(["backup"], allowed, clock=lambda: at()) == 0
    assert main(["doctor"], allowed, clock=lambda: at()) == 0


def test_different_disks_pass(env):
    assert main(["backup"], env, clock=lambda: at()) == 0  # the conftest gives the backup folder its own id


def test_one_matching_source_is_enough(env, tree, monkeypatch):
    # the first source is on its own disk, the loose file shares the backup folder's disk
    loose = str(tree["loose"].resolve())
    monkeypatch.setattr(store, "device_of", lambda p: 2 if str(p) == loose or "backups" in str(p) else 1)
    assert "same disk" in store.check_layout(from_env(env))[0]
    assert str(tree["loose"]) in store.check_layout(from_env(env))[0]


def test_a_backup_folder_that_does_not_exist_yet_uses_its_parent(tmp_path):
    assert REAL_DEVICE_OF(tmp_path / "not" / "there") == tmp_path.stat().st_dev


def test_unreadable_device_errs_on_the_side_of_warning(env, monkeypatch):
    def boom(p):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(store, "device_of", boom)
    assert "cannot compare the disks" in store.check_layout(from_env(env))[0]


def test_setting_is_strict_and_defaults_to_refusing(env):
    assert from_env(env).allow_same_disk is False
    with pytest.raises(ConfigError, match="LOMBO_VAULT_ALLOW_SAME_DISK must be true or false"):
        from_env({**env, "LOMBO_VAULT_ALLOW_SAME_DISK": "treu"})
