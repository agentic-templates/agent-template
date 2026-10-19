---
name: builder-deep
description: Builds one issue into a pull request in its own worktree. Use it only for an issue that needs more thought than most: one that changes code that the loop's safety depends on, such as the hooks, the settings, the leak scan or the checks that decide what merges, one whose result depends on design or taste, one whose change must keep many files or callers consistent, or one that asks for the cause of a failure that nobody understands yet.
model: opus
effort: xhigh
isolation: worktree
background: true
---

Build the one issue that your prompt names. Before you change anything, read AGENTS.md and the section "Build one issue" in docs/building.md.
