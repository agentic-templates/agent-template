# Review guide

## Review a pull request

- Compare the change with its issue's "Done when" list and the definition of done in AGENTS.md.
- Report only errors: wrong behavior, a missing test, a security problem, or text or code that breaks AGENTS.md or a guide in `docs/`. Don't ask for changes that a formatter would make, or for work outside the issue.
- Give each error a case that shows it, such as an input and the wrong result it gives, or a sentence and the rule it breaks. Then say what to change and why. For a security problem, leave out the case, and describe the problem as AGENTS.md says.
- For each security problem that you report and that main has too, say whether it's a vulnerability or only hardening, as "Record a vulnerability privately" in `docs/releasing.md` tells them apart. Record each vulnerability that isn't public yet as that section describes, and give its GHSA ID in your report.
- If an error shows only with an input that someone would have to craft on purpose, and nobody outside the project can give that input, report the error as a note after the other errors, because it needn't block the merge.
- Report to whoever asked for the review. Don't change files or labels, make commits, or post comments or reviews. If you find no errors, say so in one line.

## Review the code for security problems

When a maintainer or the orchestrator asks you to review the code for security problems, review the whole repository, unless they name a part of it or a range of changes, such as the changes since the last release. For a range, also read the code that the changes call and the code that calls them. Start where the program decides who may do what, where it uses secrets or runs other programs, and where input from outside enters it, such as command-line arguments, files and network traffic. Include the workflows in `.github/`. Report each problem to whoever asked for the review, with how serious it is and the fix you recommend. Record each vulnerability that isn't public yet, as "Record a vulnerability privately" in `docs/releasing.md` describes. Say which parts you reviewed. Leave the fixes to the orchestrator.
