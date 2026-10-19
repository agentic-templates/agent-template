# shellcheck shell=bash
# Functions that the tests in this folder share. A test file sources this file, checks its cases
# with expect_block and expect_allow, and ends with finish_tests. scripts/test-scripts runs every test file at the
# same time as the others, so a test file writes its files only to a folder of its own, such as one from mktemp -d.

hooks_folder=$(cd "${BASH_SOURCE[0]%/*}" && pwd)
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

# Prints a sample of what a tool sends the hook before an action: sample_input <tool> <tool name> <command, patch or path>
# The tool names are Bash, Read, Write, Edit and Codex's apply_patch. Every tool uses Claude Code's field names.
# One jq call builds the whole input, because the cases start jq so often that each call counts.
# shellcheck disable=SC2016 # The $ in the jq programs names jq's variables, such as $value.
sample_input() {
  local tool=$1 tool_name=$2 value=$3 tool_input program
  case $tool_name in
    Bash | apply_patch) tool_input='{command: $value}' ;;
    Read) tool_input='{file_path: $value}' ;;
    Write) tool_input='{file_path: $value, content: "Rules"}' ;;
    Edit) tool_input='{file_path: $value, old_string: "Rules", new_string: "More rules"}' ;;
  esac
  case $tool in
    claude-code)
      program='{session_id: "abc123", transcript_path: "\($home)/.claude/projects/p/abc123.jsonl",
        cwd: $cwd, permission_mode: "default", hook_event_name: "PreToolUse",
        tool_name: $tool_name, tool_input: TOOL_INPUT, tool_use_id: "toolu_01"}'
      ;;
    codex)
      program='{session_id: "abc123", turn_id: "1", transcript_path: null, cwd: $cwd,
        hook_event_name: "PreToolUse", model: "gpt-5", permission_mode: "default",
        tool_name: $tool_name, tool_input: TOOL_INPUT, tool_use_id: "call_01"}'
      ;;
    copilot-cli)
      program='{session_id: "abc123", timestamp: 1759536000000, cwd: $cwd,
        hook_event_name: "PreToolUse", tool_name: $tool_name, tool_input: TOOL_INPUT}'
      ;;
    cursor)
      if [[ $tool_name == Bash ]]; then
        tool_name=Shell
      fi
      program='{conversation_id: "abc123", generation_id: "def456", model: "auto", hook_event_name: "PreToolUse",
        cursor_version: "2.1.0", workspace_roots: [$cwd], transcript_path: null,
        tool_name: $tool_name, tool_input: TOOL_INPUT}'
      ;;
  esac
  jq -n --arg home "$test_home" --arg cwd "$test_cwd" --arg tool_name "$tool_name" --arg value "$value" \
    "${program/TOOL_INPUT/$tool_input}"
}

# Prints the tools that send an action: tools_for <tool name>
# Codex reads files with shell commands and writes them with apply_patch. Cursor's Write also edits.
tools_for() {
  case $1 in
    Bash) echo claude-code codex copilot-cli cursor ;;
    Read | Write) echo claude-code copilot-cli cursor ;;
    Edit) echo claude-code copilot-cli ;;
    apply_patch) echo codex ;;
  esac
}

# Runs one check on an input, with the variables that scripts/hooks/before-tool sets for the checks, and prints what
# the check prints:
# run_check <check, such as posted-text> <input>
run_check() {
  local fields
  fields=$(jq -r -f "$hooks_folder/input.jq" <<<"$2")
  (
    eval "$fields"
    export HOOK_TOOL_NAME HOOK_COMMAND HOOK_FILE_PATH HOOK_CWD LC_ALL=C
    bash "$hooks_folder/$1.check"
  )
}

# Runs the hook on an input, and sets hook_status and hook_message: run_hook <input>
# run_check and run_hook start the script with bash, without the env of its first line, because the cases start them
# about 2,000 times, and env would add a process each time. scripts/hooks/settings.test runs the hook as the tools do.
run_hook() {
  hook_status=0
  hook_message=$(HOME=$test_home bash "$hooks_folder/before-tool" 2>&1 >/dev/null <<<"$1") || hook_status=$?
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

# Every tool's input goes through read_input in helpers.bash, so expect_block and expect_allow run a case only in the
# first tool that sends its action. expect_block_in_each_tool runs a case in each tool, to cover each tool's form of
# input.

# Checks that the hook blocks an action in the first tool that sends it:
# expect_block <tool name> <command, patch or path> <message>
expect_block() {
  local tools
  tools=$(tools_for "$1")
  run_hook "$(sample_input "${tools%% *}" "$1" "$2")"
  expect_result "In ${tools%% *}, the hook for $1 \"$2\"" 2 "$3"
}

# Checks that the hook blocks an action in each tool that sends it:
# expect_block_in_each_tool <tool name> <command, patch or path> <message>
expect_block_in_each_tool() {
  local tool
  for tool in $(tools_for "$1"); do
    run_hook "$(sample_input "$tool" "$1" "$2")"
    expect_result "In $tool, the hook for $1 \"$2\"" 2 "$3"
  done
}

# Checks that the hook allows an action in the first tool that sends it:
# expect_allow <tool name> <command, patch or path>
expect_allow() {
  local tools
  tools=$(tools_for "$1")
  run_hook "$(sample_input "${tools%% *}" "$1" "$2")"
  expect_result "In ${tools%% *}, the hook for $1 \"$2\"" 0 ""
}

# Checks each form of the repository flag before a subcommand against a fixed result:
# expect_repository_flags <exit code> <message> <group> <subcommand> <remaining command words>
# Each check moves the flag to just after the subcommand before it reads the command, with move_gh_repository_flags in
# helpers.bash, so these cases also cover the flag in that place.
expect_repository_flags() {
  local status=$1 message=$2 group=$3 subcommand=$4 rest=$5 repository command
  for repository in '-R jtmpl/other' '--repo jtmpl/other' '--repo=jtmpl/other'; do
    command="gh $group $repository $subcommand $rest"
    run_hook "$(sample_input claude-code Bash "$command")"
    expect_result "The hook for $command" "$status" "$message"
  done
}

finish_tests() {
  if ((failed_cases > 0)); then
    echo "error: $failed_cases of the $case_count cases in $test_name failed. The errors above say which." >&2
    exit 1
  fi
  echo "$test_name passed. Cases: $case_count."
}
