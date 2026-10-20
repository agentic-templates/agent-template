# shellcheck shell=bash
# Functions that find the repository that a gh command posts to, for maintainer-only.check, which sources this file.
# They use is_one_of and repo_edit_flags_with_value from that check.

# The values of GH_REPO that the Bash call's simple commands set in the shell, wherever they're written, as
# record_shell_gh_repo finds them
shell_gh_repos=()

# Adds to shell_gh_repos the value of GH_REPO that a simple command sets in the shell for the commands after it: with
# words that set variables and no command, or with export, declare and the like. An empty value counts as none, as it
# does for gh: record_shell_gh_repo <word>...
record_shell_gh_repo() {
  local word
  local -a setting_words=()
  drop_command_prefixes "$@"
  if ((${#command_words[@]} == 0)); then
    setting_words=(${assignment_words[@]+"${assignment_words[@]}"})
  elif is_one_of "${command_words[0]}" export declare typeset readonly local; then
    setting_words=("${command_words[@]:1}")
  fi
  for word in ${setting_words[@]+"${setting_words[@]}"}; do
    if [[ $word == GH_REPO=?* ]]; then
      shell_gh_repos+=("${word#GH_REPO=}")
    fi
  done
}

# The flags that take text, which can hold the URL of an issue of another repository, of the gh commands whose flags the
# hooks don't list
text_flags=" -b --body -t --title -c -F --body-file --description -n --notes --notes-file -T --template "

# Sets destination to the repository that a gh command posts to, as the command or git names it, or to empty when the
# hook can't tell. gh takes the repository from the first of these that it finds:
# - the endpoint of gh api, such as repos/owner/name/issues, unless it's repos/{owner}/{repo}
# - for any other command, the repository that gh repo edit and the like take, -R or --repo, or the URL of an issue or
#   a pull request
# - GH_REPO, whose value check_gh puts in gh_repo when the words before gh set it
# - the remote named origin of the folder that the command runs in
# It also sets guessed_repositories, as find_named_repository describes. Before it takes the origin, it adds each value
# in shell_gh_repos to guessed_repositories, because gh takes such a GH_REPO while the shell exports it, but takes the
# origin when the shell didn't export it or no longer has it, as after the ( ) that set it ends.
# For gh api, run read_arguments first.
find_destination() {
  local origin
  destination="" guessed_repositories=()
  if [[ ${command_words[1]:-} == api ]]; then
    if [[ ! $argument =~ ^/?repos/([^/]+/[^/?#]+) ]]; then
      return 0
    fi
    if [[ ${BASH_REMATCH[1]} != "{owner}/{repo}" ]]; then
      destination=${BASH_REMATCH[1]}
      return 0
    fi
  else
    find_named_repository
  fi
  if [[ -n $destination ]]; then
    return 0
  fi
  if [[ -n $gh_repo ]]; then
    destination=$gh_repo
    return 0
  fi
  guessed_repositories+=(${shell_gh_repos[@]+"${shell_gh_repos[@]}"})
  if [[ -z $command_folder ]]; then
    return 0
  fi
  origin=$(git -C "$command_folder" remote get-url origin 2>/dev/null) || return 0
  # gh posts to a repository on a host, such as https://github.com/owner/name or git@github.com:owner/name, and fails
  # for a repository in a local folder.
  if [[ $origin =~ ^(https?|ssh|git):// || ($origin != *://* && $origin =~ ^[^/]+:) ]]; then
    destination=$origin
  fi
}

# Sets command_flags_with_value to the flags that take a value of the gh command in command_words, from the lists in
# helpers.bash, or to empty when the hooks list no flags of the command.
read_command_flags_with_value() {
  case "${command_words[1]:-} ${command_words[2]:-}" in
    "issue create" | "issue new") command_flags_with_value=$issue_create_flags_with_value ;;
    "issue edit") command_flags_with_value=$issue_edit_flags_with_value ;;
    "issue close") command_flags_with_value=$issue_close_flags_with_value ;;
    "issue develop") command_flags_with_value=$develop_flags_with_value ;;
    "issue comment" | "pr comment") command_flags_with_value=$comment_flags_with_value ;;
    "issue reopen" | "pr reopen") command_flags_with_value=$reopen_flags_with_value ;;
    "pr create" | "pr new") command_flags_with_value=$pr_create_flags_with_value ;;
    "pr edit") command_flags_with_value=$pr_edit_flags_with_value ;;
    "pr review") command_flags_with_value=$review_flags_with_value ;;
    "pr close") command_flags_with_value=$pr_close_flags_with_value ;;
    "pr merge") command_flags_with_value=$merge_flags_with_value ;;
    *) command_flags_with_value="" ;;
  esac
}

# Sets destination to the repository that gh repo edit, delete, archive or unarchive in command_words takes as its
# argument, or to empty for another command or when the command names none. These commands take no -R.
read_repository_argument() {
  destination=""
  case "${command_words[1]:-} ${command_words[2]:-}" in
    "repo edit")
      read_arguments 3 "$repo_edit_flags_with_value"
      destination=$argument
      ;;
    "repo delete" | "repo archive" | "repo unarchive")
      read_arguments 3 " "
      destination=$argument
      ;;
  esac
}

# Adds the repository of each -R and --repo that read_arguments found to guessed_repositories.
add_guessed_repositories() {
  local i
  for ((i = 0; i < ${#last_flags[@]}; i++)); do
    if [[ ${last_flags[i]} == -R || ${last_flags[i]} == --repo ]]; then
      guessed_repositories+=("${flag_values[i]}")
    fi
  done
}

# Sets first_expansion to the index of the first word with an expansion in command_words, from the gh command's
# subcommand on, or to the number of words when the command has none. A word with an expansion is one that the splitter
# marks, or one that holds $, {, *, ? or [, because the splitter doesn't mark ANSI-C quoting, locale quoting, brace
# expansion or a pattern, as in $'-b', $"-b", {-b,} and -[b].
find_first_expansion() {
  local i
  for ((i = 2; i < ${#command_words[@]}; i++)); do
    if [[ -n ${command_word_may_expand[i]:-} || ${command_words[i]} == *[\$\{\*\?\[]* ]]; then
      break
    fi
  done
  first_expansion=$i
}

# Adds to guessed_repositories the repository of each -R and --repo after the word at first_expansion, which it reads as
# read_arguments reads a command whose flags the hooks don't list. Once the shell expands that word, it can become
# nothing, become a flag that takes a value, or end in a flag that takes no value or one whose value is the next word,
# as -${letters}b and -b$text can. Then gh can read a word as a flag that the hook reads as a flag's value, as in
# -b $text -b -Rowner/name. It sets the variables of read_arguments anew.
add_repositories_after_expansions() {
  read_arguments $((first_expansion + 1)) " -R --repo "
  add_guessed_repositories
}

# Sets destination to the repository that the words of a gh command other than gh api name, or to empty. It reads -R
# and --repo as read_arguments in helpers.bash reads them, with the command's flags that take a value.
# gh takes the last -R, but a later -R can be part of the value of a flag before it, and gh then takes an earlier -R.
# The hook can't tell this when a word with an expansion becomes nothing, so that the -R after it becomes the value of
# the flag before it. So the function adds the repository of each -R and --repo to guessed_repositories, which must all
# be allowed. It also adds that of each -R that gh can read as a flag after a word with an expansion, as
# add_repositories_after_expansions describes. It takes the last -R before the first word with an expansion, which gh
# reads as a flag. Without such a -R, gh may find none, so the function goes on as if the command gave no -R.
# For a command whose flags the hooks don't list, it reads each flag other than -R and --repo as one that takes no
# value, as in -dRowner/name, which it reads as -d -R owner/name. Then any -R can be part of the value of a flag before
# it, in the same word or the word before, and without another -R, gh posts to the repository that the command names
# without -R. So for such a command, the function goes on as if the command gave no -R.
find_named_repository() {
  local i word command_flags_with_value first_expansion
  guessed_repositories=()
  read_repository_argument
  if [[ -n $destination ]]; then
    return 0
  fi
  read_command_flags_with_value
  find_first_expansion
  add_repositories_after_expansions
  read_arguments 2 "${command_flags_with_value:-" -R --repo "}"
  add_guessed_repositories
  if [[ -n $command_flags_with_value ]]; then
    read_arguments 2 "$command_flags_with_value" "$first_expansion"
    if ((${#repository[@]} > 0)); then
      destination=${repository[1]}
      return 0
    fi
  fi
  if [[ ${command_words[1]:-} == repo ]]; then
    return 0
  fi
  # The URL can stand anywhere among the words after the subcommand.
  for ((i = 3; i < ${#command_words[@]}; i++)); do
    word=${command_words[i]}
    if is_flag_before_value "$word" "$command_flags_with_value"; then
      i=$((i + 1))
    elif [[ $word =~ ^https?://[^/]+/[^/]+/[^/]+ ]]; then
      destination=$word
      return 0
    fi
  done
}

# Succeeds if gh reads the word after a word of a gh command as the value of a flag that the word ends in. It reads the
# word as split_flag_word does, with the command's flags that take a value. For a command whose flags the hooks don't
# list, it reads only a word in text_flags as such a flag:
# is_flag_before_value <word> <the command's flags that take a value, or empty>
is_flag_before_value() {
  local flag value value_is_next_word flags_before
  if [[ -z $2 ]]; then
    [[ $text_flags == *" $1 "* ]]
    return
  fi
  if [[ $1 != -* ]]; then
    return 1
  fi
  split_flag_word "$1" "$2"
  [[ -n $value_is_next_word ]]
}
