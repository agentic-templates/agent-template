# Review guide

## Review a pull request

- Compare the change with its issue's "Done when" list and the definition of done in AGENTS.md.
- Report only errors: wrong behavior, a missing test, a security problem, or text or code that breaks AGENTS.md or a guide in `docs/`. Such text includes each sentence that the pull request adds to AGENTS.md, the README, a guide, an agent type in `.claude/agents` or the pull request's description, and that breaks `docs/writing.md`, `docs/pages.md` or `docs/rules.md`. Don't ask for changes that a formatter would make, or for work outside the issue.
- If your prompt names a round of fixes by its range of commits, it also gives the errors that the last reviewer reported. Read only the changes in that range. Report each of those errors that the changes don't fix, and any other error in the changes. A vulnerability is an error even if main has it too. Any other problem that main has too, such as one in code that a fix copied from main, isn't an error of the pull request. List each such problem apart from the errors, so that whoever asked for the review opens an issue for it.
- Give each error a case that shows it, such as an input and the wrong result it gives, or a sentence and the rule it breaks. Then say what to change and why. For a security problem, leave out the case, and describe the problem as AGENTS.md says.
- For each security problem that you report and that main has too, say whether it's a vulnerability, as "Record a vulnerability privately" in `docs/releasing.md` defines one. Record each vulnerability that isn't public yet as that section describes, and give its GHSA ID in your report.
- Run `scripts/check` only on a commit that CI doesn't check, such as a fix in an advisory's private fork.
- If a maintainer opened the pull request and you need to run its code, such as to check a case, check it out in the worktree that you start in, never in the main checkout:

  ```sh
  git fetch origin pull/<number>/head
  git switch --detach FETCH_HEAD
  ```

  Don't use `gh pr checkout`, because auto mode sometimes denies it, and after a few denials auto mode stops to ask the maintainer. Once you've checked the code out, run only the tests that the case needs.
- If an error shows only with an input that someone would have to craft on purpose, and nobody outside the project can give that input, report the error as a note after the other errors, because it needn't block the merge. But if the pull request changes a hook, and the changed hook lets through a command that the hook on main blocks, report that as an error with the command as its case, even when only a command written on purpose would get through.
- Report to whoever asked for the review. Other than the checkout above and recording a vulnerability, don't change files or labels, make commits, or post comments or reviews. If you find no errors, say so in one line.

## Review the code for security problems

When a maintainer or the orchestrator asks you to review the code for security problems, review the whole repository, unless they name a part of it or a range of changes, such as the changes since the last release. For a range, also read the code that the changes call and the code that calls them. Start where the program decides who may do what, where it uses secrets or runs other programs, and where input from outside enters it, such as command-line arguments, files and network traffic. Include the workflows in `.github/`. Report each problem to whoever asked for the review, with how serious it is and the fix you recommend. Record each vulnerability that isn't public yet, as "Record a vulnerability privately" in `docs/releasing.md` describes. Say which parts you reviewed. Leave the fixes to the orchestrator.
