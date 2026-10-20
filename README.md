# agent-template

Start a GitHub project that an orchestrator plans, builds and releases on its own, from the goals you give it. The orchestrator is one Claude Code session that you talk to. It comes to you only for the decisions that the "Decide or ask" section of [AGENTS.md](AGENTS.md) lists, such as spending money or choosing a name.

It works with any language. The agents that build issues run in Claude Code or Codex.

## Set up a project

You need:

- [Claude Code](https://code.claude.com/docs/en/overview)
- The GitHub CLI
- bash
- [jq](https://jqlang.org/download/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

1. If you don't have a GitHub organization, create one at https://github.com/account/organizations/new.
2. Create a public repository from the template, and go into its clone:

   ```bash
   gh repo create <organization>/expense-tracker --public --template agentic-templates/agent-template --clone
   cd expense-tracker
   ```

3. Start Claude Code there, and ask it to set up the project, with any notes or spec you have: "Set up this repository for expense-tracker, a command-line tool in Python that records what you spend. Follow the setup list in README.md."

   <details>
   <summary>The setup list, for your agent</summary>

   - Run `scripts/install-git-hooks`, and ask the person which folders outside the repository are private, such as notes or other projects. List them in `.loop/leak-scan.paths`, so that the leak scan blocks any push that would publish their paths or contents.
   - Run `gh api "repos/{owner}/{repo}/rulesets?includes_parents=false"` as written, because gh fills in the owner and the repository. If it lists no ruleset named `main`, run `scripts/configure-github` now, on its own, before you change any file in `scripts` or `.gitignore`. If the script fails, stop the setup and pass its error on. When it finishes, ask the person to turn on Dependabot malware alerts at the link it prints.
   - In `AGENTS.md`, rewrite "What this project is": what the project is for, and which changes belong in it. Under "Commands", add the commands that aren't tied to one language, such as one that starts the project's database.
   - For each language the project uses, follow `docs/languages.md`.
   - If the project's output is visual, such as a web page, add a command that saves a picture of it outside the repository, and that runs without anyone at the screen. List it under "Commands".
   - If the project publishes a package or deploys, add a workflow that does it when a release is published, as "Protect the credentials that publish and deploy" in `docs/releasing.md` describes.
   - Set the year and the copyright holder in `LICENSE`.
   - Last, replace this README with one for the project, as `docs/pages.md` describes. Until the project can do something, it says what the project is for, and that it can't do anything yet.

   </details>

   [The template's README](https://github.com/agentic-templates/agent-template#set-up-a-project) stays online after the setup replaces this one.

4. To get the orchestrator's questions as push notifications on your phone, install the Claude app on the phone and sign in with your Claude Code account. After you start the orchestrator, as the next section describes, type `/remote-control` in its session, and turn on "Push when Claude decides" in `/config`. [Remote Control](https://code.claude.com/docs/en/remote-control) describes both.
5. To let the orchestrator clear its context when it grows long, write a `/clear` sender, a hook that the template doesn't ship, and add it to your own Claude Code settings, such as `~/.claude/settings.json`, for the hook events `Stop` and `StopFailure`. When the orchestrator wants a `/clear`, it writes `.loop/compact-request.txt` in the clone's folder, with its session id on the first line and the message that resumes the loop on the second. The hook:

   - reads the `session_id` from the JSON that Claude Code gives it.
   - if the file's first line is that id, types two messages into the session: `/clear`, and then the file's second line. It types them with a tool that types into a terminal, such as `tmux send-keys` when you run the orchestrator in tmux.
   - leaves the file in place, because the template's own hooks delete it after the `/clear`.

   Without a sender, the orchestrator asks you to type both messages yourself.
6. If you want the agents to build on more Claude or Codex accounts than the one that Claude Code is signed in to, list every account in `.loop/accounts.json`, including that one, as `scripts/loop/meter --help` describes.

## From a goal to a release

1. In the clone's folder, start the orchestrator:

   ```bash
   scripts/loop/claude-with-events
   ```

2. Ask it: **"Start the loop. The goal: the first version of expense-tracker, which adds an expense, lists the expenses and shows the total for each month."** Include your notes or spec again.
3. The orchestrator splits the goal into issues of one pull request each, and links each issue to the issues it needs first. It adds the `ready` label to each issue that is clear enough to build. It starts agents on the ready issues whose blocking issues are closed, as many as your accounts' usage limits have room for. Each agent opens a pull request, which merges through the merge queue. When a usage limit stops the work, the orchestrator waits for the reset and goes on.
4. When the orchestrator needs your decision, it writes the question in an issue with the `needs-decision` label, with the option it recommends, and sends you a push notification if you set them up. Answer in a comment on the issue. To list the questions that wait for you, run:

   ```bash
   gh issue list --label needs-decision
   ```

5. When the goal's issue closes, the orchestrator publishes a release. It checks that CI passed on main and that no code scanning or Dependabot alert of high or critical severity is open. An agent reviews the changes since the last release for security problems. The orchestrator fixes each vulnerability that the agent finds, and waits until another agent has checked each fix. Then it publishes the next semantic version, with notes from the merged pull requests.

## Steer the orchestrator

Tell the orchestrator these, in its session or in an issue or comment on GitHub:

- A new goal or feature.
- An error, a regression or waste that you notice, such as an agent that spends more than its issue needs. The orchestrator fixes the cause too, so that the loop doesn't make the same mistake again.
- A question that someone must answer before anyone builds, such as whether a library can do the job. The orchestrator has an agent research it.
- A correction, when the loop gets stuck or drifts from the goal.

The orchestrator reads them each time it wakes, and plans the work without stopping the agents that run. It also triages the issues that other people open.

You can also ask it:

- **"Triage these ideas:"**, followed by the text or a link. It opens issues for the ideas it accepts, and asks you about the rest.
- **"Publish a release."**
- **"Review pull request #12."**
- **"Review the code for security problems."**

If the orchestrator's Claude Code process ends, such as after a restart of the machine, start it again with this command, and pick the orchestrator's session from the list that it shows:

```bash
scripts/loop/claude-with-events --resume
```

## Fix security problems

Issues and pull requests are public, so the orchestrator keeps a security problem in a private security advisory until a release fixes it. GitHub opens an advisory for each private report too.

1. Ask the orchestrator to fix the problem. It builds the fix in the advisory's temporary private fork.
2. Ask for a release right away if the severity is high or critical, or if the problem is public. Otherwise, ask within 90 days of a report, as `.github/SECURITY.md` promises reporters. If a private fork holds a fix that no reviewer agent has passed, such as one that a reporter pushed, the orchestrator asks you in its session whether the release should include it.
3. In a new session, ask "Check the security advisories." The new session checks each fix more closely than the orchestrator, whose context also holds the work of the release.
4. Ask that session to publish only the advisories that you're sure are fixed, and only once users can install the release, because a published advisory shows attackers where to look.

## Limits

- The template works only in a public repository that an organization owns.
- The loop runs only while the orchestrator's Claude Code process runs, on a machine that stays awake.
- Run only one orchestrator in a repository at a time, because two can take the same issue.
- Codex runs the hooks only after you review them with `/hooks`.
- The hooks check only the commands on their lists, so a script or another command can still read a secret file such as `.env`, or get the GitHub token from gh or git. [Claude Code's sandbox doesn't stop this on macOS](https://github.com/agentic-templates/repo-template/issues/427#issuecomment-6160078401). To keep your other secrets out of an agent's reach, run it in a separate user account or virtual machine that doesn't hold them.
- A repository made from the template doesn't get the template's later changes.
- People with GitHub's Triage role can add the `ready` label without write access. A workflow then removes the label, but the orchestrator can take the issue first, so give that role only to people you'd let choose the work.
- Pull requests merge without a person's review once their checks pass, including Dependabot's pull requests. Code scanning stops a merge only when it finds a high or critical security alert, or a CodeQL error, in the lines that the pull request changes. It never stops Dependabot's pull requests.
- Only a library whose language has a tool that finds breaking changes gets a check for them. In other projects, a release can miss a breaking change and raise the version too little.

## How it works

`AGENTS.md` and its guides in `docs/` hold the rules, and [docs/loop.md](docs/loop.md) describes the loop that the orchestrator runs. `scripts/check` runs the checks locally and in CI, and the hooks in `scripts/hooks/` block an agent's risky actions, such as reading `.env`. When `scripts/loop/meter` says that a retrospective is due, the orchestrator starts an agent that measures the loop's work and opens a pull request that changes the loop's own rules, checks and agent types.

## License

[MIT](LICENSE)
