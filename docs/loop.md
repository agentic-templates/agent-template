# Loop guide

## Start the loop or take it over

Find your case, from what the maintainer asks and what the session-start hook says:

- The maintainer asks you to start the loop. Do steps 1 to 5.
- You're another session, the hook says "The Claude Code process of this repository's orchestrator has ended", and
  the maintainer asks you to continue its work. Take the loop over: do steps 1 to 5.
- The hook says "You are this repository's orchestrator, in a new session or process". Compare the `pid:` line that
  it gives with the state file's. Then write the `session:` and `pid:` lines that it gives, in place of step 1:
  - If they differ, as after `scripts/loop/claude-with-events --resume`, the old process's agents and background
    tasks have ended. Do steps 3 to 5.
  - If they match, as after `/clear`, your agents and background tasks may still run. Do step 3, because nothing
    tells you whether the meter still runs, and go on with the cycle from your state file.

  In both cases, do step 2 too, unless CronList still lists the heartbeat.
- The hook says "You are this repository's orchestrator. Continue the loop", as after a compaction. Go on with the
  cycle from your state file.

The steps:

1. Run `echo $CLAUDE_CODE_SESSION_ID $CLAUDE_PID`, and write the two ids on the `session:` and `pid:` lines of
   `.loop/state.md` in the main checkout.
2. Schedule this heartbeat to run every hour:

   ```
   Loop heartbeat: if your last turn stopped at a usage limit, or nothing runs while work remains, resume the loop from .loop/state.md. Otherwise reply in one line with what still runs.
   ```

3. Start `scripts/loop/meter --wake` as a background task.
4. Run `scripts/start-build-run`, which frees the issues of earlier builders, and saves their unfinished work.
5. Work through the cycle.

## Work through the cycle

Work through these steps each time you wake, for a message or because an agent or a background task ended.

To give a builder something, such as a correction or a reviewer's errors, send it a message. A builder that
`scripts/loop/run-builder` started can't take messages. Wait until its run ends, and then start a new agent of the
type `builder` on its pull request instead, or on its issue if it opened none.

Start the prompt of each agent of the types `builder`, `builder-small`, `builder-deep`, `researcher` and `reviewer`
with a line that names its work: for an issue, the line that `scripts/take-next-issue` printed, and for a pull
request, its number alone. `scripts/loop/report` reads that line to tell which issue or pull request each agent worked
on.

### Take in

- **Messages and mistakes.** Read the maintainer's messages in this session. Then note the time on the `taken in:`
  line of your state file, write `taken in: now` in its place, and read the issues and comments that the maintainer
  wrote on GitHub since the time you noted. Open or rewrite issues for each goal and idea, and for the cause of each
  correction, without stopping the builders that run. Treat each mistake of your own or of an agent that the loop could
  make again, such as a time that you guessed, as a correction, even when you fixed the mistake itself. If a
  correction changes the issue of a running builder, give the builder the change.
- **Answers.** For each issue with `needs-decision` that the maintainer answered in a comment, carry out the answer,
  as "Labels" in `docs/planning.md` describes.
- **Other people's issues.** Triage them, as "Triage issues that others open" in `docs/planning.md` describes.
- **Reports.** When an agent ends, read its report, and act on what it asks of you. Of the items that it lists for the
  maintainer, decide each one that "Decide or ask" in AGENTS.md lets you decide and that no hook leaves to the
  maintainer. Ask about the rest as "Ask the maintainer" below describes.
- **Hand-offs.** When a builder or the auditor reports that it handed off its work, because its context grew too
  long, run `scripts/clear-worktrees <issue>`, so that a new agent can check out the issue's branch. The auditor's
  issue is the one that it opened for the retrospective. If the script exits with a code other than 0, it left the
  worktree, and with it the issue's branch, in place. Then plan a builder's issue again, and for the auditor's issue,
  ask the maintainer about the worktree in an issue with `needs-decision`. If the script exits with 0, start a new
  agent of the same type with the same prompt, and put the link to the hand-off comment in that prompt. Start it in
  your session once your own account's advice lets you, because `scripts/loop/run-builder` can't give a builder the
  link.
- **Builders that stopped.** A builder that ends without a pull request, and without a report that says why, stopped
  before it finished, such as at a usage limit. If its issue is still open and assigned to you, run
  `scripts/clear-worktrees <issue>`, which saves its unfinished work on the issue's branch and removes its worktree.
  Then:
  - If the script exits with a code other than 0, it left the worktree, and with it the issue's branch, in place.
    Plan the issue again, as "Plan" describes.
  - If the issue is on the state file's list of issues whose builder stopped, this is its second stop. Plan the issue
    again, and take it off the list.
  - Otherwise, add the issue to that list, and remove your assignment with
    `gh issue edit <issue> --remove-assignee @me`, so that `scripts/take-next-issue` can give the issue to a new
    builder, which starts from the saved work.

### Plan

- Give a goal that needs more than one pull request a parent issue, and split it, as "Break down a big idea" in
  `docs/planning.md` describes.
- Put each question that someone must find out before building in a research issue, as "Write a research issue" in
  `docs/planning.md` describes.
- Add `ready` to each issue that meets "Definition of ready" in `docs/planning.md`.
- To plan an issue again, remove `ready` and your assignment with
  `gh issue edit <issue> --remove-label ready --remove-assignee @me`, and close its pull request if it has one. Then
  split the issue, clarify it, or ask about it.

### Launch

Start an agent for each ready issue, as far as "Read the meter" allows:

1. Run `scripts/take-next-issue`. It assigns you the next ready issue that can start, and prints its number and its
   kind, such as `12 other` or `13 research`. If it prints `none`, go on with "Land".
2. If you know that the issue's change is one line, make it yourself, in a worktree of your own, as "Make a change"
   in AGENTS.md describes.
3. Otherwise, choose the agent's type: `researcher` for a `research` issue, and otherwise the builder type whose
   description fits the change. Choose its account as "Read the meter" describes. If that's your own account, start a
   background agent of that type. Don't give it a model, so that it runs at its type's model and effort. After its
   prompt's first line, name the files to start from if you know them.

After each issue, go back to step 1. Start an auditor too when "Start a retrospective" says so.

### Land

- **Drafts.** For each draft pull request that the loop opened, and that no reviewer has read yet, start a
  `reviewer` agent with the pull request's number. Give each error that the reviewer reports to the builder that
  opened the draft, and ask it to fix each one, with a test when the error is in code. If you opened the draft, fix
  the errors yourself. If the draft changes code that the loop's safety depends on, which is the first kind in the
  description of `builder-deep`, start a new reviewer on it after each round of fixes, until a reviewer reports no
  error. When no error remains, run `gh pr ready <number>` and `gh pr merge <number>`.
  If it's the auditor's pull request, first add the label `retrospective` to it if it's missing, because the meter
  counts the hours that the agents worked since the newest merged pull request with that label.
- **Stuck pull requests.** Run `scripts/merge-dependabot-pull-requests`, which turns on auto-merge for Dependabot's
  pull requests. Then run `scripts/stuck-pull-requests`. For each pull request that it prints:
  - If it's Dependabot's, look for an issue about it with `gh issue list --state all --search "#<number>"`. If there's
    none, open one that asks for a pull request of your own with the same update and the fixes that the update needs,
    and that names Dependabot's pull request as `#<number>`. Do nothing else with Dependabot's pull request.
  - If the script says that the merge queue removed it 2 times or more, plan its issue again.
  - If it's `BLOCKED` after all its checks passed, and it isn't in the merge queue, look for it in the state file's
    list of `BLOCKED` pull requests from your last pass. If it's there, plan its issue again. If it isn't,
    leave it until your next pass.
  - For any other one that no builder is fixing yet, start a new agent of the type `builder` to fix it: to resolve
    its conflicts with main, fix its checks, or fix what made the merge queue remove it. Once it's fixed, add it to
    the merge queue again with `gh pr merge <number>`.

  Once you've gone through them all, replace the state file's list of `BLOCKED` pull requests with the ones that
  this pass found.
- **Merges.** While pull requests are merging, keep `scripts/wait-for-pull-requests` running as a background task,
  and start it again each time it ends. When a pull request merges, the script fast-forwards `main` in the main
  checkout to `origin/main`, so that you and the agents you start get the hooks, scripts and agent types that merged.
  If the script says it can't fast-forward, fix what it names, such as by switching the main checkout back to `main`.
- **Settings.** Once a pull request that changes `scripts/configure-github` merges, and the fast-forward is done, run
  `scripts/configure-github` in the main checkout, so that GitHub's settings match main.
- **Releases.** Once a goal is done, publish a release as "Publish a release" in `docs/releasing.md` describes, and
  don't wait for the maintainer to ask. A goal is done when its issue closes as completed. For a goal of more than one
  pull request, that's its parent issue.

### Record

Bring `.loop/state.md` up to date after each launch, merge or decision, so that a compaction can come at any time.
Besides the `session:` and `pid:` lines, it holds:

- the goal
- each running agent, with its issue or pull request, and its agent id or `scripts/loop/run-builder` task
- the drafts that a reviewer has read
- the issues whose builder stopped once
- the focused audits, each with its finding and its issue
- the `BLOCKED` pull requests from your last pass
- what you do next, and the open questions

A hook writes the clock's time into the file: on its `updated:` line each time you write the file, on its `taken in:`
line each time you write `now` there, and on its `heartbeat:` line each time you schedule the heartbeat. Keep these
times as the hook wrote them. Once the `updated:` time is 6 days or more after the `heartbeat:` time, delete the
heartbeat and schedule it again, because a recurring prompt ends 7 days after you schedule it.

## Read the meter

For each account in `.loop/accounts.json`, `scripts/loop/meter` prints a line that ends with the account's advice.
The meter's last line starts with `machine`, and gives your context size and whether a retrospective is due. Each
part of it that starts with `audit due:` names a finding for a focused audit. The line before it starts with
`agents`, and gives the context size of each agent that runs. Run the meter once in each pass, before you launch.

`scripts/loop/meter --wake` ends, and so wakes you, when an account's advice changes, and when a hook tells an agent
to hand off its work, so that you can plan the agent that takes over. Start it again as a background task each time it
ends.

Your own account is the Claude account in `.loop/accounts.json` whose `env` values match this session's
environment, such as `CLAUDE_CONFIG_DIR`. In "Launch", for the builder or researcher of each issue that
`scripts/take-next-issue` gives you, choose the account whose line has the lowest `week` percentage, among the
accounts whose advice lets you start the agent. A Codex account builds only the kinds `research` and `other`. Start
a builder of the type `builder-deep` only on a Claude account. Start reviewers, the auditor and each builder that needs
more than its issue, such as one that fixes a pull request or takes over a hand-off, on your own account, in your
session. `scripts/loop/run-builder` can't start such a builder, because it gives a builder only its issue's text. Each
account's advice says what you can start on that account:

- **go:** start any agent.
- **watch:** keep the number of agents that run on the account from growing, and start no auditor.
- **near:** start only reviewers, and agents for the issues that fit the types `builder-small` or `researcher`.
- **stop:** start nothing on the account.
- **parked:** start nothing on the account. If it's your own account, also delete the heartbeat, because the meter
  wakes you a minute after the reset instead. Schedule the heartbeat again then.
- **no reading:** start nothing on the account, and fix what the account's line names, or ask the maintainer if the
  fix needs them.

To start an agent for an issue on another account, run one of these commands as a background task, with the line
that `scripts/take-next-issue` printed as `<issue> <kind>`. Its last line says how the run ended, and where the
agent's report is.

- `scripts/loop/run-builder <issue> <kind> --tool codex --account <name>` for a Codex account
- `scripts/loop/run-builder <issue> <kind> --tool claude --account <name> --type <type>` for a Claude account, with
  the type that fits the issue, as step 3 of "Launch" chooses it

If no account can take the issue that `scripts/take-next-issue` gave you, remove your assignment from it. Then run
the script again, followed by the number of each issue that no account can take, such as
`scripts/take-next-issue 12 15`, so that it skips them.

## Compact your context

Keep your shell in the main checkout, and run git in a worktree with `git -C <worktree>`. After a compaction, the
session-start hook shows you your state file only when your shell is in the main checkout.

When `scripts/hooks/hand-off` tells you that your context has grown long enough to compact, ask for a compaction if the
maintainer installed a compaction sender. That's a hook in the maintainer's own Claude Code settings that runs on both
`Stop` and `StopFailure`, and types `/compact` when it finds `.loop/compact-request.txt`. Finish the step you're on,
such as landing a pull request or starting a builder, and bring your state file up to date. Then write
`.loop/compact-request.txt` with your session id on its first line and `Resume from .loop/state.md.` on its second, and
end your turn. Without a sender, ask the maintainer to type `/compact`, and go on with the loop.

## Ask the maintainer

Ask the maintainer each question that only they can answer, in an issue with `needs-decision`, as "Labels" in
`docs/planning.md` describes, and keep working on the rest.

When a hook or the permission classifier blocks something that the loop needs, don't work around it. Ask in the
same way for the permission that lets the loop do it itself.

Each time you write a question for the maintainer in an issue with `needs-decision`, or an agent's report names an
issue where it wrote one, send the maintainer a push notification with the issue's title and link, using the
`PushNotification` tool.

## Start a retrospective

When the meter's `machine` line says `retrospective due`, start an `auditor` agent, unless one runs or its pull
request is still open.

## Start a focused audit

For each finding after `audit due:` on the meter's `machine` line, start a focused audit, without waiting for the next
retrospective:

1. Skip a finding if the state file lists an audit whose finding has the same subject, and whose issue is still open.
   A finding's subject is one of these:
   - a build, which the finding names by the builder's type and issue, such as `builder #208`
   - the builders' waits for room on the machine
   - the time of `scripts/check`
2. Open an issue with the label `research` that quotes the finding, and asks the researcher to find its cause and to
   open an issue for each fix. Say in it that the logs are in the `.loop` folder of the main checkout. Add `ready` to
   it, because agents take instructions only from issues with that label. Assign it to yourself, so that
   `scripts/take-next-issue` doesn't give it to anyone.
3. Start a `researcher` agent on it, as step 3 of "Launch" describes, with `<issue> research` as the line that names
   its work.
4. Add the audit to the state file's list of focused audits.

A focused audit doesn't count as a retrospective, so the meter goes on counting toward the next one.
