# shellcheck shell=bash
# Functions that the tests in this folder share. A test file sources this file, checks its cases
# with expect_block and expect_allow, and ends with finish_tests. scripts/test-hooks runs every test file at the
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

# Prints a sample of what a tool sends the hook before an action: sample_input <tool> <tool name> <command, patch or path>
# The tool names are Bash, Read, Write, Edit and Codex's apply_patch. Every tool uses Claude Code's field names.
sample_input() {
  local tool=$1 tool_name=$2 value=$3 tool_input
  case $tool_name in
    Bash | apply_patch) tool_input=$(jq -n --arg command "$value" '{command: $command}') ;;
    Read) tool_input=$(jq -n --arg path "$value" '{file_path: $path}') ;;
    Write) tool_input=$(jq -n --arg path "$value" '{file_path: $path, content: "Rules"}') ;;
    Edit) tool_input=$(jq -n --arg path "$value" '{file_path: $path, old_string: "Rules", new_string: "More rules"}') ;;
  esac
  case $tool in
    claude-code)
      jq -n --arg home "$test_home" --arg cwd "$test_cwd" --arg tool_name "$tool_name" --argjson tool_input "$tool_input" \
        '{session_id: "abc123", transcript_path: "\($home)/.claude/projects/p/abc123.jsonl",
          cwd: $cwd, permission_mode: "default", hook_event_name: "PreToolUse",
          tool_name: $tool_name, tool_input: $tool_input, tool_use_id: "toolu_01"}'
      ;;
    codex)
      jq -n --arg cwd "$test_cwd" --arg tool_name "$tool_name" --argjson tool_input "$tool_input" \
        '{session_id: "abc123", turn_id: "1", transcript_path: null, cwd: $cwd,
          hook_event_name: "PreToolUse", model: "gpt-5", permission_mode: "default",
          tool_name: $tool_name, tool_input: $tool_input, tool_use_id: "call_01"}'
      ;;
    copilot-cli)
      jq -n --arg cwd "$test_cwd" --arg tool_name "$tool_name" --argjson tool_input "$tool_input" \
        '{session_id: "abc123", timestamp: 1759536000000, cwd: $cwd,
          hook_event_name: "PreToolUse", tool_name: $tool_name, tool_input: $tool_input}'
      ;;
    cursor)
      if [[ $tool_name == Bash ]]; then
        tool_name=Shell
      fi
      jq -n --arg cwd "$test_cwd" --arg tool_name "$tool_name" --argjson tool_input "$tool_input" \
        '{conversation_id: "abc123", generation_id: "def456", model: "auto", hook_event_name: "PreToolUse",
          cursor_version: "2.1.0", workspace_roots: [$cwd], transcript_path: null,
          tool_name: $tool_name, tool_input: $tool_input}'
      ;;
  esac
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

# Runs one check on an input, as scripts/hooks/before-tool runs it, and prints what the check prints:
# run_check <check, such as posted-text> <input>
run_check() {
  local fields
  fields=$(jq -r -f "$hooks_folder/input.jq" <<<"$2")
  (
    eval "$fields"
    export HOOK_TOOL_NAME HOOK_COMMAND HOOK_FILE_PATH HOOK_CWD LC_ALL=C
    "$hooks_folder/$1.check"
  )
}

# Runs the hook on an input, and sets hook_status and hook_message: run_hook <input>
run_hook() {
  hook_status=0
  hook_message=$(HOME=$test_home "$hooks_folder/before-tool" 2>&1 >/dev/null <<<"$1") || hook_status=$?
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

# Every tool's input goes through read_input in helpers.bash, so a case runs only in the first tool that sends its action,
# unless the test file sets every_tool to yes. The tests for secret files and instruction files do, to cover each
# tool's form of input.
every_tool=""

# Prints the tools that a case runs in: case_tools <tool name>
case_tools() {
  local tools
  tools=$(tools_for "$1")
  if [[ -n $every_tool ]]; then
    echo "$tools"
  else
    echo "${tools%% *}"
  fi
}

# Checks that the hook blocks an action in the tools that case_tools prints:
# expect_block <tool name> <command, patch or path> <message>
expect_block() {
  local tool
  for tool in $(case_tools "$1"); do
    run_hook "$(sample_input "$tool" "$1" "$2")"
    expect_result "In $tool, the hook for $1 \"$2\"" 2 "$3"
  done
}

# Checks that the hook allows an action in the tools that case_tools prints: expect_allow <tool name> <command, patch or path>
expect_allow() {
  local tool
  for tool in $(case_tools "$1"); do
    run_hook "$(sample_input "$tool" "$1" "$2")"
    expect_result "In $tool, the hook for $1 \"$2\"" 0 ""
  done
}

# Checks all repository flag forms on either side of a subcommand against a fixed result:
# expect_repository_flags <exit code> <message> <group> <subcommand> <remaining command words>
expect_repository_flags() {
  local status=$1 message=$2 group=$3 subcommand=$4 rest=$5 repository command
  for repository in '-R jtmpl/other' '--repo jtmpl/other' '--repo=jtmpl/other'; do
    for command in "gh $group $repository $subcommand $rest" "gh $group $subcommand $repository $rest"; do
      run_hook "$(sample_input claude-code Bash "$command")"
      expect_result "The hook for $command" "$status" "$message"
    done
  done
}

finish_tests() {
  if ((failed_cases > 0)); then
    echo "error: $failed_cases of the $case_count cases in $test_name failed. The errors above say which." >&2
    exit 1
  fi
  echo "$test_name passed. Cases: $case_count."
}
