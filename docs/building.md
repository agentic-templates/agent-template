# Building guide

## Build one issue

Build the issue by the kind that `scripts/take-next-issue` printed:

- For `research`, don't open a pull request. Answer its question in a comment, with a recommendation and the evidence behind it. If you need to try code, do it in a temporary folder, such as one that `mktemp -d` makes, and put the short parts that show the answer in the comment. Check each issue that the research issue blocks. If the answer changes what that issue asks for, rewrite the issue to match. Decide what you can about that issue, as "Decide or ask" in AGENTS.md describes. Write each question that only the maintainer can answer in that issue, and add `needs-decision` to it. Then close the research issue, and name in your report the issues where you wrote a question.
- For `parent`, don't build anything. Close it if its "Done when" is met, and give it back if it isn't.
- For `left behind <pull request>`, finish that pull request: run `scripts/check` again, update its description, and turn on auto-merge with `gh pr merge <number>`.
- For `other`, make the change as steps 2 to 5 of "Make a change" in AGENTS.md describe.

If you build on a branch that ends in a commit with the subject `chore: save the unfinished work of a stopped session`, check each file the commit adds or changes, and undo each change that the issue doesn't need.

Give the issue back when you can't finish it, such as when it needs a decision that "Decide or ask" in AGENTS.md leaves to the maintainer, or when it's against one of the rules in AGENTS.md or too big for one pull request. To give it back, close the issue's pull request, if it has one, with `gh pr close <number>`, which keeps the branch for the next builder. Then stop, and say in your report what the issue needs. Leave the issue's labels and its assignment as they are, because the orchestrator plans the issue again.

End with a report to the orchestrator that says how the issue ended, with the number of its pull request if it has one.
