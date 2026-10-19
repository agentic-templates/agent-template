# shellcheck shell=bash
# The function for the record that scripts/fresh-reader writes of each page that it gives a reader, and that
# unread-pages.check reads. The record holds a line for each page that a reader read: git's hash of the page's content
# as git hash-object prints it, which a commit with that content holds for the page too.

# Prints the absolute path of the record, for the repository of the current folder. Each worktree has its own.
pages_read_path() {
  git rev-parse --path-format=absolute --git-path pages-read
}
