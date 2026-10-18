# Planning guide

## Labels

- `needs-triage`: the issue forms add it to the issues that others open. Don't add it to an issue that a maintainer asked for, or that you create at triage or while planning.
- `needs-decision`: only for a question that the maintainer must answer, as "Decide or ask" in AGENTS.md describes. Write the question in the issue, with the option you recommend. When the maintainer decides, write the decision into the issue's body, and remove the label.

## Triage new ideas

Triage is for ideas: anything a maintainer wants judged before deciding whether to build it.

1. Take from the input the ideas that could change this project, and tell the maintainer in one line what you left out. Split them into single ideas. One idea is one result that someone wants. Merge ideas that ask for the same result.
2. Judge each idea on its merits, not on how much the maintainer seems to want it:
   - Does it fit the direction in "What this project is" in AGENTS.md?
   - Does an open or closed issue already cover it?
   - Can the project build it, and is it worth what it costs? Count what it adds for agents to read and run in later sessions, such as rules, checks and options.
   - Do its claims about the project, such as how the code works, match the code and AGENTS.md? If they don't, judge the idea with the claims corrected.
3. Give each idea one verdict:
   - **Accept:** the idea becomes an issue in the backlog, with `needs-breakdown` if it's too big for one pull request. If an open issue covers it, add the idea's new details to that issue instead. If a closed issue covered it, mention that issue in the new one.
   - **Research first:** the idea can't be judged until a question is answered. It becomes an issue with `needs-decision`, blocked by a new research issue for the question.
   - **Drop:** it doesn't fit the project's direction, can't be built, isn't worth its cost, or was closed as not planned before. Nothing is created.
   - **Ask:** only the maintainer can settle it, such as an idea too vague to judge, or one that needs a decision on taste or risk.
4. Show the maintainer a table of the ideas, verdicts and reasons, one line each. Carry out **Accept** and **Research first** right away. In the same message, ask about each **Drop** and **Ask**, with the answer you recommend. Then carry out what the maintainer decides.

Write each new issue in your own words. Leave out private details from the input. If part of the input comes from a public source, link that source instead of copying it.

If the maintainer's answers change the project's direction, open a pull request that updates "What this project is", as "Make a change" in AGENTS.md describes.

## Triage issues that others open

Triage every issue that has `needs-triage` or has no type label. Judge each issue as step 2 of "Triage new ideas" describes, and give it one of the verdicts below. Carry out **Duplicate**, **Question** and **Too vague** right away, and **Accept** for a bug that you can reproduce. Ask the maintainer about the others in one message, with your recommendations, and carry out what they decide.

- **Accept:** remove `needs-triage` and give the issue its type label, plus `needs-breakdown` if it's too big.
- **Research first:** give the issue its type label, and replace `needs-triage` with `needs-decision`. Create a research issue for the question, and mark this issue as blocked by it.
- **Needs a decision:** only the maintainer can settle it, and they want to decide later. Give the issue its type label, and replace `needs-triage` with `needs-decision`.
- **Too vague:** comment with a question that asks for the missing detail, and leave `needs-triage`. If nobody answers within two weeks, comment that the issue is closing for lack of detail, and close it as not planned.
- **Duplicate:** close it with `gh issue close <issue> --duplicate-of <other issue>`.
- **Question:** for a question about using the project, answer it in a comment, and close the issue. If the docs should have answered it, also open a `bug` about the docs.
- **Drop:** comment why, then close it with `gh issue close <issue> --reason "not planned"`.

If an issue came from an account that isn't a maintainer's, and its verdict is **Accept**, **Research first** or **Needs a decision**, carry out the verdict on a new issue instead of the original. Write the new issue in your own words, and include what the maintainer decided or the question that's still open. Then close the original with `gh issue close <issue> --duplicate-of <new issue>`, and add a comment that points its author to the new issue. If they opened a pull request for the original, also ask them to change its `Closes` line to the new issue.

## Break down a big idea

- A big idea is one parent issue. Its "What should change" states the result the maintainer wants, and its "Done when" says how to tell that the result is reached.
- Split it only when you make it ready to build.
- Split it into sub-issues that each fit in one pull request and leave main working. Create each one with `gh issue create --parent <parent>`, then remove `needs-breakdown` from the parent.
- Give the parent the `ready` label along with its sub-issues.

## Write a research issue

- A research issue's "Done when" asks for a comment with the answer, a recommendation and the evidence that the maintainer needs to decide.
- Each issue that depends on the answer is blocked by the research issue.

## Definition of ready

Every issue that gets the `ready` label, including research issues and parents, must come from a maintainer's account. A maintainer approves each one. The one exception is a bug that an agent finds and reproduces while it works on another issue: that agent marks it ready itself, as step 4 of "Make a change" in AGENTS.md describes.

An issue can get `ready` when all of these are true:

- It asks for one change that fits in one pull request.
- "Why" says who needs the change and what for.
- Someone could check each "Done when" item by running or looking at something. Where speed or accuracy matters, the item gives a number that the maintainer approved.
- Nothing is left that only the maintainer can decide.
- "What should change" says what's out of scope, if the agent that builds the issue might otherwise do more than the issue asks for.
- It's blocked by every issue whose change it needs first.
- It has no status label.

A research issue is ready when its question and what depends on the answer are clear, and it has no status label. A parent is ready when someone could check each of its "Done when" items, it has no status label, and each of its sub-issues is ready.

## Plan the next work

Plan all the issues that a goal needs before anyone builds them.

1. Agree on the goal with the maintainer: what a user can do afterwards that they can't do now.
2. Triage anything new, including open issues that need triage.
3. Choose the backlog issues that the goal needs, and leave out the rest. Add the issues the goal still lacks, including research issues for what someone has to find out, and break down any big issue you chose.
4. Link each issue to the issues whose change it needs first, with `gh issue edit <issue> --add-blocked-by <other issue>`. Link only real dependencies. Every blocker must be part of this plan, have the `ready` label already or be closed. A build run can build several issues at once, so only a link makes sure that one change comes before another.
5. Make every issue meet the definition of ready. Decide what you can, as "Decide or ask" in AGENTS.md describes.
6. Show the maintainer the issues as a list, one line each, with any question that only they can answer.
7. Add the `ready` label to each issue the maintainer approved, with `gh issue edit <issue> --add-label ready`.
