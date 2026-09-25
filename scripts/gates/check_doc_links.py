#!/usr/bin/env python3
"""Check that every relative link and cross-reference in ``docs/`` resolves.

The gap this closes. ``check_doc_references.py`` holds the prose to the q
functions it names; ``check_declaration_reference.py`` holds the declaration
page to the declared keys; ``generate_*.py --check`` hold the generated
artifacts to their sources. Nothing held a *link* to anything. A page could
point at ``../guides/gone.md`` and every gate passed, because a path that
appears in no generated artifact has nothing to disagree with - the same
shape of hole that ``check_doc_references.py`` was written to close for
function names.

WHY QUARTO RATHER THAN A REGEX. A regex over ``](...)`` finds a missing
file, and that is roughly half the problem. Quarto resolves the link the way
a reader's browser will: it follows ``.md`` to the page that document
actually becomes, understands a section anchor, and reports an unresolved
``@sec-`` cross-reference as well. Getting that right by hand means
reimplementing a markdown renderer, badly. The tree already renders under
Quarto - ``docs/_quarto.yml`` - so this asks the renderer.

WHAT QUARTO DOES NOT COVER, and why there is a second pass below. Quarto
resolves a link's TARGET FILE. It does not check the ``#fragment`` after it,
so ``stack.md#no-such-section`` renders without a murmur - measured, not
assumed. This tree leans on those fragments (30 of them, and ``guides/uqs.md``
alone has 18), and they are read on GitHub as often as in the rendered site.
So anchors are checked here, against GitHub's slug rules, because GitHub is
where these links are followed today.

WHY A WRAPPER AT ALL, rather than just running Quarto. Quarto reports both
failures as *warnings* and exits 0, and ``--fail-if-warnings`` does not
change that for these two (measured, on Quarto 1.10). A warning nobody fails
on is a warning nobody reads, so the exit code has to be manufactured here.

Quarto also names the broken target but not the file holding it, so this
tracks the ``[n/m] path`` progress line and attributes each warning to the
document being rendered when it appeared.

MISSING QUARTO IS A FAILURE, not a skip - with one deliberate exception.
``--allow-missing`` downgrades it to a warning, and the pre-commit hook
passes that flag: a q developer without Quarto should not be blocked from
committing. CI passes no flag, so there a missing Quarto is a hard error.
That asymmetry is the opposite of the contract-surface gate's, and for a
stated reason: hosted runners cannot have KDB-X, but installing Quarto is
one step. The environment that CAN enforce is the one that does.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"

#: Quarto colours its output; the progress line is unreadable without this.
ANSI = re.compile(r"\x1b\[[0-9;]*m")

#: "[ 3/31] guides/uqs.md" - the document every following warning belongs to.
#: Quarto right-aligns the index, so single digits carry a leading space. A
#: pattern without \s* silently matched only files 10 and up, which is worse
#: than not matching at all: warnings from the first nine were attributed to
#: whatever document preceded them.
PROGRESS = re.compile(r"^\[\s*\d+/\d+\]\s+(\S+)")

#: The two failures worth gating on. Quarto words them differently.
UNRESOLVED = re.compile(r"Unable to resolve (?:link target|crossref)[:]?\s*(\S+)", re.IGNORECASE)


def quarto() -> str | None:
    return shutil.which("quarto")


def render(exe: str, out_dir: str) -> tuple[int, list[str]]:
    """Render docs/ to a throwaway directory, returning (code, clean lines).

    A temp output dir rather than docs/_site: a gate that leaves build
    artifacts behind teaches people to add them to .gitignore and then to
    ignore them.
    """
    proc = subprocess.run(
        [exe, "render", "--output-dir", out_dir],
        cwd=DOCS,
        capture_output=True,
        text=True,
    )
    # Quarto redraws the progress line with a carriage return, so several
    # "[n/m] path" entries can share one "line". Without this split the
    # document count is short and - worse - a warning is attributed to
    # whichever document happened to end the line it was printed after.
    raw = (proc.stdout + "\n" + proc.stderr).replace("\r", "\n")
    return proc.returncode, [ANSI.sub("", line).strip() for line in raw.splitlines()]


def findings(lines: list[str]) -> list[tuple[str, str]]:
    """(document, unresolved target) for every warning Quarto emitted."""
    current = "<before any document>"
    out: list[tuple[str, str]] = []
    for line in lines:
        progress = PROGRESS.match(line)
        if progress:
            current = progress.group(1)
            continue
        hit = UNRESOLVED.search(line)
        if hit:
            out.append((current, hit.group(1)))
    return out


#: A markdown link with a fragment: [text](path/to/doc.md#some-anchor).
#: Absolute URLs and bare in-page "#anchor" links are left alone - the first
#: is not ours to verify, the second Quarto's own rendering would catch.
ANCHOR_LINK = re.compile(r"\]\((?!https?:|#)([^)\s#]+)#([^)\s]+)\)")

#: Setext and ATX headings. Fenced code is stripped first, so a "# comment"
#: inside a q block is not mistaken for one.
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*$", re.MULTILINE)
FENCE = re.compile(r"^```.*?^```", re.DOTALL | re.MULTILINE)

#: An explicit id, as Quarto and Pandoc write it: "## Title {#sec-thing}".
EXPLICIT_ID = re.compile(r"\{#([^}\s]+)[^}]*\}\s*$")


def slug(heading: str) -> str:
    """GitHub's anchor for a heading.

    Lowercase, drop everything that is not a word character, a space or a
    hyphen, then spaces to hyphens. Inline code and emphasis markers go
    first, so `` `.qbw` and the plant `` and ".qbw and the plant" agree.
    """
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text.strip())


def anchors_in(path: Path) -> set[str]:
    body = FENCE.sub("", path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for raw in HEADING.findall(body):
        explicit = EXPLICIT_ID.search(raw)
        if explicit:
            found.add(explicit.group(1))
            raw = EXPLICIT_ID.sub("", raw).strip()
        found.add(slug(raw))
    return found


def broken_anchors() -> list[tuple[str, str]]:
    """(document, "target#anchor") for every fragment that resolves to no
    heading in the document it points at."""
    out: list[tuple[str, str]] = []
    cache: dict[Path, set[str]] = {}
    for doc in sorted(DOCS.rglob("*.md")):
        if "_site" in doc.parts:
            continue
        for target, anchor in ANCHOR_LINK.findall(doc.read_text(encoding="utf-8")):
            dest = (doc.parent / target).resolve()
            if dest.suffix != ".md" or not dest.is_file():
                continue  # a missing FILE is Quarto's finding, not this one
            if dest not in cache:
                cache[dest] = anchors_in(dest)
            if anchor not in cache[dest]:
                out.append((str(doc.relative_to(DOCS)), f"{target}#{anchor}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--allow-missing",
        action="store_true",
        help="warn instead of failing when Quarto is not installed",
    )
    args = ap.parse_args()

    exe = quarto()
    if exe is None:
        if args.allow_missing:
            print(
                "check_doc_links: quarto is not installed, so the links in docs/ "
                "were NOT checked. Install it from https://quarto.org to run this "
                "locally; CI checks them on every push.",
                file=sys.stderr,
            )
            return 0
        print(
            "check_doc_links: quarto is not installed. Install it from "
            "https://quarto.org, or pass --allow-missing to downgrade this to "
            "a warning.",
            file=sys.stderr,
        )
        return 1

    with tempfile.TemporaryDirectory(prefix="uqf-doclinks-") as out_dir:
        code, lines = render(exe, out_dir)
        broken = findings(lines) + broken_anchors()
        rendered = sum(1 for line in lines if PROGRESS.match(line))

    if code != 0 and not broken:
        print("check_doc_links: quarto render failed:", file=sys.stderr)
        print("\n".join(lines[-25:]), file=sys.stderr)
        return 1

    if broken:
        print(
            f"{len(broken)} unresolved link(s) or cross-reference(s) in docs/:\n",
            file=sys.stderr,
        )
        for doc, target in broken:
            print(f"  docs/{doc}: {target}", file=sys.stderr)
        print(
            "\nEach one is a link a reader follows to nothing. Fix the path, or "
            "the anchor, or delete the link.",
            file=sys.stderr,
        )
        return 1

    print(
        f"check_doc_links: {rendered} document(s) rendered, every link target and "
        f"section anchor in docs/ resolves"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
