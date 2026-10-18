# Languages guide

## Add a language

1. Pin the version of the language, its package manager and each framework and database that the code in the language uses, as [Pin versions](dependencies.md#pin-versions) describes. Add steps to `.github/workflows/ci.yml` that install those versions and the language's packages.
2. If the package manager has a release-age control, configure it to reject versions less than 7 days old when it resolves new or updated dependencies, transitive ones too where it can, and leave its list of exempt packages empty. Check the documentation of the package manager's pinned version for what the control covers and where its cutoff falls, and check release dates yourself for what it doesn't cover.
3. If the package manager has a frozen or locked install mode that rejects a missing or out-of-date lockfile without rewriting it, use that mode in CI.
4. Add the formatter, the linter, the type checker if the language has one, and the tests to `scripts/check`. In the tools you choose, including their standard language plugins, turn on these settings where available, for the tests as well as the code:
   - the type checker's strict mode, or the compiler's setting that turns warnings into errors
   - the linter's rules against unused code
   - the linter's rules against code that ignores an error, such as an empty `catch` block, a returned error that the caller never checks or a promise that nothing awaits
   - the tools' checks against blanket and unused suppression comments or attributes, such as `# noqa`
   - a failure on a focused test, such as `test.only`
   - the linter's limit on how complex one function can be: the cognitive count where the linter has one, and the cyclomatic count otherwise, with a limit of 15, or 10 for Ruff's C901, which doesn't count `and` and `or`. Where the count ignores nesting, also limit nesting to 4 levels, unless that rule is experimental.

   Don't add a separate tool or plugin solely to supply a missing setting. For each setting that you turned on, add a small example that breaks it, and see `scripts/check` fail on it. Delete each example before you commit.
5. Add a hook to `.claude/settings.json` and `.codex/hooks.json` that runs the formatter on each file in the language that the agent edits.
6. If the language's coverage tool can mark single lines as excluded, add a coverage check to `scripts/check`. The check fails when a line never runs in a test. Where the tool measures branches, the check also fails when a branch is never taken. Lines marked as excluded don't count, but the check fails on an exclusion marker that gives no reason. Measure coverage with the tests that `scripts/check` runs, and with no others. If the tool can't mark lines as excluded, add no coverage check.
7. If the project publishes a library in the language, and the language has a tool that finds breaking changes, such as cargo-semver-checks for Rust, add a check to CI that looks for a break in the library's public interface. If a pull request breaks the interface, the check fails, unless the pull request's title marks the change with `!` before the colon, as in `feat!: rename the import command`. Have the check compare the pull request with the commit on main that it branched from, not with the last release.
8. Add the language's package manager, such as pip or npm, to `.github/dependabot.yml`, so that Dependabot updates its packages too.
9. Update the "Commands" section in `AGENTS.md` with the language's install, run and test commands.
