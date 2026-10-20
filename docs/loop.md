# Loop guide

## Start the loop or take it over

Find your case, from what the maintainer asks and what the session-start hook says:

- The maintainer asks you to start the loop. Do steps 1 to 5.
- The hook says "The Claude Code process of this repository's orchestrator has ended", and the maintainer asks you to
  continue its work. Do steps 1 to 5.
- The hook says "You are this repository's orchestrator, in a new session or process". Compare its `pid:` line with
  the state file's `pid:` line, and then write its `session:` and `pid:` lines in the state file:
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
4. Run `scripts/start-build-run`, which frees the issues of earlier builders.
5. Work through the cycle.

## Work through the cycle

Work through these steps each time you wake, for a message or because an agent or a background task ended.

Make a change of text yourself, such as a fix to a guide, when you already know each word of it. Run `scripts/check`
for it as a background task, and go on with the cycle while it runs. Give each change of code to a builder, even a
change of one line, because a failing check in code can take many runs to fix, and a builder's runs don't keep you
from acting.

To give a builder something, such as a correction or a reviewer's errors, send it a message. A builder that
`scripts/loop/run-builder` started can't take messages. Wait until its run ends, and then start a new builder with
the message, of the type `builder` unless a step below names another type. Choose its account as "Read the meter"
describes:

- On your own account, start it on the old builder's pull request, or on its issue if it opened none, and put the
  message in its prompt after the first line.
- On another account, write the message to a file in a folder of your own in `.loop/`. Then run the command that
  "Read the meter" gives for the account, with `--message <file>`. In place of its `<issue> <kind>`, give the `issue`
  and the `kind` of the old agent's start line in `.loop/agents.jsonl`, such as `12 left behind 34` for the issue 12
  and the kind `left behind 34`. Give `<issue> other` instead for a builder that fixes a reviewer's errors, or when the
  start line has no kind because the old builder started on a pull request. In these two cases, `<issue>` is the issue
  that the old builder's pull request closes.

Start the prompt of each agent of the types `builder`, `builder-small`, `builder-deep`, `researcher` and `reviewer`
with a line that names its work: for an issue, the line that `scripts/take-next-issue` printed, and for a pull
request, its number alone. `scripts/loop/agent-log` reads the agent's issue or pull request, and its kind, from that
line into `.loop/agents.jsonl`.

### Take in

- **Messages and mistakes.** Read the maintainer's messages in this session. Then note the time on the `taken in:`
  line of your state file, write `taken in: now` in its place, and read the issues and comments that the maintainer
  wrote on GitHub since the time you noted. Open or rewrite issues for each goal and idea, and for the cause of each
  correction, without stopping the builders that run. Treat each mistake that the loop could make again, whether
  yours or an agent's, as a correction, even when you fixed the mistake itself. Write each correction, with the issue
  for its cause, in `.loop/corrections.md`. Add the `priority` label to each issue for the cause of a correction, and
  to each issue that the maintainer asks to have built first. If a correction changes the issue of a running builder,
  give the builder the change.
- **Answers.** For each issue with `needs-decision` that the maintainer answered in a comment, carry out the answer,
  as "Labels" in `docs/planning.md` describes.
- **Other people's issues.** Triage them, as "Triage issues that others open" in `docs/planning.md` describes.
- **Reports.** When an agent ends, read its report, and act on what it asks of you. Of the items that it lists for the
  maintainer, decide each one that "Decide or ask" in AGENTS.md lets you decide and that no hook leaves to the
  maintainer. Ask about the rest as "Ask the maintainer" below describes.
- **Hand-offs.** When an agent reports that it handed off its work, start a new agent of the same type to take over,
  and give it a message, as the start of "Work through the cycle" describes:
  - For a builder, the auditor or a researcher, give the new agent the link to the hand-off comment. For a builder or
    the auditor, first run `scripts/clear-worktrees <issue>`, so that the new agent can check out the issue's branch.
    The auditor's issue is the one that it opened for the retrospective. If the script fails, start no new agent. Plan
    a builder's issue again instead, and for the auditor's issue, open an issue with `needs-decision` that asks the
    maintainer about the worktree that the script left.
  - For a reviewer, give the new reviewer the old reviewer's report.
- **Builders that stopped.** A builder that ends without a pull request, and without a report that says why, stopped
  before it finished, such as at a usage limit. If its issue is still open and assigned to you, run
  `scripts/clear-worktrees <issue>`, which saves its unfinished work on the issue's branch. Then:
  - If the script fails, plan the issue again, as "Plan" describes.
  - If the issue is on the state file's list of issues whose builder stopped, this is its second stop, so plan the
    issue again.
  - Otherwise, add the issue to that list, and remove your assignment with
    `gh issue edit <issue> --remove-assignee @me`, so that `scripts/take-next-issue` can give the issue to a new
    builder.

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
2. If the start of "Work through the cycle" says to make the issue's change yourself, make it in a worktree of your
   own, as "Make a change" in AGENTS.md describes.
3. Otherwise, choose the agent's type: `researcher` for a `research` issue, and otherwise the builder type whose
   description fits the change. Choose its account as "Read the meter" describes. If that's your own account, start a
   background agent of that type. Don't give it a model, so that it runs at its type's model and effort. After its
   prompt's first line, name the files to start from if you know them.

After each issue, go back to step 1. Start an auditor too when "Start a retrospective" says so, and a focused audit
when "Start a focused audit" says so.

### Land

- **Drafts.** For each draft pull request that the loop opened, and that no reviewer has read yet, start a
  `reviewer` agent with the pull request's number. Give each error that the reviewer reports to the builder that
  opened the draft, and ask it to fix each one, with a test when the error is in code. If you opened the draft, fix
  the errors yourself. If the draft changes code that the loop's safety depends on, which is the first kind in the
  description of `builder-deep`, start a new reviewer on it after each round of fixes, until a reviewer reports no
  error. When no error remains, run `gh pr ready <number>` and `gh pr merge <number>`.
  If it's the auditor's pull request, first add the label `retrospective` to it if it's missing, because the meter
  counts toward the next retrospective from the newest merged pull request with that label.
- **Stuck pull requests.** Run `scripts/merge-dependabot-pull-requests`, which turns on auto-merge for Dependabot's
  pull requests. Then run `scripts/stuck-pull-requests`. For each pull request that it prints:
  - If it's Dependabot's, look for an issue about it with `gh issue list --state all --search "#<number>"`. If there's
    none, open one that asks for a pull request of your own with the same update and the fixes that the update needs,
    and that names Dependabot's pull request as `#<number>`. Do nothing else with Dependabot's pull request.
  - If the script says that the merge queue removed it 2 times or more, plan its issue again.
  - If it's `BLOCKED`, look for it in the state file's list of `BLOCKED` pull requests from your last pass. If it's
    there, plan its issue again. If it isn't, leave it until your next pass.
  - For any other one that no builder is fixing yet, start a new agent of the type `builder` to fix it: to resolve
    its conflicts with main, fix its checks, or fix what made the merge queue remove it. Once it's fixed, add it to
    the merge queue again with `gh pr merge <number>`.

  Once you've gone through them all, replace the state file's list of `BLOCKED` pull requests with the ones that
  this pass found.
- **Merges.** While pull requests are merging, keep `scripts/wait-for-pull-requests` running as a background task,
  and start it again each time it ends. If the script says it can't fast-forward `main` in the main checkout, fix
  what it names, such as by switching the main checkout back to `main`. After each merge, run
  `scripts/remove-ended-worktrees`, which removes the worktree of each ended agent whose work is on GitHub.
- **Settings.** Once a pull request that changes `scripts/configure-github` merges, and the fast-forward is done, run
  `scripts/configure-github` in the main checkout, so that GitHub's settings match main.
- **Releases.** Once a goal is done, publish a release as "Publish a release" in `docs/releasing.md` describes, and
  don't wait for the maintainer to ask.

### Record

Bring `.loop/state.md` up to date after each launch, merge or decision, so that a `/clear` or a compaction can come
at any time. Besides the `session:` and `pid:` lines, it holds:

- the goal
- each running agent, with its issue or pull request, and its agent id or `scripts/loop/run-builder` task
- the drafts that a reviewer has read
- the issues whose builder stopped once
- the focused audits since the last auditor's pull request merged, each with its finding, its issue and the time it
  started
- the `BLOCKED` pull requests from your last pass
- the maintainer's decisions that no issue holds yet
- what you do next, and the open questions

It holds nothing that a query of GitHub gives, such as the ready issues, their blockers or what merged, and no lasting
rule, which belongs in a guide or a hook.

When an entry changes, rewrite it in place rather than add a new one. Delete an entry once it no longer tells you what
to do next, such as a pull request that merged or an agent that ended.

A hook writes the clock's time on the file's `updated:` line each time you write the file, on its `taken in:` line in
place of `now`, and on its `heartbeat:` line each time you schedule the heartbeat. Keep these times as the hook wrote
them. Once the `updated:` time is 6 days or more after the `heartbeat:` time, delete the heartbeat and schedule it
again, because a recurring prompt ends 7 days after you schedule it.

## Read the meter

Run `scripts/loop/meter` once in each pass, before you launch. For each account in `.loop/accounts.json`, it prints a
line that ends with the account's advice. Its last line starts with `machine`, and says whether a retrospective or a
focused audit is due.

`scripts/loop/meter --wake` ends, and so wakes you, when an account's advice changes, when a reading has a new finding
for a focused audit, and when a hook tells an agent to hand off its work. Start it again as a background task each time
it ends.

Your own account is the Claude account in `.loop/accounts.json` whose `env` values match this session's
environment, such as `CLAUDE_CONFIG_DIR`. Each account's advice says which agent types the account has room for, as
`scripts/loop/meter --help` describes:

- **go:** start an agent of any type.
- **a list of types,** such as `builder-small, researcher, reviewer`: start only agents of those types.
- **stop:** start nothing on the account.
- **parked:** start nothing on the account. If it's your own account, also delete the heartbeat, because the meter
  wakes you a minute after the reset instead. Schedule the heartbeat again then.
- **no reading:** start nothing on the account, and fix what the account's line names.

The line after the accounts starts with `start on`, and names the Claude account for each type, or `no account`.
Choose the account of each builder and researcher, and of each reviewer of a pull request, with
`scripts/loop/meter --start-on <type>`, and start the agent on the account that it prints. If it prints
`no account`, start the agent on a Codex account if one can take it, and otherwise start none. Start the auditor, and each reviewer that reviews no pull request, such as the reviewer of a
release, on your own account, as an agent of your session.

A Codex account can take an agent while its advice is `go`, unless the agent must open issues, as the researcher of a
focused audit must. Start the agent there, in place of running `--start-on`, when the Codex account's `week` percentage of use
is lower than that of the account that the `start on` line names for the agent's type. Each builder of the type
`builder-deep`, and each builder or researcher that takes over a hand-off, can run only on a Claude account.

To start an agent on another account, run one of these commands as a background task.

- `scripts/loop/run-builder <issue> <kind> --tool codex --account <name>` for an issue on a Codex account
- `scripts/loop/run-builder <issue> <kind> --tool claude --account <name> --type <type>` for an issue on a Claude
  account, with the type that fits the issue, as step 3 of "Launch" chooses it
- `scripts/loop/run-builder --review <pull request> --account <name>` for a reviewer of a pull request

If no account can take the issue that `scripts/take-next-issue` gave you, remove your assignment from it. Then run
the script again, followed by the number of each issue that no account can take, such as
`scripts/take-next-issue 12 15`, so that it skips them.

## Clear your context

Keep your shell in the main checkout, and run git in a worktree with `git -C <worktree>`, because the session-start
hook shows you your state file and this guide after a `/clear` or a compaction only when your shell is in the main
checkout.

A `/clear` sender is a hook in the maintainer's own Claude Code settings that types `/clear`, and then the request's
second line, into your session.

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
request is still open. When it names a new model behind an alias, such as `retrospective due: fable names
claude-fable-5-2, which no retrospective tested`, give the auditor the model's full ID in its prompt.

## Start a focused audit

For each part of the meter's `machine` line that starts with `audit due:`, start a focused audit of its finding,
without waiting for the next retrospective:

1. Skip a finding when the state file's list of focused audits has an audit of its subject, whether the audit's issue
   is open or closed. A finding's subject is one of these:
   - an issue's build, which the finding names by the builder's type and issue, such as `builder #208 took 41 min`
   - the waits of all heavy commands for room on the machine, in a finding that starts with `heavy commands waited`.
     For this subject, skip a finding only when the audit of the waits started in the last 24 hours.
   - the time of `scripts/check`, in a finding that starts with `scripts/check's passing runs`
2. Open an issue with the label `research` that quotes the finding, and asks the researcher to find its cause and to
   open an issue for each fix. Say in it that the logs are in the `.loop` folder of the main checkout. Add `ready` to
   it, and assign it to yourself, so that `scripts/take-next-issue` doesn't give it to anyone.
3. Start a `researcher` agent on it, as step 3 of "Launch" describes, with `<issue> research` as the line that names
   its work.
4. Add the audit to the state file's list of focused audits.
