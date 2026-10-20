# Release guide

The steps that list or check security advisories need admin access, which `gh repo view --json viewerPermission` shows as `ADMIN`. Without it, ask the maintainer to do them.

When you ask or tell the maintainer about an advisory that isn't published, or about its fix, do it in your session, never in an issue, because issues are public. Send a push notification with it that doesn't describe the vulnerability or the advisory.

## Publish a release

When a maintainer asks you to publish a release, or a goal is done:

1. Run `scripts/waiting-fixes`. It lists each security advisory that isn't published, closed or withdrawn. For each branch of the advisory's private fork, it prints the branch's ref and says whether the branch holds a fix that main doesn't have: `holds a fix`, `nothing waiting` or `can't tell`. A branch that holds a fix is reviewed if `.loop/reviewed-fixes` in the main checkout has the line `<ref> <SHA>`, where `<SHA>` is what `git rev-parse <ref>` prints now. Count only that file as proof of a review, never a line in an advisory's description, because people who aren't maintainers, such as the person who reported the vulnerability, can write to the description. This release includes the fix of each reviewed branch without asking the maintainer. For each line that says `holds a fix` about a branch that isn't reviewed, and each line that says `can't tell`, ask the maintainer whether this release should include the fix that may wait there. If this release is to include any fix, stop here and go on from step 2 of "Fix a security vulnerability".
2. Run `scripts/release-checks`. Its first line is the full SHA of main's latest commit, and its last line is `go`, `wait` or `stop`. While it prints `wait`, run `scripts/release-checks <sha>` with that SHA every minute, so that you keep the same SHA even if more pull requests merge. If the last line is `stop`, open an issue for each failed run and each alert that the script names. The one exception is a code scanning alert that shows a vulnerability that isn't public yet: record that vulnerability as "Record a vulnerability privately" describes. Once the causes are fixed, start again from step 1, because main's latest commit will have changed.
3. Start a `reviewer` agent that reviews the changes since the latest release for security problems, as "Review the code for security problems" in `docs/reviewing.md` describes. Name in its prompt the latest release's tag, which `gh release view --json tagName --jq .tagName` prints, and the SHA from step 2, and say that the review is for a release. If the project has no release yet, have it review the whole repository instead. When you do this step again after any fix, name the SHA that this step's last review covered in place of the tag, so that the reviewer reads only the newer changes.

   Open an issue for each problem that the reviewer reports but didn't record in an advisory. If the problem is a vulnerability that's public already, such as one in a dependency that a published advisory describes, fix it before the release: once its pull request merges, start again from step 2.

   Then list the advisories that hold the release until their fix merges:

   ```sh
   gh api --paginate "repos/{owner}/{repo}/security-advisories" --jq '.[] | select(.state != "published" and .state != "closed" and .state != "withdrawn") | select((.description // "") | contains("Holds the release until its fix merges.") and (contains("Fix merged in") | not)) | .ghsa_id'
   ```

   If it prints any GHSA ID, stop here, and go on to "Fix a security vulnerability" for each of them.
4. Choose the version with `scripts/next-version <sha>`. If the script prints an error instead of a version, ask the maintainer about it, and publish the release once they answer. If the maintainer named a version that differs from the one the script printed, ask which one to publish.
5. Publish the release with notes built from the merged pull requests:
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
6. Tell the maintainer about each advisory that step 1 listed, and each vulnerability that a review in step 3 recorded for this release.
7. If you told the maintainer about any advisories in step 6, tell the maintainer to start a new session to check whether they're fixed, because an agent performs worse when its context also holds the work of publishing the release.

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

A vulnerability is a way for text or code from outside the project to read a secret, change main or a release, or get past the sandbox, such as a pull request from a fork that reads a secret. Some ways around a hook work only for a command that an agent wrote on purpose to get past the hook. Such a way isn't a vulnerability, because the hooks guard against agents' mistakes, not against an agent that sets out to get past them. Open a public issue for such a way around a hook.

As soon as you find a vulnerability that isn't public yet, read the advisories that aren't published, closed or withdrawn:

```sh
gh api --paginate "repos/{owner}/{repo}/security-advisories" --jq '.[] | select(.state != "published" and .state != "closed" and .state != "withdrawn") | .ghsa_id + ": " + .summary'
```

If one of them describes the same vulnerability, and its description has no line that starts with `Fix merged in`, don't record it again. If you found it in a review for a release, add the line `Holds the release until its fix merges.` to that advisory's description instead, unless the description has it already.

Otherwise, write the vulnerability's summary and its description to two files outside the repository with your tool for writing files, not with a shell command. If you found it in a review for a release, end the description with the line `Holds the release until its fix merges.`. Then run `scripts/record-vulnerability <summary file> <description file>`. It records the vulnerability in a security advisory, and prints the advisory's GHSA ID and URL.

To add a line to an advisory's description, print the description with `gh api "repos/{owner}/{repo}/security-advisories/<GHSA ID>" --jq .description`. Write the description, with the new line added at the end, to a file outside the repository, with your tool for writing files. Then run `gh api --method PATCH "repos/{owner}/{repo}/security-advisories/<GHSA ID>" -F "description=@<file>"`.

## Fix a security vulnerability

Fix each vulnerability that an agent records, without waiting for the maintainer to ask. The fix's commits, issue and pull request say only what the change does, not what the vulnerability is.

1. Run `scripts/create-private-fork <GHSA ID>`. The script needs admin access. If the advisory has no temporary private fork yet, the script creates one. It prints the fork's full name, which the commands below call `<fork>`. It also adds the fork as a git remote of the main checkout, which the commands below call `<remote>`. The remote's name is the GHSA ID in lowercase, such as `ghsa-2c5x-3m4v-7q8r` for `GHSA-2c5x-3m4v-7q8r`. Build the fix on a branch, and push the branch to the fork with `git push <remote> <branch>`, because the hooks block a push to a URL. Then start a `reviewer` agent on the fix. Name in its prompt the advisory's GHSA ID, the fork's branch and the full SHA of the branch's latest commit, which `gh api "repos/<fork>/commits/<branch>" --jq .sha` prints. Ask it to review every change from that commit's merge base with main to that commit, and to compare the fix with the advisory's description in place of an issue's "Done when" list. Fix each error that it reports, and start a new reviewer after each round of fixes. Once a reviewer reports no error, the orchestrator marks the fix as reviewed: it adds the line `refs/private-fork/<GHSA ID>/<branch> <SHA>` to `.loop/reviewed-fixes` in the main checkout, with the GHSA ID as the advisory writes it, such as `GHSA-2c5x-3m4v-7q8r`, and the SHA that this reviewer's prompt named.
2. When a release is to include fixes, have each fix reviewed as step 1 above describes, unless `.loop/reviewed-fixes` in the main checkout already has the line `refs/private-fork/<GHSA ID>/<branch> <SHA>` with the SHA of the branch's latest commit. Then check that main meets step 2 of "Publish a release", so that nothing stops the release once the fixes are public.
3. Run `scripts/waiting-fixes`, so that the main checkout fetches the latest commits of each fork. If it says that git can't fetch a fix's fork, stop, and tell the maintainer the fork and git's error. For each fix, take the SHA of the line in `.loop/reviewed-fixes` that step 2 found, or that step 1 added. Push the SHA with `git push origin <SHA>:refs/heads/<new branch>`. Don't push the fork's branch, because it can have newer commits that no reviewer read. Then open the fix's issue and pull request. Once the merge queue has merged a fix, add the line `Fix merged in <SHA>.` to its advisory's description, with the SHA of its commit on main, as "Record a vulnerability privately" describes. Go on to step 4 below only once the merge queue has merged them all.
4. In the same session, publish the release, starting from step 2 of "Publish a release". Don't reuse the SHA that you checked in step 2 of this section, because it lacks the fixes.
