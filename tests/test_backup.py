"""Making backups: what is written, what is never deleted, and every way a backup must fail safe."""

import hashlib
import json
import os
import stat
import tarfile

import pytest

from conftest import at
from lombo_vault import agebin, core, store
from lombo_vault.cli import main
from lombo_vault.config import from_env


def names(folder):
    return sorted(p.name for p in folder.iterdir())


def test_a_backup_writes_an_archive_and_a_manifest(env, tmp_path, tree, capsys):
    assert main(["backup"], env, clock=lambda: at()) == 0
    folder = tmp_path / "backups"
    stamp = core.stamp_of(at())
    assert names(folder) == sorted([core.archive_name(stamp), core.manifest_name(stamp)])
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    archive = folder / core.archive_name(stamp)
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    raw = archive.read_bytes()
    assert raw.startswith(core.AGE_HEADER)
    assert b"secret-api-key-123" not in raw
    assert b"SQLite format 3" not in raw
    manifest = core.parse_manifest((folder / core.manifest_name(stamp)).read_text(), stamp)
    assert manifest.archive_sha256 == hashlib.sha256(raw).hexdigest()
    assert manifest.archive_size == len(raw)
    config = tree["app"] / "sonarr" / "config.xml"
    assert manifest.files[core.member_name(str(config))] == hashlib.sha256(config.read_bytes()).hexdigest()
    assert len(manifest.files) == 3
    assert "3 files" in capsys.readouterr().out


def test_backup_never_reads_the_private_key(env, key_file, fake_age):
    assert main(["backup"], {**env, "LOMBO_VAULT_KEY_FILE": str(key_file)}, clock=lambda: at()) == 0
    log = fake_age.read_text()
    assert "-r age1" in log
    assert "-i" not in log.split()
    assert "-d" not in log.split()


def test_archive_content_round_trips(env, tmp_path, tree):
    """Decrypt by hand with the fake (its transform is a XOR) and check what is inside the tar."""
    assert main(["backup"], env, clock=lambda: at()) == 0
    archive = tmp_path / "backups" / core.archive_name(core.stamp_of(at()))
    body = archive.read_bytes().split(b"\n", 2)[2]
    plain = bytes(b ^ 0x5A for b in body)[: -len(b"AGE-FAKE-END")]
    scratch = tmp_path / "plain.tar.gz"
    scratch.write_bytes(plain)
    with tarfile.open(scratch) as tar:
        member_names = tar.getnames()
    assert core.member_name(str(tree["app"] / "empty-dir")) in member_names
    assert core.member_name(str(tree["loose"])) in member_names


def test_symlinks_are_stored_not_followed_and_special_files_are_skipped(env, tmp_path, tree, capsys):
    outside = tmp_path / "outside.txt"
    outside.write_text("do not follow me")
    (tree["app"] / "link").symlink_to(outside)
    (tree["app"] / "dirlink").symlink_to(tmp_path / "srv")
    os.mkfifo(tree["app"] / "pipe")
    assert main(["backup"], env, clock=lambda: at()) == 0
    manifest = core.parse_manifest(
        (tmp_path / "backups" / core.manifest_name(core.stamp_of(at()))).read_text(), core.stamp_of(at())
    )
    assert len(manifest.files) == 3  # neither the link target nor the fifo is a file in the backup
    found = store.scan(from_env(env))
    assert any("pipe (not a regular file)" in s for s in found.skipped)
    assert {e.kind for e in found.entries} == {"dir", "file", "link"}


def test_exclude_patterns_leave_things_out(env, tree):
    (tree["app"] / "Cache").mkdir()
    (tree["app"] / "Cache" / "big.bin").write_bytes(b"x" * 100)
    (tree["app"] / "server.log").write_text("log")
    found = store.scan(from_env({**env, "LOMBO_VAULT_EXCLUDE": "Cache,*.log"}))
    assert found.files == 3
    assert sorted(s.split(" ")[0].rsplit("/", 1)[-1] for s in found.skipped) == ["Cache", "server.log"]


def test_a_failed_backup_leaves_nothing_and_keeps_older_ones(env, tmp_path, monkeypatch, capsys, hooks):
    cfg = {
        **env,
        "LOMBO_VAULT_KEEP": "1",
        "LOMBO_VAULT_DRY_RUN": "false",
        "LOMBO_VAULT_WEBHOOK_URL": hooks.url + "/h",
        "LOMBO_VAULT_PING_URL": hooks.url + "/ping",
    }
    assert main(["backup"], cfg, clock=lambda: at(2)) == 0
    before = names(tmp_path / "backups")
    monkeypatch.setenv("FAKE_AGE_FAIL", "1")
    assert main(["backup"], cfg, clock=lambda: at(1)) == 1
    assert names(tmp_path / "backups") == before
    err = capsys.readouterr().err
    assert "fake failure" in err
    assert "Older backups were not touched" in err
    assert "BACKUP FAILED" in hooks.posts()[0]["body"]["content"]
    assert hooks.pings() == ["/ping", "/ping/fail"]


def test_retention_runs_after_success_only_when_live(env, tmp_path, capsys):
    for day in (4, 3, 2):
        assert main(["backup"], env, clock=lambda d=day: at(d)) == 0
    live = {**env, "LOMBO_VAULT_KEEP": "2", "LOMBO_VAULT_DRY_RUN": "false"}
    assert main(["backup"], {**live, "LOMBO_VAULT_DRY_RUN": "true"}, clock=lambda: at(1)) == 0
    assert "would delete 4 file(s)" in capsys.readouterr().out
    assert len(names(tmp_path / "backups")) == 8
    assert main(["backup"], live, clock=lambda: at(0)) == 0
    assert "deleted 6 file(s)" in capsys.readouterr().out
    left = {core.parse_name(n)[0] for n in names(tmp_path / "backups")}
    assert left == {core.stamp_of(at(1)), core.stamp_of(at(0))}


def test_retention_never_touches_foreign_files(env, tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    foreign = ["notes.txt", "lombovault_old.tar.gz.age", "photo.jpg", ".lombovault.lock"]
    for n in foreign:
        (folder / n).write_text("mine")
    (folder / "subdir").mkdir()
    (folder / "subdir" / core.archive_name("20200101T000000Z")).write_text("nested")
    cfg = {**env, "LOMBO_VAULT_KEEP": "1", "LOMBO_VAULT_DRY_RUN": "false"}
    for day in (3, 2, 1):
        assert main(["backup"], cfg, clock=lambda d=day: at(d)) == 0
    assert set(foreign) <= set(names(folder))
    assert (folder / "subdir" / core.archive_name("20200101T000000Z")).exists()


def test_delete_files_refuses_anything_that_is_not_ours(tmp_path):
    (tmp_path / "precious.txt").write_text("x")
    with pytest.raises(store.BackupError, match="not a file this tool made"):
        store.delete_files(tmp_path, ["precious.txt"])
    with pytest.raises(store.BackupError):
        store.delete_files(tmp_path, ["../" + core.archive_name("20200101T000000Z")])
    assert (tmp_path / "precious.txt").exists()


def test_stale_temp_files_are_cleaned_by_retention(env, tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    old = folder / core.temp_name(core.archive_name("20200101T000000Z"))
    old.write_text("half")
    os.utime(old, (1, 1))
    fresh = folder / core.temp_name(core.manifest_name("20200101T000000Z"))
    fresh.write_text("half")
    assert main(["backup"], {**env, "LOMBO_VAULT_DRY_RUN": "false"}, clock=lambda: at()) == 0
    assert not old.exists()
    assert fresh.exists()


def test_backup_folder_inside_a_source_is_refused(env, tree, capsys):
    cfg = {**env, "LOMBO_VAULT_BACKUP_DIR": str(tree["app"] / "sonarr" / "backups")}
    assert main(["backup"], cfg, clock=lambda: at()) == 1
    assert "inside the source" in capsys.readouterr().err
    assert not (tree["app"] / "sonarr" / "backups").exists()


def test_a_source_inside_the_backup_folder_is_refused(env, tmp_path, capsys):
    (tmp_path / "backups" / "stuff").mkdir(parents=True)
    cfg = {**env, "LOMBO_VAULT_SOURCES": str(tmp_path / "backups" / "stuff")}
    assert main(["backup"], cfg, clock=lambda: at()) == 1
    assert "inside the backup folder" in capsys.readouterr().err


def test_a_symlinked_backup_folder_cannot_hide_the_overlap(env, tmp_path, tree, capsys):
    link = tmp_path / "innocent"
    link.symlink_to(tree["app"])
    assert main(["backup"], {**env, "LOMBO_VAULT_BACKUP_DIR": str(link)}, clock=lambda: at()) == 1
    assert "inside the source" in capsys.readouterr().err


def test_a_missing_source_fails_before_anything_is_written(env, tmp_path, capsys):
    assert main(["backup"], {**env, "LOMBO_VAULT_SOURCES": "/no/such/place"}, clock=lambda: at()) == 1
    assert "does not exist" in capsys.readouterr().err
    assert not (tmp_path / "backups").exists()


def test_an_unreadable_file_fails_the_backup_without_leftovers(env, tmp_path, tree, capsys):
    if os.geteuid() == 0:
        pytest.skip("root ignores permissions")
    hidden = tree["app"] / "sonarr" / "private.db"
    hidden.write_text("x")
    hidden.chmod(0)
    try:
        assert main(["backup"], env, clock=lambda: at()) == 1
    finally:
        hidden.chmod(0o600)
    assert names(tmp_path / "backups") == []
    assert "[FAIL] backup" in capsys.readouterr().err


def test_a_file_that_vanishes_during_the_backup_fails_safely(env, tmp_path, tree, monkeypatch):
    real = store._add_entry
    victim = tree["app"] / "sonarr" / "config.xml"

    def sabotage(tar, entry, hashes):
        if entry.path == str(victim):
            victim.unlink()
        real(tar, entry, hashes)

    monkeypatch.setattr(store, "_add_entry", sabotage)
    assert main(["backup"], env, clock=lambda: at()) == 1
    assert names(tmp_path / "backups") == []


def test_a_second_backup_in_the_same_second_is_refused(env, tmp_path, capsys):
    assert main(["backup"], env, clock=lambda: at()) == 0
    before = names(tmp_path / "backups")
    assert main(["backup"], env, clock=lambda: at()) == 1
    assert "already exists" in capsys.readouterr().err
    assert names(tmp_path / "backups") == before


def test_an_age_that_writes_no_header_is_caught(env, tmp_path, fake_age, capsys):
    script = tmp_path / "bin" / "age"
    script.write_text("#!/bin/sh\ncat >/dev/null\necho not-encrypted\n")
    assert main(["backup"], env, clock=lambda: at()) == 1
    assert "age header" in capsys.readouterr().err
    assert names(tmp_path / "backups") == []


def test_age_dying_early_gives_its_own_message(env, tmp_path, fake_age, capsys):
    script = tmp_path / "bin" / "age"
    script.write_text("#!/bin/sh\necho 'age: error: bad recipient' >&2\nexit 1\n")
    (tmp_path / "srv" / "appdata" / "big.bin").write_bytes(b"x" * 3_000_000)
    assert main(["backup"], env, clock=lambda: at()) == 1
    assert "bad recipient" in capsys.readouterr().err


def test_a_missing_age_program_is_explained(env, capsys):
    assert main(["backup"], {**env, "LOMBO_VAULT_AGE_BIN": "no-such-age"}, clock=lambda: at()) == 1
    assert "Install age" in capsys.readouterr().err


def test_a_dead_webhook_does_not_fail_a_good_backup(env, tmp_path, capsys):
    cfg = {**env, "LOMBO_VAULT_PING_URL": "http://127.0.0.1:1/p"}
    assert main(["backup"], cfg, clock=lambda: at()) == 0
    assert "[warn] ping failed" in capsys.readouterr().err


def test_a_manifest_that_is_json_with_the_right_names(env, tmp_path):
    assert main(["backup"], env, clock=lambda: at()) == 0
    doc = json.loads((tmp_path / "backups" / core.manifest_name(core.stamp_of(at()))).read_text())
    assert doc["version"] == 1
    assert doc["sources"] == [str(p) for p in from_env(env).sources]


def test_agebin_encryptor_requires_a_program(tmp_path):
    with (
        pytest.raises(agebin.AgeError),
        open(tmp_path / "o", "wb") as out,
        agebin.encryptor("no-such-age", ("age1x",), out),
    ):
        pass
