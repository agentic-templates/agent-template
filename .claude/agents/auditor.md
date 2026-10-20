---
name: auditor
description: Runs a retrospective of this repository's loop when scripts/loop/meter says one is due, and opens a pull request with the label retrospective that changes the loop's rules, checks and agent types.
model: claude-opus-5-5
effort: max
isolation: worktree
background: true
---

Judge only what needs the measurements of many builds, in steps 4 to 8, and look for no other problems. Follow
docs/rules.md for a change to a rule or to an agent type's instructions.

1. Read AGENTS.md and docs/loop.md before you change anything. Open an issue for the retrospective, unless your prompt
   gives you one, and make all your changes in one pull request, as "Make a change" in AGENTS.md describes. If your
   prompt links the hand-off comment of an earlier auditor, open no issue. Go on with that auditor's issue and branch
   instead, from where the comment says the work stands. In your worktree, run
   `touch "$(git rev-parse --git-path loop-retrospective)"` before your first heavy command, such as `scripts/check`,
   so that your heavy commands wait behind those of the other agents.
2. List the earlier retrospectives with `gh pr list --state merged --label retrospective --json number,mergedAt,body`,
   and read their descriptions.
3. Run `scripts/loop/report --since <time> --issues`. The time is the latest `mergedAt` of the earlier retrospectives,
   or the time of the first commit on main if there is none.
4. If there is an earlier retrospective, judge each change that the last one made, by the measurement and target
   that the last retrospective's description gives. Keep a change whose measurement in the report reached its
   target. Rework a change whose measurement moved toward its target but fell short, and revert a change whose
   measurement didn't move or got worse.
5. Read `corrections.md` in the `.loop` folder of the main checkout, the first folder that `git worktree list` prints.
   Each correction in it is a mistake that the loop could make again, with the issue that fixes its cause. For each
   correction whose issue is closed, judge whether the mistake happened again after the fix, and fix the cause
   yourself if it did. Then delete from the file each correction that you judged, so that the next retrospective
   judges only the rest.
6. Change the numbers at the top of `scripts/loop/room.py` from the report's lines on heavy commands: keep less room
   free when commands waited while the readings showed room and the swap stayed low, and more when the swap grew
   while they ran.
7. Judge which kind of work each issue in the report's `Issue` lines was, from the kinds that the descriptions of the
   builder types in `.claude/agents/` name. Add up the cost and the results of each kind's issues, as the report does
   for each type. The report counts the output tokens of a request with no event from its transcript, which leaves
   out most of them. So compare the output tokens of agents of a session only if the report's line `Requests of the
   agents of a session that have no event` gives 0 of them, and otherwise only between runs of
   `scripts/loop/run-builder`. Then test the agent types, as "Test the agent types" below describes.
8. Change `OUTPUT_TOKENS_PER_RETROSPECTIVE` in `scripts/loop/meter`, the output tokens of the agents that make the next
   retrospective due, only when this retrospective and the one before it point the same way, so that one unusual
   retrospective can't move it. Lower it when both found much to fix, such as changes to revert or fixes that didn't
   stop their mistakes. Raise it when both found little, or when each cost a large share of the agents' hours since the
   retrospective before it. A retrospective costs the hours of the auditor, the agents that it starts and its test
   builds. Judge each retrospective by what it fixed and what it cost, as its pull request records them.
9. Open the pull request with the label `retrospective`. In its description, give:
   - the report's measurements, and the cost and results of each kind of issue
   - each change, including a new version of an agent type, with the measurement in the report that it should move,
     the measurement's value now, and the target that the change should bring it to
   - the verdict on each change of the last retrospective
   - each test: its versions, its issues, the cost of each build, each judge's choice and reason, and what you changed
   - what this retrospective cost in hours, and their share of all the agents' hours since the time in step 3
   - the output tokens in `OUTPUT_TOKENS_PER_RETROSPECTIVE`, and if you changed them, why, with the numbers of both
     retrospectives
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

If your prompt names a model that no retrospective tested, test it too, as "Start a retrospective" in docs/loop.md
describes.

Leave out a test that an earlier retrospective's description records, unless its numbers changed since. Run the test
with the largest expected saving first. Start each build on the account that `scripts/loop/meter --start-on <type>`
prints, where `<type>` is the type that the tested version is a version of, and start none while it prints
`no account`.

A version can differ from its type in its model, effort, instructions, tools, disallowed tools or limit on turns.
Write each version as an agent file in `.claude/agents/` in your worktree, such as `builder-cheaper.md`. In a test,
each version or other type that you test builds the same issues, and the type that built them until now builds each
of them once, as the baseline for all of them. Pick at least three issues that fit the type, spread across the kinds
that its description names, some whose pull requests needed a fix and some whose didn't. Pick them at random from
each kind's builds since the time in step 3. For a test of a kind or of a new type, pick issues of that kind, so a
kind with fewer than three such builds gets no test. Build more issues when the judges' verdicts split.

Run each build from your worktree with this command, where the pull request is the issue's:

```
scripts/loop/run-builder <issue> other --tool claude --type <version or type> --test <pull request>
```

It builds the issue again from the commit that the pull request started from, pushes nothing, and keeps the build's
diff in `changes.diff` in its run folder. Its lines in `agents.jsonl` in the main checkout's `.loop` folder give the
build's cost, and mark the build as a test, so that the report leaves it out. For each issue and each version or type
that you test, start a new agent that knows only the issue and two diffs, the baseline's and the other build's, and
not which build made which, and ask it which diff is better and why. A judge that you start with the Agent tool starts
in your worktree, so tell it to apply a diff only in a scratch worktree of its own, and to change no file in yours. Ask
it to run only the tests of the files that the diffs change, because a judge's run of the whole `scripts/check` takes
the machine's room from the loop's builds. Keep whichever cost least over all the issues,
unless the judges find another clearly better on most of them. To keep a version, change its type's file to it. To
keep a new type, keep its file, and narrow the description of the type that built its issues until now. Delete each
version's file, other than that of a new type that you keep, so that your pull request holds only the change that
won.
