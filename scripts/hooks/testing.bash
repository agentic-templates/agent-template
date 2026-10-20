# shellcheck shell=bash
# Functions that the tests in this folder share. A test file sources this file, checks its cases
# with expect_block and expect_allow, and ends with finish_tests. scripts/test-scripts runs every test file at the
# same time as the others, so a test file writes its files only to a folder of its own, such as one from mktemp -d.

hooks_folder=$(cd "${BASH_SOURCE[0]%/*}" && pwd)

# Sets the array lowercase_names to the names of the variables that start with a lowercase letter or _.
find_lowercase_names() {
  lowercase_names=("${!a@}" "${!b@}" "${!c@}" "${!d@}" "${!e@}" "${!f@}" "${!g@}" "${!h@}" "${!i@}" "${!j@}" "${!k@}"
    "${!l@}" "${!m@}" "${!n@}" "${!o@}" "${!p@}" "${!q@}" "${!r@}" "${!s@}" "${!t@}" "${!u@}" "${!v@}" "${!w@}" "${!x@}"
    "${!y@}" "${!z@}" "${!_@}")
}

# run_hook and run_check run the hook and the checks in a subshell of the test, which has read helpers.bash already, as
# the subshell of before-tool has, rather than in a bash process of their own, which reads helpers.bash again. That
# saves a process and part of the CPU time of each case, as the pull request of issue #382 measured.
# scripts/hooks/settings.test and block-log.test run the hook as the tools do. Reading helpers.bash turns on options and
# a trap for the checks, so the test gets its own back after it.
find_lowercase_names
names_before_helpers=" names_before_helpers ${lowercase_names[*]} "
test_options=$(set +o)
# shellcheck source=scripts/hooks/helpers.bash
source "$hooks_folder/helpers.bash"
eval "$test_options"
trap - ERR
unset test_options
# A variable KEEP_<name> for each variable with a lowercase name that the hook has in a bash process of its own: bash's,
# the environment's and those that helpers.bash sets. become_hook_shell unsets the test's other variables with lowercase
# names, which are its own. Looking the names up in a list instead took 4 ms more CPU time for each case. The variables
# in capitals all stay, because the test exports those that the hook reads, as a tool's environment holds them.
find_lowercase_names
for name in "${lowercase_names[@]}"; do
  printf -v "KEEP_$name" yes
done
# The functions and variables that helpers.bash defines, which the hook in the test's subshell reads, so finish_tests
# checks that the test defined none of them again. helpers.bash sets check_name again in each file that sources it.
helper_variables=()
for name in "${lowercase_names[@]}"; do
  if [[ $names_before_helpers != *" $name "* && $name != check_name ]]; then
    helper_variables+=("$name")
  fi
done
unset names_before_helpers name
helper_functions=$(compgen -A function -X find_lowercase_names)
# shellcheck disable=SC2086 # The names of the functions, one to a line, become a word each.
helper_definitions=$(declare -f $helper_functions && declare -p "${helper_variables[@]}")

test_name=${0#./}
case_count=0
failed_cases=0

# The hook runs with this home folder, so that the cases don't depend on the machine.
# It's outside /home, because macOS looks up each path in /home over the network.
test_home=/home-for-tests/someone
# The folder the agent works in, which the sample inputs send. A test file can change it.
test_cwd=$test_home/project

# The checks log each block here, in place of .loop/blocks.log, so that the tests' blocks stay out of the
# repository's log.
export HOOK_BLOCK_LOG=/dev/null
# The checks read this list of allowed repositories in place of the one in .loop/, so that the cases can post to
# jtmpl/other.
export HOOK_ALLOWED_REPOSITORIES=${hooks_folder%/hooks}/fixtures/allowed-repositories
# The checks record the gh that they found here, in place of .loop/gh-version, so that the tests' stand-ins for gh stay
# out of the repository's record, and each case runs gh --version.
export HOOK_GH_VERSION_FILE=/dev/null

# Sets quoted_json to a text as a JSON string, in quotes: quote_json <text>
# The cases build their inputs without jq, because starting jq took 4 ms of CPU time for each case.
quote_json() {
  local LC_ALL=C text=$1 character code
  text=${text//\\/\\\\}
  text=${text//\"/\\\"}
  # JSON escapes each control character, such as a line break, which this writes as \u000a.
  while [[ $text =~ [[:cntrl:]] ]]; do
    character=${BASH_REMATCH[0]}
    printf -v code '\\u%04x' "'$character"
    text=${text//"$character"/$code}
  done
  quoted_json="\"$text\""
}

# Sets sample_input_json to a sample of what a tool sends the hook before an action:
# make_sample_input <tool> <tool name> <command, patch or path>
# The tool names are Bash, Read, Write, Edit and Codex's apply_patch. Every tool uses Claude Code's field names.
# scripts/hooks/testing.test checks that it makes the input that jq made from the same values.
make_sample_input() {
  local tool=$1 tool_name=$2 tool_input cwd transcript_path
  quote_json "$3"
  case $tool_name in
    Bash | apply_patch) tool_input="{\"command\": $quoted_json}" ;;
    Read) tool_input="{\"file_path\": $quoted_json}" ;;
    Write) tool_input="{\"file_path\": $quoted_json, \"content\": \"Rules\"}" ;;
    Edit) tool_input="{\"file_path\": $quoted_json, \"old_string\": \"Rules\", \"new_string\": \"More rules\"}" ;;
  esac
  if [[ $tool == cursor && $tool_name == Bash ]]; then
    tool_name=Shell
  fi
  quote_json "$tool_name"
  tool_name=$quoted_json
  quote_json "$test_cwd"
  cwd=$quoted_json
  case $tool in
    claude-code)
      quote_json "$test_home/.claude/projects/p/abc123.jsonl"
      transcript_path=$quoted_json
      sample_input_json="{\"session_id\": \"abc123\", \"transcript_path\": $transcript_path, \"cwd\": $cwd,
        \"permission_mode\": \"default\", \"hook_event_name\": \"PreToolUse\", \"tool_name\": $tool_name,
        \"tool_input\": $tool_input, \"tool_use_id\": \"toolu_01\"}"
      ;;
    codex)
      sample_input_json="{\"session_id\": \"abc123\", \"turn_id\": \"1\", \"transcript_path\": null, \"cwd\": $cwd,
        \"hook_event_name\": \"PreToolUse\", \"model\": \"gpt-5\", \"permission_mode\": \"default\",
        \"tool_name\": $tool_name, \"tool_input\": $tool_input, \"tool_use_id\": \"call_01\"}"
      ;;
    copilot-cli)
      sample_input_json="{\"session_id\": \"abc123\", \"timestamp\": 1759536000000, \"cwd\": $cwd,
        \"hook_event_name\": \"PreToolUse\", \"tool_name\": $tool_name, \"tool_input\": $tool_input}"
      ;;
    cursor)
      sample_input_json="{\"conversation_id\": \"abc123\", \"generation_id\": \"def456\", \"model\": \"auto\",
        \"hook_event_name\": \"PreToolUse\", \"cursor_version\": \"2.1.0\", \"workspace_roots\": [$cwd],
        \"transcript_path\": null, \"tool_name\": $tool_name, \"tool_input\": $tool_input}"
      ;;
  esac
}

# Prints a sample of what a tool sends the hook before an action, as make_sample_input makes it:
# sample_input <tool> <tool name> <command, patch or path>
sample_input() {
  make_sample_input "$@"
  printf '%s\n' "$sample_input_json"
}

# Sets tools to the tools that send an action, separated by spaces: find_tools <tool name>
# Codex reads files with shell commands and writes them with apply_patch. Cursor's Write also edits.
find_tools() {
  case $1 in
    Bash) tools="claude-code codex copilot-cli cursor" ;;
    Read | Write) tools="claude-code copilot-cli cursor" ;;
    Edit) tools="claude-code copilot-cli" ;;
    apply_patch) tools=codex ;;
  esac
}

# Turns the subshell that runs the hook into what a bash process of the hook would be: it turns off the test's options,
# forgets the paths of the commands that the test ran, so that the hook finds a stand-in that the test added since, and
# unsets the test's variables with lowercase names. A test function's local variable can hide a global one with the
# same name, so it unsets each name until it has no value.
become_hook_shell() {
  set +eEuo pipefail
  trap - ERR
  hash -r
  find_lowercase_names
  set -- "${lowercase_names[@]}"
  while (($# > 0)); do
    MARKER=KEEP_$1
    if [[ -z ${!MARKER+set} ]]; then
      unset -v "$1" 2>/dev/null
      while [[ -n ${!1+set} ]] && unset -v "$1" 2>/dev/null; do
        :
      done
    fi
    shift
  done
  unset lowercase_names MARKER
}

# Runs one check on an input, with the variables that scripts/hooks/before-tool sets for the checks, and prints what
# the check prints:
# run_check <check, such as posted-text> <input>
run_check() {
  local fields
  fields=$(jq -r -f "$hooks_folder/input.jq" <<<"$2")
  (
    eval "$fields"
    export HOOK_TOOL_NAME HOOK_COMMAND HOOK_FILE_PATH HOOK_CWD HOOK_SESSION_ID HOOK_AGENT_ID LC_ALL=C
    become_hook_shell
    # With set -e, a check that bash can't parse ends with code 2, as it would in a process of its own.
    set -e
    # shellcheck source=/dev/null # ShellCheck checks each check as a file of its own.
    source "$hooks_folder/$1.check"
  )
}

# Runs the hook on an input, and sets hook_status and hook_message: run_hook <input>
run_hook() {
  hook_status=0
  hook_message=$(
    export HOME=$test_home
    become_hook_shell
    # shellcheck source=/dev/null # ShellCheck checks before-tool as a file of its own.
    source "$hooks_folder/before-tool" 2>&1 >/dev/null <<<"$1"
  ) || hook_status=$?
}

# Checks the hook's result for one case: expect_result <description> <exit code> <message>
expect_result() {
  local description=$1 status=$2 message=$3
  case_count=$((case_count + 1))
  if [[ $hook_status != "$status" || $hook_message != "$message" ]]; then
    echo "error: $description must exit with code $status and the message \"$message\", but it exited with code $hook_status and the message \"$hook_message\"." >&2
    failed_cases=$((failed_cases + 1))
  fi
}

# Checks that the hook blocks an action in the first tool that sends it, which is enough because every tool's input
# goes through read_input in helpers.bash: expect_block <tool name> <command, patch or path> <message>
expect_block() {
  local tools
  find_tools "$1"
  make_sample_input "${tools%% *}" "$1" "$2"
  run_hook "$sample_input_json"
  expect_result "In ${tools%% *}, the hook for $1 \"$2\"" 2 "$3"
}

# Checks that the hook blocks an action in each tool that sends it:
# expect_block_in_each_tool <tool name> <command, patch or path> <message>
expect_block_in_each_tool() {
  local tools tool
  find_tools "$1"
  for tool in $tools; do
    make_sample_input "$tool" "$1" "$2"
    run_hook "$sample_input_json"
    expect_result "In $tool, the hook for $1 \"$2\"" 2 "$3"
  done
}

# Checks that the hook allows an action in the first tool that sends it:
# expect_allow <tool name> <command, patch or path>
expect_allow() {
  local tools
  find_tools "$1"
  make_sample_input "${tools%% *}" "$1" "$2"
  run_hook "$sample_input_json"
  expect_result "In ${tools%% *}, the hook for $1 \"$2\"" 0 ""
}

# Each wrapper that drop_command_prefixes in command-prefixes.bash drops before a command, with options where it
# takes them
wrappers=("timeout -k 5 --signal=TERM 10m" "time -p" "nice -n 5" nohup "stdbuf -oL -e 0" "command -p" builtin noglob xargs)

# The message that blocks a cd after time, command or noglob, as follow_cd in helpers.bash describes it
cd_after_wrapper="Blocked: the hooks can't tell which folder this cd leaves the shell in, because after time, command or noglob the shell may run the program cd, which changes no folder, instead of its own. Run cd without the word before it."

# Checks that the hook blocks a command behind each wrapper in wrappers: expect_block_behind_wrappers <command> <message>
expect_block_behind_wrappers() {
  local wrapper
  for wrapper in "${wrappers[@]}"; do
    expect_block Bash "$wrapper $1" "$2"
  done
}

# Runs the hook on an action of a session, or of a subagent of it, in the first tool that sends the action, and checks
# its result: expect_action_from <session> <agent id or ""> <tool name> <command, patch or path> <exit code> <message>
# A subagent's input has the id of its session, and its own in agent_id.
expect_action_from() {
  local tools input
  find_tools "$3"
  make_sample_input "${tools%% *}" "$3" "$4"
  input=$(jq -c --arg session "$1" --arg agent "$2" \
    '.session_id = $session | if $agent == "" then . else . + {agent_id: $agent, agent_type: "builder"} end' \
    <<<"$sample_input_json")
  run_hook "$input"
  expect_result "For session $1${2:+ and its agent $2}, the hook for $3 \"$4\"" "$5" "$6"
}

# Runs the hook on a command of a session, or of a subagent of it, and checks its result:
# expect_from <session> <agent id or ""> <command> <exit code> <message>
expect_from() {
  expect_action_from "$1" "$2" Bash "$3" "$4" "$5"
}

# Checks each form of the repository flag before a subcommand against a fixed result:
# expect_repository_flags <exit code> <message> <group> <subcommand> <remaining command words>
# Each check moves the flag to just after the subcommand before it reads the command, with
# move_flags_after_gh_subcommand in helpers.bash, so these cases also cover the flag in that place.
expect_repository_flags() {
  local status=$1 message=$2 group=$3 subcommand=$4 rest=$5 repository command
  for repository in '-R jtmpl/other' -Rjtmpl/other -R=jtmpl/other '--repo jtmpl/other' --repo=jtmpl/other; do
    command="gh $group $repository $subcommand $rest"
    make_sample_input claude-code Bash "$command"
    run_hook "$sample_input_json"
    expect_result "The hook for $command" "$status" "$message"
  done
}

# Prints the message that blocks a git push whose remote or refspec the shell expands, as read_push_arguments in
# helpers.bash reads them: expanded_push_word <the word as the command writes it, without its quotes>
expanded_push_word() {
  echo "Blocked: the shell expands $1 in this git push, so this hook can't tell which remote or branch it names. Write the remote and each branch in plain words, as in git push origin <branch>."
}

# Checks a gh command with words of its subcommand before the group, between the group and the subcommand, and just
# after the subcommand, against a fixed result:
# expect_before_and_after_subcommand <exit code> <message> <group> <words> <subcommand> <remaining words>
# The group can be a nested group, such as repo deploy-key.
expect_before_and_after_subcommand() {
  local status=$1 message=$2 group=$3 words=$4 subcommand=$5 rest=$6 command
  for command in "gh $words $group $subcommand $rest" "gh $group $words $subcommand $rest" \
    "gh $group $subcommand $words $rest"; do
    make_sample_input claude-code Bash "$command"
    run_hook "$sample_input_json"
    expect_result "The hook for $command" "$status" "$message"
  done
}

finish_tests() {
  # shellcheck disable=SC2086 # The names of the functions, one to a line, become a word each.
  if [[ $(declare -f $helper_functions && declare -p "${helper_variables[@]}") != "$helper_definitions" ]]; then
    echo "error: $test_name defines a function or a variable of helpers.bash again, so the hook in its cases used the test's instead. Rename the test's function or variable." >&2
    exit 1
  fi
  if ((failed_cases > 0)); then
    echo "error: $failed_cases of the $case_count cases in $test_name failed. The errors above say which." >&2
    exit 1
  fi
  echo "$test_name passed. Cases: $case_count."
}
