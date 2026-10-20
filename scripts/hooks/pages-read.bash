# shellcheck shell=bash
# The functions for the record that scripts/fresh-reader writes of each page that it gives a reader, and that
# unread-pages.check and unread-bodies.check read. The record holds a line for each page that a reader read: git's hash
# of the page's content as git hash-object prints it, which a commit with that content holds for the page too. When
# body_hash prints another hash for the page's text, the record holds that hash on a line of its own too.

# Prints the absolute path of the record, for the repository of the current folder. Each worktree has its own.
pages_read_path() {
  git rev-parse --path-format=absolute --git-path pages-read
}

# Prints git's hash of the body of an issue or pull request, with the line breaks at its end replaced by one, so that a
# body that a command gives with --body, which usually ends in none, gets the hash of the file that the reader read,
# which usually ends in one: body_hash <body>
body_hash() {
  local body=$1
  body=${body%"${body##*[!$'\n']}"}
  printf '%s\n' "$body" | git hash-object --stdin
}
