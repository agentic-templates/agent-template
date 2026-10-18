# Prints the fields of a tool's input that the checks use, as shell assignments of the HOOK_ variables that read_input
# in helpers.bash reads. scripts/hooks/before-tool and the hooks' tests run it with jq -r -f.
if type == "object" then
  @sh "HOOK_TOOL_NAME=\(.tool_name // "" | tostring) HOOK_COMMAND=\(.tool_input.command // "" | tostring) HOOK_FILE_PATH=\(.tool_input.file_path // "" | tostring) HOOK_CWD=\(.cwd // "" | tostring)"
else
  error("the input isn't a JSON object")
end
