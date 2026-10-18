# shellcheck shell=bash
# Network fixtures vary check and title. Keep the project's other required checks present and passing.
# Read the fixture data with jq, independently of the parser that the scripts under test use.
TEST_PROJECT_CHECKS=$(sed -n '/^ruleset=/,/^JSON$/p' "${BASH_SOURCE[0]%/*}/configure-github" | sed '1d;$d' |
  jq -r '.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[] |
    select(.integration_id == 15368 and .context != "check" and .context != "title") | .context')
export TEST_PROJECT_CHECKS

# Check-run lines in the network fixtures' format: project_check_runs <pull request>
project_check_runs() {
  local number=$1 check id=10000
  while IFS= read -r check; do
    if [[ -n $check ]]; then
      printf 'check\t%s\t%s\t%s\tSUCCESS\n' "$number" "$id" "$check"
      id=$((id + 1))
    fi
  done <<< "$TEST_PROJECT_CHECKS"
}

# A fixture's check must stay separate from a downstream check with the same name: fixture_check_name <base>
fixture_check_name() {
  local name=$1
  while grep -Fxq "$name" <<< "$TEST_PROJECT_CHECKS"; do
    name=${name}_fixture
  done
  printf '%s\n' "$name"
}
