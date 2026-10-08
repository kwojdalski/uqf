"""A release artifact by URL or by GitHub release tag, for `uqs deploy push` (#872).

.github/workflows/release.yml builds artifacts in CI, after the same checks
as every push, and attaches each one's .tar.gz, .sha256 and .manifest.json to
a GitHub Release. So a server runs exactly what CI built and tested, never
something built on whichever laptop ran `uqs deploy build`.

`push` takes, as its ARTIFACT:

  - a local path, as before;
  - a URL to the .tar.gz: it and the .sha256 beside it are downloaded;
  - a release tag, e.g. v1.4.0: the release's assets are listed through the
    GitHub API (--release-repo, else this checkout's origin) and its one
    artifact downloaded - or, when it carries more than one (the plain and
    the --q-target 4.0 build), refused naming each, to be given by URL.

Downloads land in a cache directory of their own, and the archive is then
checked exactly as a local one is (deploy.artifact.read_artifact): against
the published .sha256 beside it, and every member against the manifest,
before preflight. A token in GH_TOKEN or GITHUB_TOKEN is sent for a private
repository, and never printed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path

from uqs.deploy.config import DeployError
from uqs.logger import get_logger

log = get_logger(__name__)

#: How an artifact's archive is named on a release.
ARCHIVE = re.compile(r"uqf-[0-9TZ]+-[0-9a-f]{12}\.tar\.gz")
#: A git tag, as `push` reads one: not a path and not a URL.
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
#: owner/name on GitHub, as --release-repo takes it.
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
API = "https://api.github.com"
TIMEOUT = 60

#: url -> bytes. urllib by default; a test passes its own.
Opener = Callable[[str], bytes]


def _cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "uqf" / "releases"


def _token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def http_get(url: str) -> bytes:
    headers = {"User-Agent": "uqs-deploy"}
    if url.startswith(API):
        headers["Accept"] = "application/vnd.github+json"
    token = _token()
    if token and (url.startswith(API) or url.startswith("https://github.com/")):
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=TIMEOUT
        ) as r:  # noqa: S310 - https only, checked by the caller
            return r.read()
    except OSError as exc:
        raise DeployError("artifact", f"could not download {url}: {exc}") from None


def origin_repo(root: Path) -> str | None:
    """owner/name of this checkout's origin on GitHub, or None."""
    try:
        url = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, check=False, timeout=10,
        ).stdout.strip()  # fmt: skip
    except OSError:
        return None
    found = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return found.group(1) if found else None


def _download(url: str, opener: Opener, dest: Path) -> Path:
    name = url.rsplit("/", 1)[-1]
    if not ARCHIVE.fullmatch(name):
        raise DeployError("artifact", f"{url} does not name an artifact (uqf-<release>.tar.gz)")
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / name
    archive.write_bytes(opener(url))
    # the published checksum, beside the archive, where read_artifact checks it
    Path(f"{archive}.sha256").write_bytes(opener(f"{url}.sha256"))
    log.info("downloaded {} and its .sha256 into {}", name, dest)
    return archive


def _tag_url(tag: str, repo: str, opener: Opener) -> str:
    body = json.loads(opener(f"{API}/repos/{repo}/releases/tags/{tag}"))
    urls = [
        a["browser_download_url"]
        for a in body.get("assets", [])
        if ARCHIVE.fullmatch(a.get("name", ""))
    ]
    if not urls:
        raise DeployError("artifact", f"release {tag} of {repo} carries no uqf artifact")
    if len(urls) > 1:
        raise DeployError(
            "artifact",
            f"release {tag} of {repo} carries {len(urls)} artifacts - give the one to deploy "
            "by URL: " + ", ".join(urls),
        )
    return urls[0]


def resolve(
    artifact: str,
    *,
    repo: str | None = None,
    root: Path | None = None,
    opener: Opener = http_get,
    cache: Path | None = None,
) -> str:
    """A local path to `artifact`: as given when it is one, else downloaded
    from a URL or a release tag. The download is checked by load_artifact."""
    if Path(artifact).exists():
        return artifact
    cache = cache or _cache()
    if artifact.startswith("https://"):
        return str(_download(artifact, opener, cache / "url"))
    if artifact.startswith(("http://", "file:", "ftp:")):
        raise DeployError("artifact", f"{artifact}: only an https:// URL is downloaded")
    if artifact.endswith(".tar.gz") or not TAG.fullmatch(artifact):
        raise DeployError("artifact", f"no artifact at {artifact}")
    repo = repo or (origin_repo(root) if root else None)
    if not repo or not REPO.fullmatch(repo):
        raise DeployError(
            "artifact",
            f"{artifact} is not a file, so it is read as a release tag - but no GitHub "
            "repository is known: pass --release-repo OWNER/NAME",
        )
    url = _tag_url(artifact, repo, opener)
    return str(_download(url, opener, cache / re.sub(r"[^A-Za-z0-9._-]", "_", artifact)))
