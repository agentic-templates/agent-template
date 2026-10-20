# Building guide

## Build one issue

If the issue's branch ends in a commit with the subject `chore: save the unfinished work of a stopped session`, check each file that the commit adds or changes before you build, and undo each change that the issue doesn't need.

Build the issue by the kind that your prompt names:

- For `research`, don't open a pull request. Answer its question in a comment, with a recommendation and the evidence behind it. If you try code, put the short parts that show the answer in the comment. Next, go through the issues that the research issue blocks. Rewrite each one whose request changes because of the answer. If the answer leaves a question about one of them to the maintainer, as "Decide or ask" in AGENTS.md describes, write the question in that issue and add `needs-decision` to it. Then close the research issue, and name in your report the issues where you wrote a question.
- For `parent`, don't build anything. Close it if its "Done when" is met, and give it back if it isn't.
- For `left behind <pull request>`, finish that pull request: run `scripts/check` again, update its description, and turn on auto-merge with `gh pr merge <number>`.
- For `other`, make the change as steps 2 to 5 of "Make a change" in AGENTS.md describe.

Give the issue back when you can't finish it, such as when it needs a decision that "Decide or ask" in AGENTS.md leaves to the maintainer, or when it's against one of the rules in AGENTS.md or too big for one pull request. To give it back, close its pull request, if it has one, with `gh pr close <number>`. Leave the labels and the assignment of your issue as they are, and say in your report what the issue needs.

End with a report to the orchestrator that says how the issue ended, with the number of its pull request if it has one.
