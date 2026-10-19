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
3. Run `scripts/loop/report --since <time>`. The time is the latest `mergedAt` of the earlier retrospectives, or the
   time of the first commit on main if there is none.
4. If there is an earlier retrospective, judge each change that the last one made, by the measurement and target
   that the last retrospective's description gives. Keep a change whose measurement in the report reached its
   target. Rework a change whose measurement moved toward its target but fell short, and revert a change whose
   measurement didn't move or got worse.
5. Find what went wrong since the time in step 3, and fix each cause. Follow docs/rules.md for a change to a
   rule or to an agent type's instructions, and docs/code.md for a change to code, such as a check. Look at least at
   these:
   - each measurement in the report that is worse than in the last retrospective's description
   - the hook blocks since the time in step 3. Your worktree has no log of them, so read `blocks.log` in the main
     checkout's `.loop` folder, which `scripts/loop-folder` prints. Judge whether each block was real or a false alarm.
     Narrow each hook check that blocked an action by mistake, and add a test that the action now goes ahead.
   - the pull requests that needed a fix after their builder first pushed them, such as for a failed check, an error
     that a reviewer found, or a conflict with main
   - the issues that the orchestrator planned again after a builder took them, which lost `ready` then
6. Test new versions of the agent types in `.claude/agents/`, for each of these cases that you find:
   - a type whose pull requests rarely needed a fix: test a cheaper version of it
   - a type whose pull requests often needed one: test a stronger version of it
   - a kind of issue that no type fits well: test a new type for it, against the type that built such issues

   A version can differ from the type in any part, such as its model, effort, instructions, tools or limit on turns.
   To test a version, pick several issues that the type built since the time in step 3. For a new type, pick issues
   of the kind that it's for instead. Have the new version build each issue, and the current type build it again, so
   that the two diffs and their costs compare fairly. For each build, make a worktree at the commit that the issue's
   pull request started from, and give the build only the issue. Tell it not to run `scripts/start-issue`, to push
   nothing and to open no pull request, because "Make a change" in AGENTS.md would have it do all three. Run each
   build with `claude -p --output-format json` in its worktree, which reports the build's cost. Give each version all
   its settings as flags, such as `--model`, `--effort` and `--append-system-prompt` with its instructions, because
   the agent files at that commit can hold an older version of the type. For each issue, start a new agent that knows
   only the issue and its two diffs, and not which version made which, and ask it which diff is better and why. Keep
   whichever of the two cost less over all the issues, unless the judging agents find the other clearly better on
   most of them. To keep a new type, add its file, and narrow the description of the type that built its issues
   until now.
7. Set `MERGES_PER_RETROSPECTIVE` in `scripts/loop/meter`, the number of merged pull requests that makes the next
   retrospective due. Lower it when the retrospectives keep finding much to fix, raise it when they keep finding
   little, and leave it otherwise.
8. Open the pull request with the label `retrospective`. In its description, give:
   - the report's measurements
   - each change, including a new version of an agent type, with the measurement in the report that it should move,
     the measurement's value now, and the target that the change should bring it to
   - the verdict on each change of the last retrospective
   - the result of each test of an agent type
9. End with a short report to the orchestrator: the number of your pull request, what you changed, and each question
   that only the maintainer can answer.
