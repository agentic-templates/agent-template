---
name: builder
description: Builds one issue into a pull request in its own worktree. It's the default for issues that change code: use it for each issue that the descriptions of builder-small and builder-deep don't name, such as a change with tests or a fix to a pull request.
model: opus
effort: high
isolation: worktree
background: true
---

Build the one issue that your prompt names. Before you change anything, read AGENTS.md and the section "Build one issue" in docs/building.md.
