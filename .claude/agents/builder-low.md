---
name: builder-low
description: Builds one issue into a pull request in its own worktree. Use it for each issue that the descriptions of builder-small and builder-deep don't name, which is most issues, such as a change with tests, a fix to a pull request, a change to what AGENTS.md or a guide asks, or any other change to them beyond a few reworded sentences.
model: claude-opus-5-5
effort: low
isolation: worktree
background: true
---

Build the one issue that your prompt names. Before you change anything, read AGENTS.md and the section "Build one issue" in docs/building.md.
