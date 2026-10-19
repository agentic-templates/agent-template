# shellcheck shell=bash
# Functions that the checks in this folder share. A check sources this file first.

# A check blocks only with exit code 2. Any other failure, such as a jq error, ends the check
# with code 1, which allows the action, because a broken hook must never stop work.
# Some commands fail with code 2 themselves, so the trap replaces their code.
set -eEuo pipefail
trap 'exit 1' ERR

# The file name of the check that sourced this file, which log_block writes.
check_name=${BASH_SOURCE[1]##*/}

# scripts/hooks/before-tool reads this file once, and then runs each check in a subshell, which skips the rest of the
# file when the check sources it. Reading the file took a third of the CPU time of each check, as issue #91 measured.
if [[ -n ${helpers_read:-} ]]; then
  return 0
fi
helpers_read=yes

# shellcheck source=scripts/loop-folder
source "${BASH_SOURCE[0]%/*}/../loop-folder"
# shellcheck source=scripts/hooks/words-without-secrets.bash
source "${BASH_SOURCE[0]%/*}/words-without-secrets.bash"
# shellcheck source=scripts/hooks/simple-commands.bash
source "${BASH_SOURCE[0]%/*}/simple-commands.bash"

# This file's folder, as an absolute path, because some checks change to the folder that the agent works in.
hooks_folder=${BASH_SOURCE[0]%/*}
if [[ $hooks_folder != /* ]]; then
  hooks_folder=$PWD/$hooks_folder
fi

# Sets these variables from the fields of the tool's input, which scripts/hooks/before-tool reads once for all the
# checks. Every tool sends Claude Code's form: one JSON object, with the tool in tool_name and its input in tool_input.
# - tool_name: the tool, such as Bash or Read. Cursor's Shell counts as Bash.
# - shell_command: the command that Bash runs, or empty.
# - patch: the patch text that Codex's apply_patch applies, or empty.
# - file_path: the file that Read, Write or Edit uses, or empty.
# - cwd: the folder the agent works in, or empty if the tool doesn't send it.
# - session_id: the session's id, or empty if the tool doesn't send it. A subagent's input has its session's id.
# - agent_id: a subagent's id, which Claude Code sends only for the actions of a subagent, or empty.
read_input() {
  tool_name=${HOOK_TOOL_NAME:-}
  if [[ $tool_name == Shell ]]; then
    tool_name=Bash
  fi
  shell_command=""
  patch=""
  if [[ $tool_name == apply_patch ]]; then
    patch=${HOOK_COMMAND:-}
  else
    shell_command=${HOOK_COMMAND:-}
  fi
  file_path=${HOOK_FILE_PATH:-}
  cwd=${HOOK_CWD:-}
  session_id=${HOOK_SESSION_ID:-}
  agent_id=${HOOK_AGENT_ID:-}
}

# Blocks the action, tells the agent why, and logs the block with the part of the action that the check matched:
# block <reason> [<part>]
# The part is the simple command that the check reads, unless the check gives another, such as a file's path.
block() {
  printf '%s\n' "$1" >&2
  # The log's errors go to /dev/null, because the agent reads the check's errors as the reason. A log that can't be
  # written, such as on a full disk, must not end the check with an error either, because that allows the action.
  if (($# > 1)); then
    log_block "$2" 2>/dev/null || true
  else
    log_block ${simple_command_words[@]+"${simple_command_words[@]}"} 2>/dev/null || true
  fi
  exit 2
}

# Prints the loop's folder of this hook's repository, which is the one whose rules apply, even when the agent works in
# another one.
hook_loop_folder() {
  local git_folders
  git_folders=$(git -C "$hooks_folder" rev-parse --path-format=absolute --git-common-dir --show-toplevel) || return 1
  loop_folder "${git_folders%%$'\n'*}" "${git_folders#*$'\n'}"
}

# Prints the main checkout of this hook's repository, by its physical path, as git prints the folders.
hook_main_checkout() {
  local git_folders
  git_folders=$(git -C "$hooks_folder" rev-parse --path-format=absolute --git-common-dir --show-toplevel) || return 1
  main_checkout "${git_folders%%$'\n'*}" "${git_folders#*$'\n'}"
}

# Succeeds if the action comes from the orchestrator's session, the one that the state file in the loop's folder names,
# and not from a subagent of it, such as a builder, whose input has the session's id too. No hook protects the state
# file, so a builder could write its own session's id there and count as the orchestrator.
is_orchestrator() {
  local loop
  if [[ -n $agent_id ]]; then
    return 1
  fi
  loop=$(hook_loop_folder 2>/dev/null) || return 1
  is_orchestrators_session "$loop" "$session_id"
}

# Adds a line to blocks.log in the loop's folder of this hook's repository, when scripts/hooks/before-tool runs the
# check for a tool's action. A check that runs on its own, as when an agent runs it by hand to compare two versions of
# it, adds no line, because it blocked no action. HOOK_BLOCK_LOG names another file, as in the hooks' tests. The line
# holds the time, the check's file name and the words, with the values that could hold a secret left out, so that its
# reader can tell a real block from a false alarm: log_block <word>...
log_block() {
  local log=${HOOK_BLOCK_LOG:-} line words
  if [[ -z ${is_run_by_hook:-} ]]; then
    return 0
  fi
  if [[ -z $log ]]; then
    log=$(hook_loop_folder)/blocks.log || return 0
    mkdir -p "${log%/*}" || return 0
  fi
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ) $check_name"
  if (($# > 0)) && words=$(words_without_secrets "$@"); then
    line+=" $words"
  fi
  printf '%s\n' "$line" >>"$log"
}

# Sets expanded_path to the path that a word of a command names, with ~ and $HOME filled in as the shell fills them
# in. Fails when another variable, a command's output or another person's home folder still decides the path, because
# the hook can't tell that path before the command runs: expand_home <word>
expand_home() {
  local home=${HOME%/}
  expanded_path=$1
  # The command's words come as written, so these match ~ and $HOME before the shell expands them.
  # shellcheck disable=SC2016,SC2088
  case $1 in
    "~" | '$HOME' | '${HOME}') expanded_path=$home ;;
    "~/"*) expanded_path=$home/${1#"~/"} ;;
    '$HOME/'*) expanded_path=$home/${1#'$HOME/'} ;;
    '${HOME}/'*) expanded_path=$home/${1#'${HOME}/'} ;;
    "~"*) return 1 ;;
  esac
  [[ $expanded_path != *[\$\`]* ]]
}

# Prints a path with symlinks and .. resolved in its folders, also when the file or its folders don't exist yet,
# so that two ways to write one path, such as /tmp and /private/tmp on macOS, give the same result:
# physical_path <path> <folder that a relative path starts from>
physical_path() {
  local path=$1 folder rest="" part
  local -a parts=()
  if [[ $path != /* ]]; then
    path=$2/$path
  fi
  # The deepest folder that exists can hold symlinks. The parts after it can't, so they're resolved as text.
  folder=$path
  while [[ -n $folder && ! -d $folder ]]; do
    rest=/${folder##*/}$rest
    folder=${folder%/*}
  done
  folder=$(CDPATH='' cd -- "${folder:-/}" && pwd -P)
  IFS=/ read -r -a parts <<<"$rest"
  if ((${#parts[@]} > 0)); then
    for part in "${parts[@]}"; do
      case $part in
        "" | .) ;;
        ..)
          folder=${folder%/*}
          folder=${folder:-/}
          ;;
        *) folder=${folder%/}/$part ;;
      esac
    done
  fi
  echo "$folder"
}

# Sets the array tool_paths to the files that the agent's file-writing tool would create or change: the file_path of
# Write and Edit, or each file that a patch of Codex's apply_patch adds, changes or moves to. A patch names each file
# on a line of its own. The files that a patch deletes aren't on the list.
read_tool_paths() {
  local line
  tool_paths=()
  case $tool_name in
    Write | Edit) tool_paths=("$file_path") ;;
    apply_patch)
      while IFS= read -r line; do
        case $line in
          "*** Add File: "*) tool_paths+=("${line#"*** Add File: "}") ;;
          "*** Update File: "*) tool_paths+=("${line#"*** Update File: "}") ;;
          "*** Move to: "*) tool_paths+=("${line#"*** Move to: "}") ;;
        esac
      done <<<"$patch"
      ;;
  esac
}

# Changes command_folder as a cd command in command_words would, or empties it when the hook can't tell the folder.
# command_folder is the folder that the next simple command runs in, and visit_simple_commands keeps a cd in a subshell
# inside it. A check sets it to ${cwd:-$PWD} before the first simple command, because Cursor doesn't send the folder
# the agent works in, and then the hook's own folder counts.
follow_cd() {
  if [[ ${command_words[0]} != cd ]]; then
    return 0
  fi
  if ((${#command_words[@]} != 2)) || [[ ${command_words[1]} == -* ]]; then
    command_folder=""
    return 0
  fi
  change_command_folder "${command_words[1]}"
}

# Changes command_folder to the folder that a word names, starting from command_folder, or empties it when the hook
# can't tell the folder, such as when it doesn't exist yet, or a variable other than HOME decides it:
# change_command_folder <word>
change_command_folder() {
  if ! expand_home "$1"; then
    command_folder=""
    return 0
  fi
  # A check that doesn't read the folder doesn't set it.
  if [[ -n ${command_folder:-} ]]; then
    command_folder=$(CDPATH='' cd -- "$command_folder" 2>/dev/null && CDPATH='' cd -- "$expanded_path" 2>/dev/null && pwd) ||
      command_folder=""
  fi
}

# Reads one of env's options, and sets env_option_words to the number of words that it takes: 2 when its value is the
# next word, otherwise 1. -C changes command_folder for the command that env runs, and visit_simple_commands changes it
# back after that command. Blocks each option that the hooks don't read, because they can't tell then which word is the
# command, as with -S, whose value holds the command, or --uns, which GNU's env reads as --unset:
# read_env_option <option> <next word, or empty>
read_env_option() {
  local option=$1 name value
  env_option_words=1
  case $option in
    --ignore-environment | --null | --debug) return 0 ;;
    --unset | --chdir) name=$option value=$2 env_option_words=2 ;;
    --unset=* | --chdir=*) name=${option%%=*} value=${option#*=} ;;
    *)
      # A group of the options that take no value, as in -i0, can end in one that takes a value. Its value is the rest
      # of the word, as in -iuNAME, or the next word.
      if [[ $option =~ ^-[0iv]*$ ]]; then
        return 0
      fi
      if [[ ! $option =~ ^-[0iv]*([uCP])(.*)$ ]]; then
        block "Blocked: the hooks can't tell which command env runs after its option $option, so they can't check that command. Run the command without that option. The hooks read only env's options -i, -0, -u, -C, -P and -v, and their long forms."
      fi
      name=-${BASH_REMATCH[1]} value=${BASH_REMATCH[2]}
      if [[ -z $value ]]; then
        value=$2 env_option_words=2
      fi
      ;;
  esac
  if [[ $name == -C || $name == --chdir ]]; then
    if [[ -z ${folder_after_command+set} ]]; then
      folder_after_command=${command_folder-}
    fi
    change_command_folder "$value"
  fi
}

# Sets the array command_words to a simple command's words from the command's name on, without the words before it:
# sudo, env, env's options and their values, and the words that set variables. Sets runs_through_env to true if env is
# among them: drop_command_prefixes <word>...
# The shell reads a word as setting a variable when it starts with a name and =, as in NAME=value, but env reads each
# word with = that way.
drop_command_prefixes() {
  local -a prefixed=("$@")
  local i=0 word is_after_env=false reads_env_options=false
  runs_through_env=false
  while ((i < ${#prefixed[@]})); do
    word=${prefixed[i]}
    if [[ $reads_env_options == true && $word == -- ]]; then
      reads_env_options=false
    elif [[ $reads_env_options == true && $word == -* ]]; then
      read_env_option "$word" "${prefixed[i + 1]-}"
      i=$((i + env_option_words - 1))
    elif [[ $word =~ ^[A-Za-z_][A-Za-z0-9_]*= || ($is_after_env == true && $word == *=*) ]]; then
      reads_env_options=false
    elif [[ $word == sudo ]]; then
      is_after_env=false
      reads_env_options=false
    elif [[ ${word##*/} == env ]]; then
      is_after_env=true
      reads_env_options=true
      runs_through_env=true
    else
      break
    fi
    i=$((i + 1))
  done
  command_words=(${prefixed[@]+"${prefixed[@]:i}"})
  command_word_may_expand=(${words_may_expand[@]+"${words_may_expand[@]:i}"})
}

# The options of git before its subcommand that take the next word as their value, as handle_options in git's git.c
# reads them. git reads each of its options only in full, and fails on an option that it doesn't know.
git_options_with_value=" -C -c --git-dir --work-tree --namespace --config-env --attr-source --shallow-file "

# Reads the options at the start of a git command, before its subcommand, and sets these variables:
# - git_folder: the options that make git work in the same repository as the command, starting from the agent's folder:
#   each -C, --git-dir and --work-tree
# - git_setting_names: the name of each setting that -c <name>=<value> or --config-env=<name>=<variable> gives
# - git_subcommand: the index of the word after the options, such as push
read_git_options() {
  local i=1 word option value
  git_folder=(-C "${cwd:-.}") git_setting_names=()
  while [[ ${command_words[i]:-} == -* ]]; do
    word=${command_words[i]}
    # git runs these as its subcommand help or version, with the words after them as its arguments.
    case $word in
      -h | --help | -v | --version) break ;;
    esac
    option=$word value=""
    if [[ $git_options_with_value == *" $word "* ]]; then
      i=$((i + 1))
      value=${command_words[i]:-}
    elif [[ $word == --*=* ]]; then
      option=${word%%=*} value=${word#*=}
    fi
    case $option in
      # git takes each -C relative to the folder before it, and a relative --git-dir or --work-tree relative to the
      # folder after every -C, so a check that asks git about the command's repository passes these on in their order.
      -C | --git-dir | --work-tree) git_folder+=("$option" "$value") ;;
      -c | --config-env) git_setting_names+=("${value%%=*}") ;;
    esac
    i=$((i + 1))
  done
  git_subcommand=$i
}

# Succeeds if a -c or --config-env option that read_git_options read gives a setting: gives_git_setting <name>
# It compares the names in any case, because git reads the section and the key of a name, as in core.hooksPath, in any
# case.
gives_git_setting() {
  local name gives=1
  shopt -s nocasematch
  for name in ${git_setting_names[@]+"${git_setting_names[@]}"}; do
    if [[ $name == "$1" ]]; then
      gives=0
    fi
  done
  shopt -u nocasematch
  return $gives
}

# The flags of git push that take a value, as git push -h lists them.
push_flags_with_value=" --repo --recurse-submodules --receive-pack --exec -o --push-option "

# Reads the words of a git push command after push, and sets these variables:
# - flags: the flags, without their values
# - push_remote: the first word that isn't a flag or a flag's value, which names a remote or a URL, or empty
# - push_refspecs: the other words that aren't flags or flags' values
# Run read_git_options first.
read_push_arguments() {
  local i word
  local -a words=()
  flags=() push_remote="" push_refspecs=()
  for ((i = git_subcommand + 1; i < ${#command_words[@]}; i++)); do
    word=${command_words[i]}
    case $word in
      -*)
        flags+=("$word")
        if [[ $word != *=* && $push_flags_with_value == *" $word "* ]]; then
          i=$((i + 1))
        fi
        ;;
      *) words+=("$word") ;;
    esac
  done
  if ((${#words[@]} > 0)); then
    push_remote=${words[0]}
    push_refspecs=("${words[@]:1}")
  fi
}

# Moves repository flags between a gh command group and its subcommand just after the subcommand,
# so that the checks read the same words and flags in either position. Other flags and groups stay as written.
move_gh_repository_flags() {
  local index=2
  if ((${#command_words[@]} < 3)) || [[ ${command_words[0]##*/} != gh ]]; then
    return 0
  fi
  case ${command_words[1]} in
    pr | issue | label | release) ;;
    *) return 0 ;;
  esac
  while ((index < ${#command_words[@]})); do
    case ${command_words[index]} in
      -R | --repo) index=$((index + 2)) ;;
      --repo=*) index=$((index + 1)) ;;
      *) break ;;
    esac
  done
  # An incomplete flag or a command without a subcommand starts no subcommand check.
  if ((index == 2 || index >= ${#command_words[@]})); then
    return 0
  fi
  command_words=("${command_words[@]:0:2}" "${command_words[index]}"
    "${command_words[@]:2:index-2}" "${command_words[@]:index+1}")
  command_word_may_expand=("${command_word_may_expand[@]:0:2}" "${command_word_may_expand[index]}"
    "${command_word_may_expand[@]:2:index-2}" "${command_word_may_expand[@]:index+1}")
}

# The flags of gh api that take a value, as gh api --help lists them.
api_flags_with_value=" --cache -F --field -H --header --hostname --input -q --jq -X --method -p --preview -f --raw-field -t --template "

# The flags that take a value, as gh issue create --help, gh issue edit --help and gh pr close --help list them.
issue_create_flags_with_value=" -a --assignee --attach --blocked-by --blocking -b --body -F --body-file -l --label -m --milestone --parent -p --project --recover -T --template -t --title --type -R --repo "
issue_edit_flags_with_value=" --add-assignee --add-blocked-by --add-blocking --add-label --add-project --add-sub-issue --attach -b --body -F --body-file -m --milestone --parent --remove-assignee --remove-blocked-by --remove-blocking --remove-label --remove-project --remove-sub-issue -t --title --type -R --repo "
pr_close_flags_with_value=" -c --comment -R --repo "

# Reads the words of a gh command from an index on, and sets these variables:
# - argument: the first word that isn't a flag or a flag's value, or empty
# - repository: --repo and the value of -R or --repo, or empty
# - target: the words that name the argument to other gh commands: the argument, and the words in repository
# - flags: the flags, without their values
# - flag_values: the value of each flag in flags that takes the next word as its value, or empty
# read_arguments <index> <flags that take a value>
read_arguments() {
  local i word
  argument="" repository=() target=() flags=() flag_values=()
  for ((i = $1; i < ${#command_words[@]}; i++)); do
    word=${command_words[i]}
    case $word in
      -R | --repo)
        repository=(--repo "${command_words[i + 1]:-}")
        i=$((i + 1))
        ;;
      --repo=*) repository=(--repo "${word#--repo=}") ;;
      -*)
        flags+=("$word")
        if [[ $word != *=* && $2 == *" $word "* ]]; then
          flag_values+=("${command_words[i + 1]:-}")
          i=$((i + 1))
        else
          flag_values+=("")
        fi
        ;;
      *)
        if [[ -z $argument ]]; then
          argument=$word
          target=("$word")
        fi
        ;;
    esac
  done
  target+=(${repository[@]+"${repository[@]}"})
}

# Succeeds if the gh command in command_words has the flag --help, so that it only prints its help. -h doesn't count,
# because gh repo create and gh repo edit read it as --homepage. gh reads a word after -- as an argument, and the word
# after a flag that takes a value as that value, as in --description --help. The hook doesn't know which flags take a
# value, so --help counts only after a word that isn't a flag, or after a flag that gives its value after =. It counts
# only before the first word with an expansion, such as $text, because that word can become --, or nothing, as in
# --body $text --help.
prints_help() {
  local i previous
  for ((i = 2; i < ${#command_words[@]}; i++)); do
    if [[ -n ${command_word_may_expand[i - 1]:-} ]]; then
      return 1
    fi
    previous=${command_words[i - 1]}
    case ${command_words[i]} in
      --) return 1 ;;
      --help)
        if [[ $previous != -* || $previous == *=* ]]; then
          return 0
        fi
        ;;
    esac
  done
  return 1
}

# Succeeds if read_arguments or read_push_arguments found one of these flags: has_flag <flag>...
has_flag() {
  local flag wanted
  for flag in ${flags[@]+"${flags[@]}"}; do
    for wanted in "$@"; do
      if [[ $flag == "$wanted" ]]; then
        return 0
      fi
    done
  done
  return 1
}

# Succeeds if read_push_arguments found one of these long options of git, or the start of one, as in --tag for --tags,
# because git takes the start of a long option as the option. git fails on a start that other options have too, such as
# --t, so such a start may count: has_git_flag <long option>...
has_git_flag() {
  local flag wanted
  for flag in ${flags[@]+"${flags[@]}"}; do
    if [[ $flag != --?* ]]; then
      continue
    fi
    for wanted in "$@"; do
      if [[ $wanted == "$flag"* ]]; then
        return 0
      fi
    done
  done
  return 1
}

# Sets api_method to the method of the gh api request whose flags read_arguments read, such as POST, in capitals when
# it's GET. Without a method, gh api sends POST if the command adds a field or an input file, and GET otherwise.
read_api_method() {
  local i flag sends_body=false
  api_method=""
  for ((i = 0; i < ${#flags[@]}; i++)); do
    flag=${flags[i]}
    case $flag in
      -X | --method) api_method=${flag_values[i]} ;;
      --method=*) api_method=${flag#--method=} ;;
      -X*)
        api_method=${flag#-X}
        api_method=${api_method#=}
        ;;
      -f* | -F* | --raw-field | --raw-field=* | --field | --field=* | --input | --input=*) sends_body=true ;;
    esac
  done
  if [[ -z $api_method ]]; then
    api_method=GET
    if $sends_body; then
      api_method=POST
    fi
  fi
  # gh sends -X get as GET.
  if [[ $api_method == [Gg][Ee][Tt] ]]; then
    api_method=GET
  fi
}

# The flags of gh pr merge that take a value, as gh pr merge --help lists them.
merge_flags_with_value=" -A --author-email -b --body -F --body-file --match-head-commit -t --subject -R --repo "

# The flags of gh pr create that take a value, as gh pr create --help lists them.
pr_create_flags_with_value=" -a --assignee --attach -B --base -b --body -F --body-file -H --head -l --label -m --milestone -p --project --recover -r --reviewer -T --template -t --title -R --repo "

# Succeeds if command_words opens a pull request with gh pr create, or its alias gh pr new, rather than show the
# command's help or do a dry run. It reads the command's flags with read_pull_request_flags.
opens_pull_request() {
  if [[ ${command_words[0]##*/} != gh || ${command_words[1]:-} != pr ]]; then
    return 1
  fi
  if [[ ${command_words[2]:-} != create && ${command_words[2]:-} != new ]]; then
    return 1
  fi
  read_pull_request_flags "$pr_create_flags_with_value"
}

# Splits a word of a gh command that starts with a dash into the flag and the value that the word gives, as gh reads
# them: split_flag_word <word> <flags that take a value>
# It sets flag, and sets value to the value in the same word, as in --title=Title, or to empty when the word gives
# none. A word can hold several short flags, as in -dc, where each flag but the last takes no value. The first flag that
# takes a value ends the flags, and the rest of the word is its value, as in -tTitle or -dt=Title, which gh reads as the
# title Title. gh drops the = after a short flag only when more follows it, so it reads -t= as the title =. An = after
# a flag that takes no value ends the flags too, and the rest of the word is that flag's value, as in -dd=false. Of such
# a word, flag is the last flag, and flags_before holds the letters of the flags before it, such as d of -dt=Title. It
# sets value_is_next_word to yes when the flag takes a value that the word doesn't give, because the next word is then
# the value, even if it starts with a dash.
split_flag_word() {
  local word=$1 flags_with_value=$2 position letter
  flag=$word value="" value_is_next_word="" flags_before=""
  case $word in
    --*=*)
      flag=${word%%=*}
      value=${word#*=}
      return 0
      ;;
    --*)
      if [[ $flags_with_value == *" $word "* ]]; then
        value_is_next_word=yes
      fi
      return 0
      ;;
  esac
  for ((position = 1; position < ${#word}; position++)); do
    letter=${word:position:1}
    if [[ $letter == = ]]; then
      value=${word:position+1}
      return 0
    fi
    if ((position > 1)); then
      flags_before+=${flag#-}
    fi
    flag=-$letter
    if [[ $flags_with_value == *" -$letter "* ]]; then
      value=${word:position+1}
      case $value in
        "") value_is_next_word=yes ;;
        =?*) value=${value#=} ;;
      esac
      return 0
    fi
  done
}

# Succeeds if gh reads a flag that takes no value, such as --draft, as true, from the value that split_flag_word gives
# it: is_true_flag_value <value>
# The value is empty when the word gives none, as in --draft, and gh then reads the flag as true. gh reads a value after
# =, as in --draft=1, as Go's strconv.ParseBool does.
is_true_flag_value() {
  case $1 in
    "" | 1 | t | T | true | TRUE | True) return 0 ;;
  esac
  return 1
}

# Reads the flags of a gh pr command from command_words, from its fourth word on, and fails when the command shows its
# help or does a dry run: read_pull_request_flags <flags that take a value>
# It sets pr_base, pr_head and pr_title to the values of --base, --head and --title, or to empty when the command
# doesn't give the flag, and pr_has_title to yes when it gives --title, whose value can be empty. It sets pr_is_draft
# to yes when the command opens a draft. It also records expansion syntax in the title and body, and sets the body
# source for read_body.
read_pull_request_flags() {
  local flags_with_value=$1 i word flag value value_is_next_word flags_before
  pr_base="" pr_head="" pr_title="" pr_has_title="" pr_title_may_expand="" pr_is_draft=""
  body_source="" body_word="" body_file="" body_may_expand=""
  for ((i = 3; i < ${#command_words[@]}; i++)); do
    word=${command_words[i]}
    case $word in
      -h | --help | --dry-run) return 1 ;;
      -*) split_flag_word "$word" "$flags_with_value" ;;
      *) continue ;;
    esac
    # A flag before the last in a word takes no value, so a -d there opens a draft.
    if [[ $flags_before == *d* ]]; then
      pr_is_draft=yes
    fi
    if [[ -n $value_is_next_word ]]; then
      i=$((i + 1))
      value=${command_words[i]:-}
    fi
    case $flag in
      -B | --base) pr_base=$value ;;
      -H | --head) pr_head=$value ;;
      -t | --title) pr_title=$value pr_has_title=yes pr_title_may_expand=${command_word_may_expand[i]:-} ;;
      -b | --body) body_source=text body_word=$value body_may_expand=${command_word_may_expand[i]:-} ;;
      -F | --body-file) body_source=file body_file=$value ;;
      -d | --draft)
        # gh keeps the last value that the command gives --draft, so -d --draft=false opens no draft.
        pr_is_draft=""
        if is_true_flag_value "$value"; then
          pr_is_draft=yes
        fi
        ;;
    esac
  done
}

# Sets head_commit to the latest commit of the pull request of opens_pull_request, in the repository of the current
# folder, or fails when it can't find it: the latest commit of the branch that --head names, or HEAD when it names none.
# It also sets head_branch to the name of the pull request's branch when the branch is in this repository. It's empty
# when HEAD points to no branch, and when --head names the branch as <user>:<branch>, because the user's repository
# can be a fork.
find_head_commit() {
  local head_ref=HEAD branch
  branch=${pr_head#*:}
  head_branch=""
  if [[ -n $branch ]]; then
    head_ref=refs/heads/$branch
    if [[ $pr_head != *:* ]]; then
      head_branch=$branch
    fi
  elif [[ -z $pr_head ]]; then
    head_branch=$(git symbolic-ref --quiet --short HEAD) || true
  fi
  head_commit=$(git rev-parse --verify --quiet "$head_ref^{commit}") || return 1
}

# Succeeds if head_commit, the pull request's latest commit, such as find_head_commit finds it, is HEAD. A commit before
# gh in the same command, as in git commit -am ... && gh pr create, then adds the changes on disk to the pull request,
# because the hooks run before the whole command.
is_pull_request_at_head() {
  local head
  head=$(git rev-parse --verify --quiet HEAD) || return 1
  [[ $head_commit == "$head" ]]
}

# Sets base_commit and head_commit to the commits that the pull request of opens_pull_request compares, in the
# repository of the current folder, or fails when it can't find them. The pull request's commits are those on its
# branch, as find_head_commit finds its latest commit, that aren't on the base branch on GitHub. The base branch is the
# one that --base names, or the one that origin/HEAD points to, and the function fetches it from origin first.
find_commits() {
  local base_branch
  base_branch=$pr_base
  if [[ -z $base_branch ]]; then
    # Outside a git repository, git's error says only that the check has nothing to compare.
    base_branch=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null) || return 1
    base_branch=${base_branch#origin/}
  fi
  find_base_commit "$base_branch" || return 1
  find_head_commit
}

# Sets base_commit to the latest commit of a branch on GitHub, which it fetches from origin first, in the repository of
# the current folder, or fails when it can't find it: find_base_commit <branch>
find_base_commit() {
  # The local copy of the base branch can be older than GitHub's. The commits that GitHub's has and the local copy
  # doesn't would then count as the pull request's own, and so would the files that they change.
  # The -- makes git read the branch as a branch, not as an option, even when it starts with a dash, because the
  # command's --base can give any word.
  GIT_TERMINAL_PROMPT=0 git fetch --quiet origin -- "$1" >/dev/null 2>&1 || true
  base_commit=$(git rev-parse --verify --quiet "refs/remotes/origin/$1^{commit}") || return 1
}

# Adds the files that the redirections of a simple command write to the array written_files, which a check empties
# before the first simple command of each command. The hook can't read such a file before the command runs.
record_written_files() {
  local i
  # >& joins two outputs.
  for ((i = 0; i < ${#redirections[@]}; i += 2)); do
    if [[ ${redirections[i]} == *">"* && ${redirections[i]} != *"&" ]]; then
      written_files+=("${redirections[i + 1]}")
    fi
  done
}

# Succeeds if an earlier part of the command writes a file, as a redirection names it: is_written <path>
is_written() {
  local file
  for file in ${written_files[@]+"${written_files[@]}"}; do
    if [[ $file == "$1" ]]; then
      return 0
    fi
  done
  return 1
}

# Sets body to the text of a file, or blocks when the hook can't read the file before the command runs:
# read_body_file <path as the command writes it>
read_body_file() {
  local path=$1
  # The command's words come as written, so ~ is matched before the shell expands it.
  # shellcheck disable=SC2088
  case $path in
    "~/"*) path=$HOME/${path#"~/"} ;;
  esac
  if is_written "$1" || [[ ! -f $path || ! -r $path ]]; then
    block "Blocked: this hook checks the body file before the command runs, and $1 doesn't exist yet or the command writes it. Write the file in a command of its own first, and give its path without variables."
  fi
  body=$(<"$path")
}

give_the_body="Give the body with --body, or write it to a file and give the file with --body-file."

# Sets body to the text that a gh command gives, and body_may_expand to yes for active expansion syntax in it.
# Blocks when the hook can't read the body: read_body <what the hook checks, such as: the issue's headings>
# It reads the body as the command writes it, without running $( ) or filling in variables: from --body or --comment,
# from the file that --body-file names, or from a here-document. It blocks a body that a command in $( ) or backticks
# prints, unless the command is cat alone, reading a here-document, because the hook would check the command's text in
# place of the body. It reads what the check found in the command's words: body_source (empty, text, comment or file),
# body_word, body_file and body_may_expand, as the array words_may_expand of visit_simple_commands holds it. Run it
# from a function that for_each_simple_command calls.
read_body() {
  body=""
  case $body_source in
    "")
      block "Blocked: the command gives no body, so this hook can't check $1. $give_the_body"
      ;;
    text | comment) read_body_word "$1" ;;
    file)
      body_may_expand=""
      if [[ $body_file == - ]]; then
        read_body_input "$1"
      else
        read_body_file "$body_file"
      fi
      ;;
  esac
}

# Sets body to the text of --body or --comment, or blocks when a command in $( ) or backticks prints the text:
# read_body_word <what the hook checks>
# A here-document in the word, as in "$(cat <<'EOF' ... EOF)", gives its text in place of the substitution.
read_body_word() {
  local rest=$body_word number
  if [[ $body_may_expand == command ]]; then
    # gh takes a comment only as text, with no flag for a file.
    if [[ $body_source == comment ]]; then
      block "Blocked: this hook can't run the command in \$( ) or backticks that prints the comment, so it can't check the comment. Give the comment in quotes, or from a here-document, as in --comment \"\$(cat <<'EOF' ... EOF)\"."
    fi
    block "Blocked: this hook can't run the command in \$( ) or backticks that prints the body, so it can't check $1. Write the body to a file in a command of its own first, and give the file with --body-file."
  fi
  if [[ ! $body_word =~ $heredoc_reference ]]; then
    body=$body_word
    return 0
  fi
  body_may_expand=""
  while [[ $rest =~ $heredoc_reference ]]; do
    number=${BASH_REMATCH[1]}
    rest=${rest#*"${BASH_REMATCH[0]}"}
    add_heredoc_to_body "$number"
  done
}

# Sets body to the text that a gh command reads from standard input, from a here-document or from a file, or blocks
# when the hook can't read it: read_body_input <what the hook checks>
read_body_input() {
  local i
  for ((i = 0; i < ${#redirections[@]}; i += 2)); do
    if [[ ${redirections[i]} =~ ^0?\<\<-?$ && ${redirections[i + 1]} =~ $heredoc_reference ]]; then
      add_heredoc_to_body "${BASH_REMATCH[1]}"
      return 0
    fi
    if [[ ${redirections[i]} =~ ^0?\<$ ]]; then
      read_body_file "${redirections[i + 1]}"
      return 0
    fi
  done
  block "Blocked: this hook can't read a body from standard input, other than from a here-document, so it can't check $1. $give_the_body"
}

add_heredoc_to_body() {
  body+=${heredocs[$1]}
  if [[ ${heredoc_note_expands[$1]} == yes ]] && heredoc_has_expansion "${heredocs[$1]}"; then
    body_may_expand=yes
  fi
}

# Succeeds at the start of parameter, command or arithmetic expansion syntax.
starts_expansion() {
  [[ $1 == '`' || ($1 == '$' && $2 == [a-zA-Z0-9_\$\?\!\#\*\@\{\(\[-]) ]]
}

# Quotes are literal in a here-document body. Backslashes protect $, backticks and backslashes.
heredoc_has_expansion() {
  local text=$1 i char next
  for ((i = 0; i < ${#text}; i++)); do
    char=${text:i:1} next=${text:i+1:1}
    if [[ $char == \\ && $next == [\$\`\\] ]]; then
      i=$((i + 1))
    elif starts_expansion "$char" "$next"; then
      return 0
    fi
  done
  return 1
}
