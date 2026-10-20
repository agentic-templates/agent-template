# Dependencies guide

## Pin versions

- When you add a tool or a package, use its newest stable version that is at least 7 days old. [Add a language](languages.md#add-a-language) sets up the package manager's release-age control, which enforces that age for the packages that it covers. Find the release date yourself for each version that the control doesn't cover, such as a tool's version or a version without a date in the registry.
- Record the exact version of every tool and package that the project installs. The language's version goes in its version file, such as `.python-version`. Packages go in a committed lockfile. A tool that the lockfile doesn't cover gets its version and checksum in the file that installs it, such as `scripts/install-check-tools`.
- Add a package only when it saves more work than it costs to review, update and secure. Prefer the standard library.
- Leave routine updates to Dependabot. When your change needs a new package, or a newer version of one, add or update it in the pull request of that change, and name each package that you added or updated in the pull request's description. If adding or updating the package also changes other packages in the lockfile, keep those changes.
