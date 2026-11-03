"""Deletion runs only against temporary deployments, through the real helper."""

import os

import pytest

from uqf_frontend import remote_helper as helper


def delete(root, paths, confirmed=True):
    return helper.handle(
        {"protocol": 1, "op": "delete", "root": str(root), "paths": paths, "confirmed": confirmed}
    )


def test_partial_success_and_nonrecursive_confinement(tmp_path):
    (tmp_path / "file").write_text("x")
    (tmp_path / "empty").mkdir()
    (tmp_path / "occupied").mkdir()
    (tmp_path / "occupied" / "keep").write_text("keep")
    (tmp_path / "link").symlink_to(tmp_path / "file")
    os.mkfifo(tmp_path / "fifo")
    paths = ["link", "fifo", "file", "empty", "occupied", "missing", "", "../escape", "/etc/passwd"]
    result = delete(tmp_path, paths)
    assert result["ok"]
    assert [r["path"] for r in result["results"] if r["deleted"]] == ["file", "empty"]
    assert (tmp_path / "occupied" / "keep").read_text() == "keep"
    assert (tmp_path / "link").is_symlink()


@pytest.mark.parametrize("confirmed", [False, None, 1, "true"])
def test_confirmation_is_required_before_any_change(tmp_path, confirmed):
    path = tmp_path / "keep"
    path.write_text("keep")
    assert not delete(tmp_path, ["keep"], confirmed)["ok"]
    assert path.exists()


def test_symlink_parent_and_unusual_filenames(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (outside / "keep").write_text("keep")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    names = ["with space", "it's quoted", "$(touch pwned)", "line\nbreak", "-flag"]
    for name in names:
        (root / name).write_text("x")
    result = delete(root, ["escape/keep", *names])
    assert not result["results"][0]["deleted"]
    assert all(r["deleted"] for r in result["results"][1:])
    assert (outside / "keep").read_text() == "keep"
