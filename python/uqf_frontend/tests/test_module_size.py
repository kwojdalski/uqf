"""The per-area modules stay within their line budget (#2168).

models.py, app.py and web/src/api.ts were split one module per area because
nearly every frontend change touched them, and two PRs in different areas
collided in one file. An area module that grows past the budget is on its
way back to being that file: split it again, by sub-area, rather than
raising the number. 400 is the uqs package's budget (test_module_split.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PKG = REPO / "python" / "uqf_frontend" / "src" / "uqf_frontend"

#: Each directory of area modules, and the files in it held to the budget.
AREAS = {
    "models": (PKG / "models", "*.py"),
    "routes": (PKG / "routes", "*.py"),
    "api": (REPO / "web" / "src" / "api", "*.ts"),
}

BUDGET = 400


@pytest.mark.parametrize("area", sorted(AREAS))
def test_every_area_module_is_within_the_budget(area):
    directory, pattern = AREAS[area]
    found = sorted(directory.glob(pattern))
    assert len(found) > 3, f"{directory} holds too few modules to mean anything"
    over = {p.name: n for p in found if (n := len(p.read_text().splitlines())) > BUDGET}
    assert not over, f"{area} modules past {BUDGET} lines - split them by sub-area: {over}"


@pytest.mark.parametrize(
    "gone",
    [
        PKG / "models.py",
        REPO / "web" / "src" / "api.ts",
    ],
)
def test_the_single_file_is_not_back(gone):
    """A file beside the package of the same name would shadow it (models.py
    beside models/) or be a second home for the API's types (api.ts beside
    api/)."""
    assert not gone.exists(), f"{gone.relative_to(REPO)} is back; its area modules replaced it"
