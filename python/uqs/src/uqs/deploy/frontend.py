"""The browser frontend in a release (#1646): opt-in, and no Node on the server.

A release always ships python/uqf_frontend's source, as one of the workspace's
packages, but nothing that runs it. `uqs deploy build --frontend DIST` adds:

  the backend   uqf-frontend's wheel and its locked dependencies (fastapi,
                uvicorn, pydantic, kola, ...), exported from uv.lock beside
                uqs's (payload.python_payload), so the server installs it
                offline into the release's .venv after uqs - by the push's
                prepare, or by scripts/install_release.py. The manifest lists
                it in `python.extra_wheels`.
  the assets    DIST, the app `npm --prefix web run build` wrote (web/dist),
                copied file for file to web/dist in the release, where
                uqf_frontend.stack finds it and serves it at /ui/. The builder
                runs Node, never the server.

DIST is refused without an index.html, and for a symbolic link, a file the
release would never ship (payload.is_excluded: an .env, a key, a licence,
node_modules). A default
build carries neither part, and its manifest is unchanged.

Starting it stays the operator's: `.venv/bin/uvicorn --factory
uqf_frontend.app:create_app` from the release, with `deploy.env` loaded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from uqs.deploy.artifact import WHEEL_DIR, ReleaseError
from uqs.deploy.payload import is_excluded

#: The workspace package whose wheel and dependencies the release carries.
PACKAGE = "uqf-frontend"
#: Its wheel's file-name prefix.
WHEEL_PREFIX = "uqf_frontend-"
#: Where the assets go in a release: <root>/web/dist, as uqf_frontend.stack reads.
WEB_DIST = "web/dist"
HELP = (
    "Carry the browser frontend: uqf-frontend's wheel and locked dependencies, and the "
    "app `npm --prefix web run build` wrote to DIST (usually web/dist); no Node on the server"
)


@dataclass
class Selected:
    """What --frontend adds to a release; nothing when it was not given."""

    dist: Path | None = None
    files: dict[str, Path] = field(default_factory=dict)

    @property
    def packages(self) -> tuple[str, ...]:
        """The workspace packages whose wheels the release adds to uqs's."""
        return (PACKAGE,) if self.dist else ()

    def members(self) -> dict[str, Path]:
        return dict(self.files)

    def annotate(self, manifest: dict, wheels: list[Path]) -> None:
        """Record the frontend in a release's manifest; a default build's is
        left exactly as it was."""
        if self.dist is None:
            return
        found = [f"{WHEEL_DIR}/{w.name}" for w in wheels if w.name.startswith(WHEEL_PREFIX)]
        if len(found) != 1:
            raise ReleaseError("frontend", f"expected one {PACKAGE} wheel, found {len(found)}")
        manifest["python"]["extra_wheels"] = found
        manifest["frontend"] = {
            "package": PACKAGE,
            "wheel": found[0],
            "web_dist": WEB_DIST,
            "files": len(self.files),
        }


def select(dist: Path | str | None) -> Selected:
    """The frontend --frontend names, checked; an empty selection without it."""
    if dist is None:
        return Selected()
    root = Path(dist)
    if not (root / "index.html").is_file():
        raise ReleaseError(
            "frontend",
            f"--frontend {dist}: no index.html - build the app first (`npm --prefix web ci && "
            "npm --prefix web run build`) and pass the directory it wrote, web/dist",
        )
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ReleaseError("frontend", f"--frontend {dist}: {rel} is a symbolic link")
        if path.is_dir():
            continue
        if is_excluded(rel):
            raise ReleaseError(
                "frontend", f"--frontend {dist}: {rel} is a file no release ships - remove it"
            )
        files[f"{WEB_DIST}/{rel}"] = path
    return Selected(dist=root, files=files)
