---
name: builder-deep
description: "Builds one issue into a pull request in its own worktree. Use it only for an issue that needs more thought than most, of one of four kinds. The first changes code that the loop's safety depends on: the hooks that block an action or turn on auto-merge, the settings, the leak scan and the checks that decide what merges. The second creates a visual design that the project has no example of to follow, such as a new graphical interface or a 3D model. The third changes code that many files or callers use, and must keep them all working. The fourth asks for the cause of a failure that nobody understands yet."
model: claude-opus-5-5
effort: xhigh
isolation: worktree
background: true
---

Build the one issue that your prompt names. Before you change anything, read AGENTS.md and the section "Build one issue" in docs/building.md.
