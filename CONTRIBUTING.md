# Contributing

This repository accepts pull requests from its owner only.

**A pull request is merged only if Krzysztof Wojdalski (`kwojdalski`) authored
it.** Pull requests from anyone else, including forks and automated accounts,
are not reviewed or merged, and are closed. This applies however small or well
tested the change is.

The rule is enforced, not only stated. The "Pull request author" job in
`.github/workflows/ci.yml` fails any pull request whose author is not
`kwojdalski`, without checking out its code. The required "Repository checks"
job depends on it, so such a pull request can never satisfy the branch ruleset.

To report a bug or propose a change, open an issue instead.
