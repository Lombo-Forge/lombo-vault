"""Regression tests for the findings of the third audit (AUDIT-vault.md). Each one failed on the old code."""

import gzip
import hashlib
import io
import tarfile

import pytest

from conftest import at, recipient
from lombo_vault import core, notify, store
from lombo_vault.cli import main
from lombo_vault.config import ConfigError, from_env

HEAD = b"age-encryption.org/v1\n"
# 20261121 and 043000 written with Arabic-Indic digits (U+0660..U+0669), which `\d` used to accept.
ARABIC_DATE = "".join(chr(0x660 + int(c)) for c in "20261121")
ARABIC_TIME = "".join(chr(0x660 + int(c)) for c in "043000")


def names(folder):
    return sorted(p.name for p in folder.iterdir())


# -- 1. an empty or symlinked source must never count as a backup -------------------------------------------


def test_a_source_that_is_a_symlink_is_followed(env, tmp_path, capsys):
    real = tmp_path / "mnt" / "app"
    real.mkdir(parents=True)
    (real / "data.db").write_text("data")
    link = tmp_path / "config"
    link.symlink_to(real)
    env = {**env, "LOMBO_VAULT_SOURCES": str(link)}
    assert main(["backup"], env, clock=lambda: at()) == 0
    assert "1 files" in capsys.readouterr().out


def test_a_backup_of_zero_files_is_refused_and_retention_does_not_run(env, tmp_path, capsys):
    live = {**env, "LOMBO_VAULT_DRY_RUN": "false", "LOMBO_VAULT_KEEP": "1"}
    assert main(["backup"], live, clock=lambda: at(2)) == 0
    empty = tmp_path / "empty"
    empty.mkdir()
    bad = {**live, "LOMBO_VAULT_SOURCES": str(empty)}
    capsys.readouterr()
    assert main(["backup"], bad, clock=lambda: at(1)) == 1
    assert "hold no files" in capsys.readouterr().err
    assert core.archive_name(core.stamp_of(at(2))) in names(tmp_path / "backups")
    assert len(names(tmp_path / "backups")) == 2


def test_a_folder_above_the_source_does_not_trigger_an_exclude(env, tmp_path, capsys):
    src = tmp_path / "cache" / "app"
    src.mkdir(parents=True)
    (src / "a.txt").write_text("a")
    (src / "cache").mkdir()
    (src / "cache" / "skip.txt").write_text("skip")
    env = {**env, "LOMBO_VAULT_SOURCES": str(src), "LOMBO_VAULT_EXCLUDE": "cache"}
    assert main(["backup"], env, clock=lambda: at()) == 0
    assert "1 files" in capsys.readouterr().out  # a.txt kept; the cache folder BELOW the source is still skipped


def test_is_excluded_matches_only_below_the_source():
    from pathlib import PurePath

    assert not core.is_excluded("/x/cache/app/a", ("cache",), PurePath("/x/cache/app"))
    assert core.is_excluded("/x/cache/app/cache/a", ("cache",), PurePath("/x/cache/app"))


# -- 2. a wrong clock -------------------------------------------------------------------------------------


def test_future_stamps_are_never_counted_by_retention():
    now = at().timestamp()
    files = {}
    for stamp in ("20271121T040000Z", "20271122T040000Z", "20261101T040000Z", "20261102T040000Z"):
        files[core.archive_name(stamp)] = now
        files[core.manifest_name(stamp)] = now
    doomed = core.plan_prune(files, 2, now)
    # only the two real runs count, so nothing is old enough to go; the new backup is not its own victim
    assert doomed == []


def test_a_backup_made_with_a_correct_clock_survives_after_two_future_ones(env, tmp_path):
    live = {**env, "LOMBO_VAULT_DRY_RUN": "false", "LOMBO_VAULT_KEEP": "2"}
    from datetime import timedelta

    for days in (400, 401):
        assert main(["backup"], live, clock=lambda d=days: at() + timedelta(days=d)) == 0
    assert main(["backup"], live, clock=lambda: at()) == 0
    assert core.archive_name(core.stamp_of(at())) in names(tmp_path / "backups")


def test_the_drill_fails_when_the_newest_backup_is_from_the_future(env, key_file, tmp_path, capsys):
    from datetime import timedelta

    assert main(["backup"], env, clock=lambda: at() + timedelta(days=345)) == 0
    capsys.readouterr()
    cfg = {**env, "LOMBO_VAULT_KEY_FILE": str(key_file)}
    assert main(["drill"], cfg, clock=lambda: at()) == 1
    assert "in the future" in capsys.readouterr().out


# -- 3. names the tool did not make -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        f"lombovault_{ARABIC_DATE}T{ARABIC_TIME}Z.tar.gz.age",
        "lombovault_99999999T999999Z.tar.gz.age",
        "lombovault_20261340T040000Z.manifest.json",
        ".lombovault_99999999T999999Z.tar.gz.age.tmp",
    ],
)
def test_junk_stamps_are_not_the_tools_files(name):
    assert core.parse_name(name) is None
    assert not core.is_own_file(name)


def test_a_junk_pair_cannot_push_real_backups_out():
    files = {"lombovault_99999999T999999Z.tar.gz.age": 0.0, "lombovault_99999999T999999Z.manifest.json": 0.0}
    for stamp in ("20261101T040000Z", "20261102T040000Z"):
        files[core.archive_name(stamp)] = 0.0
        files[core.manifest_name(stamp)] = 0.0
    assert core.plan_prune(files, 2, at().timestamp()) == []


# -- 4. alert and ping addresses ----------------------------------------------------------------------------


def test_an_address_with_a_space_is_refused_at_start_up():
    base = {
        "LOMBO_VAULT_SOURCES": "/a",
        "LOMBO_VAULT_BACKUP_DIR": "/b",
        "LOMBO_VAULT_RECIPIENTS": recipient(),
        "LOMBO_VAULT_PING_URL": "http://127.0.0.1/SECRET-TOKEN x",
    }
    with pytest.raises(ConfigError, match="PING_URL"):
        from_env(base)


@pytest.mark.parametrize("call", [lambda u: notify.send(u, "hi"), lambda u: notify.ping(u, True)])
def test_a_bad_address_is_a_warning_that_never_shows_the_secret(call):
    with pytest.raises(notify.NotifyError) as err:
        call("http://127.0.0.1/SECRET-TOKEN x")
    assert "SECRET" not in str(err.value)


def test_a_bad_address_does_not_stop_retention(env, tmp_path, capsys):
    live = {**env, "LOMBO_VAULT_DRY_RUN": "false", "LOMBO_VAULT_KEEP": "1"}
    assert main(["backup"], live, clock=lambda: at(2)) == 0
    # bypass config validation the way an odd-but-accepted address would: patch the settings after loading
    import lombo_vault.cli as cli

    real = cli.notify.ping

    def boom(url, failed=False, timeout=10):
        raise cli.notify.NotifyError("ping failed (InvalidURL)")

    cli.notify.ping = boom
    try:
        assert main(["backup"], {**live, "LOMBO_VAULT_PING_URL": "http://127.0.0.1:9/p"}, clock=lambda: at(1)) == 0
    finally:
        cli.notify.ping = real
    assert len(names(tmp_path / "backups")) == 2  # the old run was deleted: keep=1


def test_fail_goes_on_the_path_not_after_the_query(hooks):
    notify.ping(hooks.url + "/p?x=1", True)
    assert hooks.pings() == ["/p/fail?x=1"]


# -- 5. the drill ---------------------------------------------------------------------------------------------


def forge(folder, stamp, add_members, claimed):
    """Replace the archive with one holding `add_members`, with a manifest that claims `claimed` bytes."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        add_members(tar)
    payload = bytes(b ^ 0x5A for b in gzip.compress(buf.getvalue()) + b"AGE-FAKE-END")
    data = HEAD + recipient("q").encode() + b"\n" + payload
    (folder / core.archive_name(stamp)).write_bytes(data)
    listed = {"x/small.txt": (hashlib.sha256(b"a").hexdigest(), claimed)}
    text = core.build_manifest(stamp, hashlib.sha256(data).hexdigest(), len(data), listed, ["/x"])
    (folder / core.manifest_name(stamp)).write_text(text)


@pytest.fixture
def made(env, key_file, tmp_path):
    assert main(["backup"], env, clock=lambda: at(1)) == 0
    return {**env, "LOMBO_VAULT_KEY_FILE": str(key_file)}, tmp_path / "backups", core.stamp_of(at(1))


def test_the_drill_stops_when_the_archive_holds_more_than_the_manifest_says(made, tmp_path, capsys):
    cfg, folder, stamp = made

    def members(tar):
        info = tarfile.TarInfo("x/bomb.bin")
        info.size = 3 * 2**20
        tar.addfile(info, io.BytesIO(b"\0" * info.size))

    forge(folder, stamp, members, claimed=2)
    assert main(["drill"], cfg, clock=lambda: at()) == 1
    out = capsys.readouterr().out
    assert "more data than its manifest" in out
    assert list((tmp_path / "scratch").iterdir()) == []


def test_the_drill_fails_on_a_fifo_member(made, capsys):
    cfg, folder, stamp = made

    def members(tar):
        info = tarfile.TarInfo("x/pipe")
        info.type = tarfile.FIFOTYPE
        tar.addfile(info)
        small = tarfile.TarInfo("x/small.txt")
        small.size = 1
        tar.addfile(small, io.BytesIO(b"a"))

    forge(folder, stamp, members, claimed=1)
    assert main(["drill"], cfg, clock=lambda: at()) == 1
    assert "not a file, folder or link" in capsys.readouterr().out


def test_partial_pings_the_failure_address(made, hooks, capsys):
    cfg, _folder, _stamp = made
    cfg = {k: v for k, v in cfg.items() if k != "LOMBO_VAULT_KEY_FILE"}
    cfg["LOMBO_VAULT_DRILL_PING_URL"] = hooks.url + "/d"
    assert main(["drill"], cfg, clock=lambda: at()) == 0
    assert "PARTIAL" in capsys.readouterr().out
    assert hooks.pings() == ["/d/fail"]


# -- 6. layout, concurrency, typos ------------------------------------------------------------------------------


def test_a_key_file_or_scratch_folder_inside_a_source_is_a_problem(env, tmp_path, tree):
    key = tree["app"] / "key.txt"
    key.write_text("x")
    cfg = from_env({**env, "LOMBO_VAULT_KEY_FILE": str(key), "LOMBO_VAULT_SCRATCH_DIR": str(tree["app"] / "empty-dir")})
    problems = store.check_layout(cfg)
    assert any("key file" in p for p in problems)
    assert any("scratch folder" in p for p in problems)


def test_a_second_run_in_the_same_second_leaves_the_first_runs_temp_file(env, tmp_path, capsys):
    folder = tmp_path / "backups"
    folder.mkdir()
    temp = folder / core.temp_name(core.archive_name(core.stamp_of(at())))
    temp.write_bytes(b"first run is writing")
    assert main(["backup"], env, clock=lambda: at()) == 1
    assert temp.read_bytes() == b"first run is writing"


def test_a_misspelled_setting_is_reported(env, capsys):
    assert main(["list"], {**env, "LOMBO_VAULT_EXCLUDES": "x"}, clock=lambda: at()) == 0
    assert "LOMBO_VAULT_EXCLUDES" in capsys.readouterr().err
