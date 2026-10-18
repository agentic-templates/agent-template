# repo-template

Start a GitHub project that coding agents plan, build and release, while you make only the decisions that need a person. Every change goes from an issue to a pull request, which merges without a review once its checks pass. You approve the issues before agents build them, or ask an agent for a change yourself. Approved issues get the `ready` label, which agents add on their own only to bugs they reproduce.

It works with any language, in Claude Code, Codex, Copilot CLI and Cursor.

## Start a project

You need:

- The GitHub CLI
- bash
- [jq](https://jqlang.org/download/)

1. If you don't have a GitHub organization, create one at https://github.com/account/organizations/new.
2. Create a public repository from the template, and go into its clone:

   ```bash
   gh repo create <organization>/expense-tracker --public --template agentic-templates/repo-template --clone
   cd expense-tracker
   ```

3. Start your agent there, and ask it to set up the project, with any notes or spec you have: "Set up this repository for expense-tracker, a command-line tool in Python that records what you spend. Follow the setup list in README.md."

   <details>
   <summary>The setup list, for your agent</summary>

   - Run `gh api "repos/{owner}/{repo}/rulesets?includes_parents=false"` as written, because gh fills in the owner and the repository. If it lists no ruleset named `main`, run `scripts/configure-github` before you open the setup's pull request. If the script fails, stop the setup and pass its error on. When it finishes, ask the person to turn on Dependabot malware alerts at the link it prints.
   - In `AGENTS.md`, rewrite "What this project is": what the project is for, and which changes belong in it. Under "Commands", add the commands that aren't tied to one language, such as one that starts the project's database.
   - For each language the project uses, follow `docs/languages.md`.
   - If the project's output is visual, such as a web page, add a command that saves a picture of it outside the repository, and that runs without anyone at the screen. List it under "Commands".
   - If the project publishes a package or deploys, add a workflow that does it when a release is published.
   - Set the year and the copyright holder in `LICENSE`.
   - Last, replace this README with one for the project, as `docs/pages.md` describes. Until the project can do something, it says what the project is for, and that it can't do anything yet.

   </details>

   The setup replaces this README. [The template's README](https://github.com/agentic-templates/repo-template#from-idea-to-first-release) stays online.

## From idea to first release

Each request can start a new conversation.

1. **"Plan the first version of expense-tracker: it adds an expense, lists the expenses and shows the total for each month."** Include your notes or spec again. The agent splits the work into issues of one pull request each, linked to the issues they need first. Correct them, on GitHub or through the agent, and say which ones you approve.
2. **"Build the ready issues."** The agent builds several issues at once with helper agents, each after the issues it depends on are closed. When it can't finish an issue without you, it keeps the work on the issue's branch and asks you in its report. Tell it your answer, and your next "Build the ready issues" picks up from there. To change the helpers, add "with up to 3 helpers", "without helpers" or "on Opus". To set their effort in Claude Code, write a [subagent](https://code.claude.com/docs/en/sub-agents) named build-helper, and add "using build-helper".
3. **"Publish a release."** The agent checks that CI passed on main and that no code scanning or Dependabot alert of high or critical severity is open. It publishes the next semantic version with notes from the merged pull requests. GitHub asks you to approve each run of a workflow that publishes a package or deploys. Then the agent lists any security advisories you haven't published, which "Fix security problems" covers.

## Everyday requests

For later versions, use the same three requests, and these:

- **"Triage these ideas:"**, followed by the text or a link. The agent opens issues for the ideas it accepts, and asks you about the rest.
- **"Triage the new issues."** The agent accepts the bugs it can reproduce, closes duplicates, and asks you about the rest.
- **"Make #14 ready to build."** The agent splits or rewrites the issue, and asks you to approve it.
- **"Which issues need my decision?"**
- **"Fix the typo in the README's first sentence."**, or any other small change. The agent sees its pull request through to the merge.
- **"Fix the pull requests that didn't merge."**
- **"Review pull request #12."**
- **"Review the code for security problems."**

## Fix security problems

Issues and pull requests are public, so the agent keeps a security problem in a private security advisory until a release fixes it. GitHub opens an advisory for each private report too.

1. Ask the agent to fix the problem. It builds the fix in the advisory's temporary private fork.
2. Ask for a release right away if the severity is high or critical, or if the problem is public. Otherwise, ask within 90 days of a report, as `.github/SECURITY.md` promises reporters. The agent asks whether to include each fix that's still in a private fork.
3. In a new conversation, ask "Check the security advisories." That agent checks each fix more carefully than the one that published the release.
4. Ask the agent to publish only the advisories you're sure are fixed, and only once users can install the release, because a published advisory shows attackers where to look. A package is installable only after you approve its workflow's run and the run finishes.

## Limits

- The template works only in a public repository that an organization owns.
- Codex runs the hooks only after you review them with `/hooks`.
- The hooks check only the commands on their lists, so a script or another command can still read a secret file such as `.env`, or get the GitHub token from gh or git. [Claude Code's sandbox doesn't stop this on macOS](https://github.com/agentic-templates/repo-template/issues/427#issuecomment-6160078401). To keep your other secrets out of an agent's reach, run it in a separate user account or virtual machine that doesn't hold them.
- A repository made from the template doesn't get the template's later changes.
- People with GitHub's Triage role can add `ready` without write access. A workflow then removes the label, but a build run can take the issue first, so give that role only to people you'd let approve work.
- Agents merge their own pull requests once the checks and code scanning pass, and a build run merges Dependabot's too. Code scanning stops a merge only for a high or critical security alert, or a CodeQL error, in the changed lines, and never stops Dependabot's.
- Only a library whose language has a tool that finds breaking changes gets a check for them. In other projects, a release can miss a breaking change and raise the version too little.
- Ask only one agent at a time to build the ready issues, because two can take the same issue.

## What you get

`AGENTS.md` and its guides in `docs/` hold the rules, `scripts/check` runs the checks locally and in CI, and the hooks in `scripts/hooks/` block an agent's risky actions, such as reading `.env`.

## License

[MIT](LICENSE)
