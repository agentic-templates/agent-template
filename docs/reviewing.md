# Review guide

## Review a pull request

- Compare the change with its issue's "Done when" list and the definition of done in AGENTS.md.
- Report only what needs to change: wrong behavior, a missing test, a security problem, or text and code that break the guides. For each one, say what to change and why.
- Don't ask for changes that a formatter would make, or for work outside the issue.
- Write the review to a file outside the repository with your tool for writing files, not with a shell command. Post it with `gh pr review <number> --comment --body-file <file>`.

## Review the code for security problems

When a maintainer asks you to review the code for security problems, review the whole repository, unless they name a part of it or a range of changes, such as the changes since the last release. For a range, also read the code that the changes call and the code that calls them. Start where the program decides who may do what, where it uses secrets or runs other programs, and where input from outside enters it, such as command-line arguments, files and network traffic. Include the workflows in `.github/`. Report each problem to the maintainer, with how serious it is and the fix you recommend. Record each vulnerability that isn't public yet, as "Record a vulnerability privately" in `docs/releasing.md` describes. Say which parts you reviewed. Don't fix anything until the maintainer asks you to.
