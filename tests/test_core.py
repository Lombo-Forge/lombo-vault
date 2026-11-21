import json
from datetime import UTC, datetime
from pathlib import PurePosixPath as P

import pytest

from lombo_vault import core

H = "a" * 64


def archive(stamp):
    return core.archive_name(stamp)


def pair(stamp):
    return [core.archive_name(stamp), core.manifest_name(stamp)]


def test_names_round_trip():
    now = datetime(2026, 11, 21, 4, 30, 5, tzinfo=UTC)
    stamp = core.stamp_of(now)
    assert stamp == "20261121T043005Z"
    assert core.archive_name(stamp) == "lombovault_20261121T043005Z.tar.gz.age"
    assert core.parse_name(core.archive_name(stamp)) == (stamp, "archive")
    assert core.parse_name(core.manifest_name(stamp)) == (stamp, "manifest")
    assert core.stamp_to_datetime(stamp) == now
    assert core.age_days(stamp, datetime(2026, 11, 23, 4, 30, 5, tzinfo=UTC)) == 2


@pytest.mark.parametrize(
    "name",
    [
        "notes.txt",
        "lombovault_20261121T043005Z.tar.gz.age.bak",
        "xlombovault_20261121T043005Z.tar.gz.age",
        "lombovault_2026.tar.gz.age",
        "lombovault_20261121T043005Z.tar.age",
        "../lombovault_20261121T043005Z.tar.gz.age",
        "lombovault_20261121T043005Z.manifest.json/",
    ],
)
def test_foreign_names_are_never_the_tools_own(name):
    assert core.parse_name(name) is None
    assert not core.is_own_file(name)


def test_own_file_covers_final_and_temp_names():
    final = core.archive_name("20261121T043005Z")
    assert core.is_own_file(final)
    assert core.is_own_file(core.temp_name(final))


def test_complete_stamps_need_both_files():
    names = [*pair("20261101T000000Z"), core.archive_name("20261102T000000Z"), core.manifest_name("20261103T000000Z")]
    assert core.complete_stamps(names) == ["20261101T000000Z"]
    assert core.runs(names)["20261102T000000Z"] == {"archive"}


# After every stamp used below (later stamps count as clock errors).
REAL_NOW = datetime(2026, 12, 1, tzinfo=UTC).timestamp()


def files(*stamps, extra=()):
    out = {}
    for s in stamps:
        out.update(dict.fromkeys(pair(s), 1000.0))
    out.update(dict.fromkeys(extra, 1000.0))
    return out


def test_prune_keeps_the_newest_n_complete_runs():
    stamps = [f"202611{d:02d}T000000Z" for d in range(1, 8)]
    doomed = core.plan_prune(files(*stamps), keep=3, now_epoch=REAL_NOW)
    assert sorted(doomed) == sorted(n for s in stamps[:4] for n in pair(s))


def test_prune_with_fewer_runs_than_keep_deletes_nothing():
    assert core.plan_prune(files("20261101T000000Z", "20261102T000000Z"), keep=7, now_epoch=REAL_NOW) == []
    assert core.plan_prune(files("20261101T000000Z", "20261102T000000Z"), keep=2, now_epoch=REAL_NOW) == []
    assert core.plan_prune({}, keep=1, now_epoch=0) == []


def test_prune_never_returns_foreign_files():
    foreign = ["notes.txt", "photo.jpg", "lombovault_old.tar.gz.age", ".hidden"]
    f = files("20261101T000000Z", "20261102T000000Z", "20261103T000000Z", extra=foreign)
    doomed = core.plan_prune(f, keep=1, now_epoch=REAL_NOW)
    assert doomed
    assert all(core.parse_name(n) for n in doomed)
    assert not set(doomed) & set(foreign)


def test_prune_counts_only_complete_runs_and_keeps_newer_unfinished_ones():
    # newest archive has no manifest yet (a backup in progress or crashed): it must not count as a kept run,
    # and it is newer than the oldest kept run, so it is left alone.
    f = files("20261101T000000Z", "20261102T000000Z", extra=[archive("20261110T000000Z")])
    assert sorted(core.plan_prune(f, keep=1, now_epoch=REAL_NOW)) == sorted(pair("20261101T000000Z"))


def test_prune_removes_old_unfinished_runs():
    f = files("20261102T000000Z", "20261103T000000Z", extra=[archive("20261101T000000Z")])
    assert core.plan_prune(f, keep=2, now_epoch=REAL_NOW) == [archive("20261101T000000Z")]
    doomed = core.plan_prune(f, keep=1, now_epoch=REAL_NOW)
    assert set(doomed) == {archive("20261101T000000Z"), *pair("20261102T000000Z")}


def test_stale_temp_files_go_only_when_old():
    tmp = core.temp_name(archive("20261101T000000Z"))
    assert core.plan_prune({tmp: 1000.0}, keep=7, now_epoch=1000.0 + 3600) == []
    assert core.plan_prune({tmp: 1000.0}, keep=7, now_epoch=1000.0 + 90000) == [tmp]


def test_overlap_and_conflicts():
    assert core.overlaps(P("/a/b"), P("/a"))
    assert core.overlaps(P("/a"), P("/a/b"))
    assert core.overlaps(P("/a"), P("/a"))
    assert not core.overlaps(P("/a"), P("/ab"))
    assert core.conflicts(P("/mnt/hdd/backups"), [P("/mnt/ssd/app"), P("/etc/stack")]) == []
    assert "inside the source" in core.conflicts(P("/data/app/backups"), [P("/data/app")])[0]
    assert "inside the backup folder" in core.conflicts(P("/data/b"), [P("/data/b/app")])[0]
    assert core.conflicts(P("/data/same"), [P("/data/same")])


def test_exclude_matches_names_and_paths():
    pats = ("Cache", "*.log", "/data/tmp/*")
    assert core.is_excluded("/data/app/Cache/x", pats)
    assert core.is_excluded("/data/app/server.log", pats)
    assert core.is_excluded("/data/tmp/anything", pats)
    assert not core.is_excluded("/data/app/config.xml", pats)
    assert not core.is_excluded("/data/app/config.xml", ())


def test_member_name_drops_the_leading_slash():
    assert core.member_name("/data/app/a.db") == "data/app/a.db"


def manifest_text(stamp="20261101T000000Z", files=None, sha=H, size=10):
    return core.build_manifest(
        stamp, sha, size, files if files is not None else {"srv/a": (H, 5), "srv/b": ("b" * 64, 7)}, ["/srv"]
    )


def test_manifest_round_trip():
    m = core.parse_manifest(manifest_text(), "20261101T000000Z")
    assert m.files == {"srv/a": H, "srv/b": "b" * 64}
    assert m.total_bytes == 12
    assert m.archive == archive("20261101T000000Z")
    assert m.archive_sha256 == H
    assert m.archive_size == 10


def edited(**changes):
    doc = json.loads(manifest_text())
    doc.update(changes)
    return json.dumps(doc)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        edited(version=2),
        edited(created="20261102T000000Z"),
        edited(archive="other.tar.gz.age"),
        edited(archive_sha256="xyz"),
        edited(archive_size="10"),
        edited(archive_size=True),
        edited(files=[]),
        edited(files={"srv/a": "not-a-dict"}),
        edited(files={"srv/a": {"sha256": "short", "size": 1}}),
        edited(files={"srv/a": {"sha256": H, "size": -1}}),
        edited(files={"srv/a": {"sha256": H, "size": True}}),
        edited(files={"/abs": {"sha256": H, "size": 1}}),
        edited(files={"a/../../b": {"sha256": H, "size": 1}}),
    ],
)
def test_damaged_manifests_are_refused(text):
    with pytest.raises(core.ManifestError):
        core.parse_manifest(text, "20261101T000000Z")


def test_compare_manifest():
    same = core.compare_manifest({"a": "1", "b": "2"}, {"a": "1", "b": "2"})
    assert same.clean
    assert same.summary() == "identical"
    diff = core.compare_manifest({"a": "1", "b": "2", "c": "3"}, {"a": "9", "b": "2", "z": "0"})
    assert (diff.missing, diff.extra, diff.changed) == (["c"], ["z"], ["a"])
    assert not diff.clean
    assert diff.summary() == "1 missing (c); 1 extra (z); 1 changed (a)"
    many = core.compare_manifest({f"f{i}": "1" for i in range(6)}, {})
    assert "and 3 more" in many.summary()


def test_space_needed_has_a_margin():
    assert core.space_needed(0) == 64 * 1024 * 1024
    assert core.space_needed(10**9) > 10**9
