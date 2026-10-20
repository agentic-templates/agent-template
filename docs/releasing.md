# Release guide

The steps that list or check security advisories need admin access, which `gh repo view --json viewerPermission` shows as `ADMIN`. Without it, ask the maintainer to do them.

## Publish a release

Publish a release when a maintainer asks you to, and when a goal is done, which is when its issue closes as completed. For a goal of more than one pull request, that's its parent issue. Then follow these steps. In steps 1, 5 and 6, tell the maintainer in your session, never in an issue, because issues are public and would show that a vulnerability isn't fixed yet. Send a push notification with it that doesn't describe the vulnerability or the advisory.

1. Run `scripts/waiting-fixes`. It lists each security advisory that isn't published, closed or withdrawn. For each branch of the advisory's private fork, it prints a line that says whether the branch holds a fix that main doesn't have. For each line that says `holds a fix` or `can't tell`, ask the maintainer whether this release should include the fix that may wait there. If the maintainer wants the fix in this release, stop here and go on from step 2 of "Fix a security vulnerability".
2. Run `scripts/release-checks`. Its first line is the full SHA of main's latest commit, and its last line is `go`, `wait` or `stop`. While it prints `wait`, run `scripts/release-checks <sha>` with that SHA every minute, so that you keep the same SHA even if more pull requests merge. If it prints `stop`, open an issue for each failed run and each Dependabot alert that the lines before it name. A Dependabot alert's advisory is public already. For each code scanning alert, check whether it shows a vulnerability that isn't public yet, and if so record it as "Record a vulnerability privately" describes, not in an issue. Once the causes are fixed, start again from step 1, because main's latest commit will have changed.
3. Choose the version with `scripts/next-version <sha>`. If the script prints an error instead of a version, ask the maintainer in an issue with `needs-decision`, and publish the release once they answer. If the maintainer named a version that differs from the one the script printed, ask which one to publish.
4. Publish the release with notes built from the merged pull requests:
   ```sh
   gh release create <version> --target <sha> --generate-notes
   ```

   If the project attaches files to its releases, such as binaries, add `--draft` to that command. A draft doesn't start release workflows, so start the project's asset workflow yourself. For example, a workflow named `release-assets.yml` with the string inputs `build_sha` and `draft_tag` takes the full SHA checked in step 2 and the version tag of the draft you just created:

   ```sh
   gh workflow run release-assets.yml --ref main -f build_sha='<sha>' -f draft_tag='<version>'
   ```

   Use your project's workflow and input names. Here, `--ref main` selects the branch that holds the workflow. The project's workflow must reject missing or invalid inputs. It must build the exact SHA in `build_sha`, even if main advances, in the jobs that "Protect the credentials that publish and deploy" describes. Immediately before it uploads, the upload job must check that the release is still a draft, and that the release's tag resolves to that SHA. If the tag doesn't exist yet, the draft's target commit must equal the full SHA. The upload job must stop if any check fails, and upload only to that draft.

   Have the workflow include both inputs in its run name, so you can find the run for this SHA and draft:

   ```sh
   gh run list --workflow release-assets.yml --event workflow_dispatch
   ```

   Use the matching run's ID below. Publish the draft only after that run succeeds:

   ```sh
   gh run watch <run-id> --exit-status && gh release edit <version> --draft=false
   ```
5. Tell the maintainer about each advisory that step 1 listed.
6. If you told the maintainer about any advisories in step 5, tell the maintainer to start a new session to check whether they're fixed, because an agent performs worse when its context also holds the work of publishing the release.

## Protect the credentials that publish and deploy

- Run the build in a job that can only read the repository. Give the credentials, or a token that can write, only to a separate job that uploads what the build made, so that code from the build's dependencies can't reach those credentials.
- In the `release` environment, run the job that publishes a package to a registry or deploys the project.
- Where the registry or host accepts GitHub's OpenID Connect login, such as PyPI's trusted publishing, use it. Tell the maintainer the exact steps to set it up on the registry or host.
- Where the registry or host doesn't accept that login, ask the maintainer to store the job's token as a secret of the `release` environment, not of the repository.

## Check the security advisories

When a maintainer asks you to check the security advisories, check them against the latest release:

1. Run `scripts/waiting-fixes <tag>`, with the latest release's tag, which `gh release view --json tagName --jq .tagName` prints. It lists the advisories as step 1 of "Publish a release" describes, but compares each branch of their private forks with the release instead of main.
2. For each advisory on that list, check whether its problem is still in the release. For a branch of its fork, `nothing waiting` means that the release has every change in the branch, and `holds a fix` means that the release lacks some. Check each fix closely too, because a fix can be incomplete.
3. For each advisory on the list, tell the maintainer whether you're sure the release fixes it, and how you checked.
4. Publish an advisory only when the maintainer asks you to, and only once users can install the release that fixes it, because publishing shows attackers where the problem is. If a workflow that publishes a package or deploys hasn't succeeded for the release, which `gh run list` shows, tell the maintainer and don't publish the advisory. Do the same if you can't tell whether users can install the release, such as when an app store makes it available later.

   Before you publish it, set the release's version as its fixed version, and the versions before it as affected. If the project publishes a package, also set the package's ecosystem and name.
5. Once an advisory is published, add a line to the release notes that links it.

## Record a vulnerability privately

A vulnerability is a way for text or code from outside the project, such as an issue or a pull request by someone who isn't a maintainer, to read a secret, change main or a release, or get past the sandbox. Some ways around a hook work only for a command that someone wrote on purpose to get past the hook. Such a way around a hook isn't a vulnerability, because the hooks guard against agents' mistakes, not against an agent that sets out to get past them. Open a public issue for it, as for any other problem you find.

As soon as you find a vulnerability that isn't public yet, write its summary and its description to two files outside the repository with your tool for writing files, not with a shell command. Then run `scripts/record-vulnerability <summary file> <description file>`. It records the vulnerability in a security advisory, and prints the advisory's GHSA ID and URL.

## Fix a security vulnerability

The fix's commits, issue and pull request say only what the change does, not what the vulnerability is.

1. Run `scripts/create-private-fork <GHSA ID>` with the advisory's GHSA ID. The script needs admin access. If the advisory has no temporary private fork yet, the script creates one. It prints the fork's full name. Build the fix in that fork.
2. When the maintainer asks for a release that includes the fix, check that main meets step 2 of "Publish a release", so that nothing stops the release once the fix is public.
3. Push the fix to the project's repository, and open its issue and pull request. Go on to step 4 below only once the merge queue has merged it, so that the release includes the fix.
4. In the same session, publish the release, starting from step 2 of "Publish a release". Don't reuse the SHA that `scripts/release-checks` printed in step 2 of this section, because it lacks the fix.
