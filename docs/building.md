# Building guide

## Build the ready issues

When a maintainer asks you to build the ready issues, work through them without stopping to ask. Don't wait for each pull request's checks: once auto-merge is on, take the next issue. Step 6 waits for the pull requests at the end. A maintainer starts a build run only when no other build run is going on the repository, so you don't need to check for another one.

If you can start agents that each work in their own clone or worktree, build several issues at once with as many helpers as can work at the same time, or with the number the maintainer names, such as "with up to 3 helpers". Take each issue as step 3 below describes, give it to a new helper, and go back to step 2. When that many helpers are working, don't take another issue. Wait until the first of them reports or fails, and then go back to step 2. In each helper's instructions, give the line that `scripts/take-next-issue` printed for its issue, say that it builds that issue as a helper in this build run, and ask it to read only "Build one issue" in `docs/building.md` first. A helper uses the same model and effort as you. If the maintainer names another model, or an agent that the maintainer set up for helpers, the helper uses that instead. If a helper fails without saying why, run `scripts/clear-worktrees <issue>` for its issue. If the script exits with a code other than 0, it couldn't free the issue's branch, so give the issue back as step 5 below describes. Otherwise, remove the assignment from the issue, so that another helper can try it. If that helper fails too, give the issue back as step 5 below describes. A helper does steps 4 and 5 for its own issue. You do the other steps below yourself, including step 2 for the helpers' pull requests.

1. Run `scripts/start-build-run`. It saves and removes each leftover worktree that holds a ready issue's branch, and says which ones it left and why. In step 3, skip the issues of the worktrees it left, and name those worktrees in your report in step 6.
2. Run `scripts/stuck-pull-requests`. It prints a line for each stuck pull request, with what's wrong, and how many times the merge queue removed it and why:
   - For one that the queue removed 2 times or more, give its issue back as step 5 below describes.
   - For any other one that is `DIRTY`, failed its checks or isn't back in the queue, start a new helper to fix it, or fix it yourself if you work without helpers. A new helper costs less than waking the one that built the pull request, whose cached context has expired by then. To fix it, resolve the conflicts with main, fix the checks as step 4 of "Make a change" in AGENTS.md describes, or fix what the queue's reason for removing it says went wrong, if anything. Once a pull request that the queue removed is fixed, add it to the queue again with `gh pr merge <number>`.
   - For one that's `BLOCKED` outside the queue after all its checks passed, check whether the script printed it on your last pass through this step too. If it did, give its issue back as step 5 below describes. Otherwise, leave it until your next pass.
   - Leave each of Dependabot's for the maintainer.
3. Run `scripts/take-next-issue`, followed by the number of each issue that step 1 says to skip. It assigns you the lowest-numbered ready issue that a build run can start, and prints the issue's number and its kind, such as `12 other`. If it prints `none`, go on with step 6.
4. Build the issue as "Build one issue" below describes. Then go back to step 2.
5. Give an issue back as "Build one issue" below describes.
6. When no issue is left that you can start, wait for your helpers, if you have any, and go back to step 2 each time one reports or fails. When no helper is working, run `scripts/wait-for-pull-requests`, with a timeout of 10 minutes. It waits up to 9 minutes for a pull request with auto-merge on, other than Dependabot's, to merge or need a fix. Go back to step 2 when it prints anything other than "Nothing to wait for." The maintainer wants every build run to wait this way, in every tool, including the Claude desktop app, which otherwise tells agents not to poll CI. Once it prints "Nothing to wait for.", run `scripts/build-run-report`. Report to the maintainer what it prints, and add:
   - the issues you opened on your own, for problems you noticed
   - the security vulnerabilities you noticed
   - the worktrees that `scripts/start-build-run` and `scripts/clear-worktrees` left, and why
   - what a hook or a script asked you to list in your report
   - anything else a maintainer needs to run, such as `scripts/configure-github`

## Build one issue

Build the issue by the kind that `scripts/take-next-issue` printed:

- For `research`, don't open a pull request. Answer its question in a comment, with a recommendation and the evidence that the maintainer needs to decide. If you need to try code, do it outside the repository, and put the short parts that show the answer in the comment. Check each issue that the research issue blocks. If the answer changes what that issue asks for, rewrite the issue to match, and decide what you can, as "Decide or ask" in AGENTS.md describes. Add `needs-decision` only for a question that the maintainer must answer, and write it in the issue. Then close the research issue.
- For `parent`, don't build anything. Close it if its "Done when" is met, and give it back if it isn't.
- For `left behind <pull request>`, finish that pull request: run `scripts/check` again, update its description, and turn on auto-merge with `gh pr merge <number>`.
- For `other`, make the change as steps 2 to 5 of "Make a change" in AGENTS.md describe.

If you build on a branch that ends in a commit with the subject `chore: save the unfinished work of a stopped session`, check each file the commit adds or changes, and undo each change that the issue doesn't need.

Give the issue back to the maintainer only when you can't finish it without them: when it needs a decision that "Decide or ask" in AGENTS.md leaves to the maintainer, when it's against one of the rules in AGENTS.md or too big for one pull request, or when its pull request can't merge and you can't fix that. Comment on the issue with what you need, and remove your assignment with `gh issue edit <issue> --remove-assignee @me`. If it's too big, add `needs-breakdown`. Otherwise, if the issue is now blocked by another issue, add no status label, because a build run skips it until its blocking issues are closed. Otherwise, add `needs-decision`. If it has a pull request, close it with `gh pr close <number>`.

A helper tells the agent that started it how its issue ended, with the number of its pull request if it opened one, and any problems it noticed outside its issue.

## Work on a ready issue outside a build run

1. Run `scripts/take-issue <issue>`. It removes `ready`, so that no build run takes the issue from now on, and then prints whether a build run has taken it already.
2. If it prints `taken`, leave the issue, and tell the maintainer that a build run has it. The script has added `ready` back.
3. If it prints `free`, go on with step 2 of "Make a change" in AGENTS.md.

If you give up on the issue and leave no open pull request for it, add `ready` back.
