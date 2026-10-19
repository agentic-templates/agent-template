---
name: builder
description: Builds one issue into a pull request in its own worktree. Use it for each issue that the descriptions of builder-small and builder-deep don't name, which is most issues, such as a change with tests, a change to AGENTS.md or a guide other than a few reworded sentences, or a fix to a pull request.
model: opus
effort: high
isolation: worktree
background: true
---

Build the one issue that your prompt names. Before you change anything, read AGENTS.md and the section "Build one issue" in docs/building.md.
