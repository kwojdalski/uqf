"""An artifact by URL or GitHub release tag (#872). The network is a dict:
`opener` maps each URL to its bytes, and fails on any other."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from uqs.deploy import fetch
from uqs.deploy.config import DeployError

NAME = "uqf-20261008T120000Z-0123456789ab.tar.gz"
NAME_40 = "uqf-20261008T120500Z-0123456789ab.tar.gz"
BASE = "https://github.com/acme/uqf/releases/download/v1.4.0"


def _web(**pages: bytes):
    seen: list[str] = []

    def opener(url: str) -> bytes:
        seen.append(url)
        if url not in pages:
            raise DeployError("artifact", f"404 {url}")
        return pages[url]

    return opener, seen


def _published(body: bytes = b"archive bytes") -> dict[str, bytes]:
    digest = hashlib.sha256(body).hexdigest()
    return {f"{BASE}/{NAME}": body, f"{BASE}/{NAME}.sha256": f"{digest}  {NAME}\n".encode()}


def _release(*names: str) -> bytes:
    return json.dumps(
        {"assets": [{"name": n, "browser_download_url": f"{BASE}/{n}"} for n in names]
                   + [{"name": "notes.txt", "browser_download_url": f"{BASE}/notes.txt"}]}
    ).encode()  # fmt: skip


def test_a_local_path_is_used_as_it_is(tmp_path):
    local = tmp_path / NAME
    local.write_bytes(b"x")
    opener, seen = _web()
    assert fetch.resolve(str(local), opener=opener) == str(local) and seen == []


def test_a_url_downloads_the_archive_and_its_published_checksum_beside_it(tmp_path):
    opener, _ = _web(**_published())
    got = Path(fetch.resolve(f"{BASE}/{NAME}", opener=opener, cache=tmp_path))
    assert got.name == NAME and got.read_bytes() == b"archive bytes"
    assert (
        Path(f"{got}.sha256").read_text().startswith(hashlib.sha256(b"archive bytes").hexdigest())
    )


def test_a_tag_finds_its_one_artifact_through_the_api(tmp_path):
    api = f"{fetch.API}/repos/acme/uqf/releases/tags/v1.4.0"
    opener, seen = _web(**{api: _release(NAME), **_published()})
    got = fetch.resolve("v1.4.0", repo="acme/uqf", opener=opener, cache=tmp_path)
    assert Path(got).name == NAME and seen[0] == api
    assert "v1.4.0" in got, "each tag downloads into a directory of its own"


def test_a_tag_with_two_artifacts_is_refused_naming_both(tmp_path):
    api = f"{fetch.API}/repos/acme/uqf/releases/tags/v1.4.0"
    opener, _ = _web(**{api: _release(NAME, NAME_40)})
    with pytest.raises(DeployError, match=f"carries 2 artifacts.*{NAME}.*{NAME_40}"):
        fetch.resolve("v1.4.0", repo="acme/uqf", opener=opener, cache=tmp_path)


def test_a_tag_with_no_artifact_is_refused(tmp_path):
    api = f"{fetch.API}/repos/acme/uqf/releases/tags/v1.4.0"
    opener, _ = _web(**{api: _release()})
    with pytest.raises(DeployError, match="carries no uqf artifact"):
        fetch.resolve("v1.4.0", repo="acme/uqf", opener=opener, cache=tmp_path)


def test_a_tag_with_no_known_repository_says_how_to_name_one(tmp_path):
    with pytest.raises(DeployError, match="pass --release-repo OWNER/NAME"):
        fetch.resolve("v1.4.0", root=tmp_path, opener=_web()[0], cache=tmp_path)


@pytest.mark.parametrize("url", [f"http://example.com/{NAME}", f"file:///tmp/{NAME}"])
def test_only_https_is_downloaded(tmp_path, url):
    with pytest.raises(DeployError, match="only an https:// URL"):
        fetch.resolve(url, opener=_web()[0], cache=tmp_path)


def test_a_url_that_names_no_artifact_is_refused_before_downloading(tmp_path):
    opener, seen = _web()
    with pytest.raises(DeployError, match="does not name an artifact"):
        fetch.resolve(f"{BASE}/payload.sh", opener=opener, cache=tmp_path)
    assert seen == []


def test_a_downloaded_artifact_whose_checksum_does_not_match_is_refused(tmp_path):
    """The checksum check is read_artifact's, as for a local file: run it here
    on a download whose archive differs from what was published."""
    from uqs.deploy.config import load_artifact

    pages = _published()
    pages[f"{BASE}/{NAME}"] = b"tampered"
    got = fetch.resolve(f"{BASE}/{NAME}", opener=_web(**pages)[0], cache=tmp_path)
    with pytest.raises(DeployError, match=f"{NAME} does not match {NAME}.sha256"):
        load_artifact(got)


@pytest.mark.parametrize(
    ("url", "repo"),
    [
        ("git@github.com:acme/uqf.git", "acme/uqf"),
        ("https://github.com/acme/uqf.git", "acme/uqf"),
        ("https://github.com/acme/uqf", "acme/uqf"),
        ("https://gitlab.com/acme/uqf.git", None),
    ],
)
def test_the_release_repository_defaults_to_origin(tmp_path, url, repo):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", url], check=True)
    assert fetch.origin_repo(tmp_path) == repo
