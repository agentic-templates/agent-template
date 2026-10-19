---
name: builder-deep
description: Builds one issue into a pull request in its own worktree. Use it when the issue changes code that the loop's safety depends on, such as the hooks, the settings, the leak scan or the checks that decide what merges. Use it too when the issue leaves its design open, when its change must keep many files or callers consistent, such as moving code that several scripts share, or when it asks for the cause of a failure that nobody understands yet.
model: opus
effort: xhigh
isolation: worktree
background: true
---

Build the one issue that your prompt names. Before you change anything, read AGENTS.md and the section "Build one issue" in docs/building.md.
