# Loop guide

## Start the loop or take it over

Find your case, from what the maintainer asks and what the session-start hook says:

- The maintainer asks you to start the loop. Do steps 1 to 5.
- You're another session, the hook says "The Claude Code process of this repository's orchestrator has ended", and
  the maintainer asks you to continue its work. Take the loop over: do steps 1 to 5.
- The hook says "You are this repository's orchestrator, in a new session or process". Compare the `pid:` line that
  it gives with the state file's. Then write the `session:` and `pid:` lines that it gives, in place of step 1:
  - If they differ, as after `claude --resume`, the old process's agents and background tasks have ended. Do steps 3
    to 5.
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

- **Messages.** Read the maintainer's messages in this session, and the issues and comments that they wrote on GitHub
  since your last pass. Open or rewrite issues for each goal, idea or correction, without stopping the builders that
  run. If a correction changes the issue of a running builder, give the builder the change.
- **Answers.** For each issue with `needs-decision` that the maintainer answered in a comment, carry out the answer,
  as "Labels" in `docs/planning.md` describes.
- **Other people's issues.** Triage them, as "Triage issues that others open" in `docs/planning.md` describes.
- **Reports.** When an agent ends, read its report, and act on what it asks of you. For each item that it lists for
  the maintainer, such as a command that a hook told it to leave to them, ask the maintainer in an issue with
  `needs-decision`.
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
3. Otherwise, start a background agent of the type that fits the issue: `researcher` for a `research` issue, and
   otherwise the builder type whose description fits the change. Don't give it a model, so that it runs at its type's
   model and effort. After its prompt's first line, name the files to start from if you know them.

After each issue, go back to step 1. Start an auditor too when "Start a retrospective" says so.

### Land

- **Drafts.** For each draft pull request that the loop opened, and that no reviewer has read yet, start a
  `reviewer` agent with the pull request's number. Give each error that the reviewer reports to the builder that
  opened the draft, and ask it to fix each one, with a test when the error is in code. If you opened the draft, fix
  the errors yourself. Nobody reviews the fixes again. Once the errors are fixed, or if the reviewer reports none, run
  `gh pr ready <number>` and `gh pr merge <number>`. If it's the auditor's pull request, first add the label
  `retrospective` to it if it's missing, because the meter counts the hours that the agents worked since the newest
  merged pull request with that label.
- **Stuck pull requests.** Run `scripts/stuck-pull-requests`. For each pull request that it prints:
  - If it's Dependabot's, leave it for the maintainer.
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
  and start it again each time it ends.

### Record

Bring `.loop/state.md` up to date after each launch, merge or decision, so that a compaction can come at any time.
Besides the `session:` and `pid:` lines, it holds:

- the goal
- the time of your last pass, and when you scheduled the heartbeat
- each running agent, with its issue or pull request, and its agent id or `scripts/loop/run-builder` task
- the drafts that a reviewer has read
- the issues whose builder stopped once
- the `BLOCKED` pull requests from your last pass
- what you do next, and the open questions

Once the heartbeat is 6 days old, delete it and schedule it again, because a recurring prompt ends 7 days after you
schedule it.

## Read the meter

For each account in `.loop/accounts.json`, `scripts/loop/meter` prints a line that ends with the account's advice.
Its last line starts with `machine`, and gives your context size and whether a retrospective is due. Run it once in
each pass, before you launch.

`scripts/loop/meter --wake` ends, and so wakes you, when an account's advice changes. Start it again as a background
task each time it ends.

Your own account is the Claude account in `.loop/accounts.json` whose `env` values match this session's
environment, such as `CLAUDE_CONFIG_DIR`. Start an agent on another account only when your own account's advice
doesn't let you start it. Reviewers and the auditor run only on your own account. On each account, follow its own
advice:

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

- `scripts/loop/run-builder <issue> <kind> --tool codex --account <name>` for a Codex account, which builds only the
  kinds `research` and `other`
- `scripts/loop/run-builder <issue> <kind> --tool claude --account <name> --type <type>` for a Claude account, with
  the type that fits the issue, as step 3 of "Launch" chooses it

If no account can take the issue that `scripts/take-next-issue` gave you, remove your assignment from it. Then run
the script again, followed by the number of each issue that no account can take, such as
`scripts/take-next-issue 12 15`, so that it skips them.

## Compact your context

Keep your shell in the main checkout, and run git in a worktree with `git -C <worktree>`. After a compaction, the
session-start hook shows you your state file only when your shell is in the main checkout.

Once your context passes about 300k tokens, as the meter's `machine` line shows, ask for a compaction if the maintainer
installed a compaction sender. That's a Stop hook in the maintainer's own Claude Code settings that types `/compact`
when it finds `.loop/compact-request.txt`. Finish the step you're on, such as landing a pull request or starting a
builder. Then write `.loop/compact-request.txt` with your session id on its first line and `Resume from .loop/state.md.`
on its second, and end your turn.

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
