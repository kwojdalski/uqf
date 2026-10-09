"""Every test in this repository runs outside the git repository git is
committing to (#996).

git runs pre-commit hooks with GIT_DIR and GIT_INDEX_FILE exported, and the
`python-tests` hook runs this suite. Several tests build a scratch repository
with `git init <tmp>` - and with GIT_DIR set, `git init <dir>` re-initialises
$GIT_DIR instead, writing `core.bare = true` into it. From a worktree that is
the shared config of the main checkout, which then refuses every command
("this operation must be run in a work tree").

Here, at the repository root, so it covers every testpath - the gates' and
the portable converter's tests as well as python/'s - and runs before any
test module is imported. Dropping the variables changes nothing a test means:
git then finds the repository it is pointed at (`git -C <path>`) or the one
it is run in, as it does outside a hook.
"""

from __future__ import annotations

import os

#: The variables with which git tells a child process which repository,
#: index and work tree to use.
GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
)

for _name in GIT_LOCATION_VARS:
    os.environ.pop(_name, None)
