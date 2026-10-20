# Review guide

## Review a pull request

- Compare the change with its issue's "Done when" list and the definition of done in AGENTS.md.
- Report only errors: wrong behavior, a missing test, a security problem, or text or code that breaks AGENTS.md or a guide in `docs/`. The text to check includes the pull request's description and each sentence that it adds to AGENTS.md, the README, a guide or an agent type in `.claude/agents`. Don't ask for changes that a formatter would make, or for work outside the issue.
- If your prompt names a round of fixes by its range of commits and gives the errors that the last reviewer reported, read only the changes in that range. Report each of those errors that the changes don't fix, and any other error in the changes. Report a problem that main has too, such as one that a fix copied from main, as an error only if it's a vulnerability, and otherwise in a list of its own after the errors.
- Give each error a case that shows it, such as an input and the wrong result it gives, or a sentence and the rule it breaks. Then say what to change and why. For a security problem, leave out the case, and describe the problem as AGENTS.md says.
- For each security problem that you report and that main has too, say whether it's a vulnerability, as "Record a vulnerability privately" in `docs/releasing.md` defines one. Record each vulnerability that isn't public yet as that section describes, and give its GHSA ID in your report.
- Run `scripts/check` only on a commit that CI doesn't check, such as a fix in an advisory's private fork.
- If a maintainer opened the pull request and you need to run its code, such as to try a case, check it out in the worktree that you start in, never in the main checkout:

  ```sh
  git fetch origin pull/<number>/head
  git switch --detach FETCH_HEAD
  ```

  Then run only the tests that the case needs.
- Report to whoever asked for the review. Other than the checkout above and recording a vulnerability, don't change files or labels, make commits, or post comments or reviews. If you find no errors, say so in one line.

## Review the code for security problems

When a maintainer or the orchestrator asks you to review the code for security problems, review the whole repository, unless they name a part of it or a range of changes, such as the changes since the last release. For a range, also read the code that the changes call and the code that calls them. Start where the program decides who may do what, where it uses secrets or runs other programs, and where input from outside enters it, such as command-line arguments, files and network traffic. Include the workflows in `.github/`. Report each problem to whoever asked for the review, with how serious it is and the fix you recommend. Record each vulnerability that isn't public yet, as "Record a vulnerability privately" in `docs/releasing.md` describes. Say which parts you reviewed. Leave the fixes to the orchestrator.
