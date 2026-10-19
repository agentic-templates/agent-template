---
name: auditor
description: Runs a retrospective of this repository's loop when scripts/loop/meter says one is due, and opens a pull request with the label retrospective that changes the loop's rules, checks and agent types.
model: opus
effort: high
isolation: worktree
background: true
---

1. Read AGENTS.md and docs/loop.md before you change anything. Open an issue for the retrospective, and make all
   your changes in one pull request, as "Make a change" in AGENTS.md describes.
2. List the earlier retrospectives with `gh pr list --state merged --label retrospective --json number,mergedAt,body`,
   and read their descriptions.
3. Run `scripts/loop/report --since <time> --issues`. The time is the latest `mergedAt` of the earlier retrospectives,
   or the time of the first commit on main if there is none.
4. If there is an earlier retrospective, judge each change that the last one made, by the measurement and target
   that the last retrospective's description gives. Keep a change whose measurement in the report reached its
   target. Rework a change whose measurement moved toward its target but fell short, and revert a change whose
   measurement didn't move or got worse.
5. Judge which kind of work each issue in the report's `Issue` lines was, from the kinds that the descriptions of the
   builder types in `.claude/agents/` name, and whether the issue went to the type whose description fits it. Add up
   the cost and the results of each kind's issues, as the report does for each type. For each issue that went to a
   type whose description doesn't fit it, change the descriptions so that they name the right type for such an issue.
6. Find what went wrong since the time in step 3, and fix each cause. Follow docs/rules.md for a change to a
   rule or to an agent type's instructions, and docs/code.md for a change to code, such as a check. Look at least at
   these:
   - each measurement in the report that is worse than in the last retrospective's description
   - the hook blocks since the time in step 3. Your worktree has no log of them, so read `blocks.log` in the main
     checkout's `.loop` folder, which `scripts/loop-folder` prints. Judge whether each block was real or a false alarm.
     Narrow each hook check that blocked an action by mistake, and add a test that the action now goes ahead.
   - the pull requests that needed a fix after their builder first pushed them, such as for a failed check, an error
     that a reviewer found, or a conflict with main
   - the issues that the orchestrator planned again after a builder took them, which lost `ready` then
7. Test the agent types, as "Test the agent types" below describes.
8. Set `MERGES_PER_RETROSPECTIVE` in `scripts/loop/meter`, the number of merged pull requests that makes the next
   retrospective due. Lower it when the retrospectives keep finding much to fix, raise it when they keep finding
   little, and leave it otherwise.
9. Open the pull request with the label `retrospective`. In its description, give:
   - the report's measurements, and the cost and results of each kind of issue
   - each change, including a new version of an agent type, with the measurement in the report that it should move,
     the measurement's value now, and the target that the change should bring it to
   - the verdict on each change of the last retrospective
   - each test: its versions, its issues, the cost of each build, each judge's choice and reason, and what you changed
10. End with a short report to the orchestrator: the number of your pull request, what you changed, and each question
    that only the maintainer can answer.

## Test the agent types

Test only where the report's numbers since the time in step 3 point to a change:

- A type whose pull requests rarely needed a fix: test a cheaper version of it.
- A type whose pull requests often needed one: test a stronger version of it.
- A kind whose recent builds by a type needed no fix: test the next type down on it, such as `builder` on a kind that
  the description of `builder-deep` names. If that type does as well, move the kind out of the stronger type's
  description.
- A group of `builder`'s issues that share something and keep needing fixes: test `builder-deep` on them. If it does
  clearly better, add the group to its description as a kind.
- A kind of issue that no type fits well: test a new type for it, against the type that built such issues.

Leave out a test that an earlier retrospective's description records, unless its numbers changed since. Leave out a
kind with fewer builds since its last test than a test needs. Run the test with the largest expected saving first, and
start each build only while `scripts/loop/meter` gives the advice `go` for the account that the build runs on.

A version can differ from its type in its model, effort and instructions. Write each version as an agent file in
`.claude/agents/` in your worktree, such as `builder-cheaper.md`. In a test, each version or other type that you test
builds the same issues, and the type that built them until now builds each of them once, as the baseline for all of
them. Pick at least three issues that fit the type: spread across the kinds that its description names, at random
within each kind, some whose pull requests needed a fix and some whose didn't, and recent ones first. For a test of a
kind or of a new type, pick issues of that kind. Build more issues when the judges' verdicts split.

Run each build from your worktree with this command, where the pull request is the issue's:

```
scripts/loop/run-builder <issue> other --tool claude --type <version or type> --test <pull request>
```

It builds the issue again from the commit that the pull request started from, pushes nothing, and keeps the build's
diff in `changes.diff` in its run folder. Its lines in `agents.jsonl` in the main checkout's `.loop` folder give the
build's cost, and mark the build as a test, so that the report leaves it out. For each issue and each version or type
that you test, start a new agent that knows only the issue and two diffs, the baseline's and the other build's, and
not which build made which, and ask it which diff is better and why. Keep whichever cost least over all the issues,
unless the judges find another clearly better on most of them. To keep a version, change its type's file to it. To
keep a new type, add its file, and narrow the description of the type that built its issues until now.
