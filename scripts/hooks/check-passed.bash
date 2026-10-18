# shellcheck shell=bash
# Functions for the record that scripts/check writes when it passes, and that unchecked-pull-requests.check reads.
# The record holds the tree of the files that scripts/check passed on: git's hash of the tracked files and their
# contents, which a commit of the same files has too. The functions work on the repository of the current folder.

# Prints the path of the record. Each worktree has its own.
pass_record_path() {
  git rev-parse --git-path check-passed
}

# Prints the tree of the tracked files as they are on disk, with the changes that aren't committed or staged.
# It stages the files in a copy of the index, so that the real index stays as it is.
tree_on_disk() {
  local index folder status=0
  index=$(git rev-parse --git-path index) || return 1
  folder=$(mktemp -d) || return 1
  # A repository with no index yet tracks no files, and git reads a missing index as empty.
  # -p keeps the time of the index, which git compares with the times of the files to find the changed ones.
  if [[ -e $index ]] && ! cp -p "$index" "$folder/index"; then
    status=1
  elif ! GIT_INDEX_FILE=$folder/index git add -u || ! GIT_INDEX_FILE=$folder/index git write-tree; then
    status=1
  fi
  rm -rf "$folder"
  return "$status"
}
