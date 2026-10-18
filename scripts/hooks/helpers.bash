# shellcheck shell=bash
# Functions that the checks in this folder share. A check sources this file first.

# A check blocks only with exit code 2. Any other failure, such as a jq error, ends the check
# with code 1, which allows the action, because a broken hook must never stop work.
# Some commands fail with code 2 themselves, so the trap replaces their code.
set -eEuo pipefail
trap 'exit 1' ERR

# Sets these variables from the fields of the tool's input, which scripts/hooks/before-tool reads once for all the
# checks. Every tool sends Claude Code's form: one JSON object, with the tool in tool_name and its input in tool_input.
# - tool_name: the tool, such as Bash or Read. Cursor's Shell counts as Bash.
# - shell_command: the command that Bash runs, or empty.
# - patch: the patch text that Codex's apply_patch applies, or empty.
# - file_path: the file that Read, Write or Edit uses, or empty.
# - cwd: the folder the agent works in, or empty if the tool doesn't send it.
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
}

# Blocks the action, and tells the agent why: block <reason>
block() {
  printf '%s\n' "$1" >&2
  exit 2
}

# Sets the array command_words to a simple command's words from the command's name on, without the sudo,
# env and NAME=value words before it: drop_command_prefixes <word>...
drop_command_prefixes() {
  command_words=("$@")
  command_word_may_expand=(${words_may_expand[@]+"${words_may_expand[@]}"})
  while ((${#command_words[@]} > 0)); do
    if [[ ${command_words[0]} != sudo && ${command_words[0]} != env && ! ${command_words[0]} =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      return 0
    fi
    command_words=("${command_words[@]:1}")
    command_word_may_expand=("${command_word_may_expand[@]:1}")
  done
}

# Moves repository flags between a gh command group and its subcommand just after the subcommand,
# so that the checks read the same words and flags in either position. Other flags and groups stay as written.
move_gh_repository_flags() {
  local index=2
  if ((${#command_words[@]} < 3)) || [[ ${command_words[0]##*/} != gh ]]; then
    return 0
  fi
  case ${command_words[1]} in
    pr | issue | label) ;;
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
# - target: the words that name the argument to other gh commands: the argument, and -R or --repo with its value
# - flags: the flags, without their values
# - flag_values: the value of each flag in flags that takes the next word as its value, or empty
# read_arguments <index> <flags that take a value>
read_arguments() {
  local i word
  local -a repository=()
  argument="" target=() flags=() flag_values=()
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

# Succeeds if read_arguments found one of these flags: has_flag <flag>...
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

# The flags of gh pr create that take a value, as gh pr create --help lists them.
pr_create_flags_with_value=" -a --assignee --attach -B --base -b --body -F --body-file -H --head -l --label -m --milestone -p --project --recover -r --reviewer -T --template -t --title -R --repo "

# Succeeds if command_words opens a pull request with gh pr create, or its alias gh pr new, rather than show the
# command's help or do a dry run. It sets pr_base, pr_head and pr_title to the values of --base, --head and --title,
# or to empty when the command doesn't give the flag, and pr_has_title to yes when it gives --title, whose value can
# be empty. It also records expansion syntax in the title and body, and sets the body source for read_body.
# The word after a flag that takes a value is that value, even if it starts with a dash.
opens_pull_request() {
  local i word flag value
  if [[ ${command_words[0]##*/} != gh || ${command_words[1]:-} != pr ]]; then
    return 1
  fi
  if [[ ${command_words[2]:-} != create && ${command_words[2]:-} != new ]]; then
    return 1
  fi
  pr_base="" pr_head="" pr_title="" pr_has_title="" pr_title_may_expand="" body_source="" body_word="" body_file="" body_may_expand=""
  for ((i = 3; i < ${#command_words[@]}; i++)); do
    word=${command_words[i]}
    case $word in
      -h | --help | --dry-run) return 1 ;;
      --*=*)
        flag=${word%%=*}
        value=${word#*=}
        ;;
      -*)
        flag=$word
        value=""
        if [[ $pr_create_flags_with_value == *" $word "* ]]; then
          value=${command_words[i + 1]:-}
          i=$((i + 1))
        fi
        ;;
      *) continue ;;
    esac
    case $flag in
      -B | --base) pr_base=$value ;;
      -H | --head) pr_head=$value ;;
      -t | --title) pr_title=$value pr_has_title=yes pr_title_may_expand=${command_word_may_expand[i]:-} ;;
      -b | --body) body_source=text body_word=$value body_may_expand=${command_word_may_expand[i]:-} ;;
      -F | --body-file) body_source=file body_file=$value ;;
    esac
  done
}

# Finds the end and contents of a $( ) or backtick substitution, without running it.
# read_substitution <text> <opening character's index>
read_substitution() {
  local text=$1 start=$2 index=$2 char next quote="" depth=1
  substitution_body=""
  if [[ ${text:start:1} == '`' ]]; then
    index=$((index + 1))
    while ((index < ${#text})); do
      char=${text:index:1} next=${text:index+1:1}
      if [[ $char == '`' ]]; then
        substitution_end=$index
        return 0
      fi
      # Backticks remove these escapes before the command inside them is parsed.
      if [[ $char == \\ && $next == [\$\`\\] ]]; then
        substitution_body+=$next
        index=$((index + 2))
      else
        substitution_body+=$char
        index=$((index + 1))
      fi
    done
  else
    index=$((index + 2))
    while ((index < ${#text})); do
      char=${text:index:1} next=${text:index+1:1}
      if [[ $quote == "'" ]]; then
        if [[ $char == "'" ]]; then
          quote=""
        fi
      elif [[ $char == \\ ]]; then
        index=$((index + 1))
      elif [[ $char == '`' || ($char == '$' && $next == '(') ]]; then
        read_substitution "$text" "$index"
        index=$substitution_end
      elif [[ $char == '"' ]]; then
        if [[ $quote == '"' ]]; then
          quote=""
        else
          quote='"'
        fi
      elif [[ -z $quote ]]; then
        case $char in
          "'") quote="'" ;;
          '(') depth=$((depth + 1)) ;;
          ')')
            depth=$((depth - 1))
            if ((depth == 0)); then
              break
            fi
            ;;
        esac
      fi
      index=$((index + 1))
    done
    substitution_body=${text:start+2:index-start-2}
  fi
  substitution_end=$index
}

# Here-document quotes are ordinary text. Only backslashes can make a substitution literal.
check_heredoc_substitutions() {
  local function=$1 text=$2 index char next substitution_body substitution_end
  for ((index = 0; index < ${#text}; index++)); do
    char=${text:index:1} next=${text:index+1:1}
    if [[ $char == \\ && $next == [\$\`\\] ]]; then
      index=$((index + 1))
    elif [[ $char == '`' || ($char == '$' && $next == '(') ]]; then
      read_substitution "$text" "$index"
      split_simple_commands "$function" "$substitution_body"
      index=$substitution_end
    fi
  done
}

# Splits shell text and checks each simple command, including commands inside substitutions:
# for_each_simple_command <function> <command>
# Here-document bodies stay available to the checks that read issue and pull request bodies.
for_each_simple_command() {
  local function=$1 text=$2 command_text index
  local -a heredocs=() heredoc_expands=() heredoc_note_expands=()
  split_heredocs "$text"
  heredoc_note_expands=(${heredoc_expands[@]+"${heredoc_expands[@]}"})
  split_simple_commands "$function" "$command_text"
  for ((index = 0; index < ${#heredocs[@]}; index++)); do
    if [[ ${heredoc_expands[index]} == yes ]]; then
      check_heredoc_substitutions "$function" "${heredocs[index]}"
    fi
  done
}

# Splits a shell command into simple commands, and calls a function with the words of each one:
# split_simple_commands <function> <command>
# Simple commands end at &&, ||, ;, |, &, a parenthesis and a line break. Words end at spaces and tabs.
# Quotes and backslashes are removed as the shell removes them, but nothing is expanded, so "$HOME" stays $HOME.
# Redirections, such as "< .env" or "2>/dev/null", aren't words. Before each call, the array redirections
# holds them as pairs: the operator, such as < or 2>, then its target.
# It leaves substitution text in the surrounding word, because body checks inspect the original text.
split_simple_commands() {
  local function=$1 text=$2
  local length=${#text} i char next
  local word="" has_word=false quote="" descriptor="" operator="" starts_redirection word_may_expand=""
  local substitution_body substitution_end rest chunk
  local -a redirections=()
  local -a words=() words_may_expand=()

  for ((i = 0; i <= length; i++)); do
    # Inside quotes, the text up to the next character that matters joins the word in one step, because adding one
    # character at a time to a long word, such as a body in quotes, takes time that grows with the square of its
    # length. In single quotes only the quote and the marker matter, and in double quotes the quote, \, $ and `.
    if [[ -n $quote ]] && ((i < length)); then
      rest=${text:i}
      if [[ $quote == "'" ]]; then
        chunk=${rest%%[\'"$marker"]*}
      else
        chunk=${rest%%[\"\\\$\`]*}
      fi
      if [[ -n $chunk ]]; then
        word+=$chunk
        i=$((i + ${#chunk} - 1))
        continue
      fi
    fi

    char=${text:i:1}
    next=${text:i+1:1}

    # The extractor also recognizes heredoc-shaped literal words. Only their notes use this quote context.
    if [[ $quote == "'" && $char == "$marker" ]]; then
      rest=${text:i}
      if [[ $rest =~ ^$heredoc_reference ]]; then
        heredoc_note_expands[BASH_REMATCH[1]]=no
      fi
    fi

    if [[ $quote != "'" ]] && starts_expansion "$char" "$next"; then
      word_may_expand=yes
    fi

    if [[ $quote != "'" && ($char == '`' || ($char == '$' && $next == '(')) ]]; then
      read_substitution "$text" "$i"
      split_simple_commands "$function" "$substitution_body"
      word+=${text:i:substitution_end-i+1}
      has_word=true
      i=$substitution_end
      continue
    fi

    if [[ -n $quote && i -lt length ]]; then
      if [[ $char == "$quote" ]]; then
        quote=""
      elif [[ $quote == '"' && $char == "\\" && $next == [\"\\\$\`] ]]; then
        word+=$next
        i=$((i + 1))
      else
        word+=$char
      fi
      continue
    fi

    if ((i < length)); then
      case $char in
        "'" | '"')
          quote=$char
          has_word=true
          continue
          ;;
        \\)
          # A backslash at the end of a line joins the line to the next one.
          if [[ $next != $'\n' ]]; then
            word+=$next
            has_word=true
          fi
          i=$((i + 1))
          continue
          ;;
        '#')
          if ! $has_word; then
            # A comment runs to the end of the line.
            while [[ -n ${text:i+1:1} && ${text:i+1:1} != $'\n' ]]; do
              i=$((i + 1))
            done
            continue
          fi
          ;;
      esac
      if [[ $char != [[:space:]\;\&\|\(\)\<\>] ]]; then
        word+=$char
        has_word=true
        continue
      fi
    fi

    # The character ends the current word.
    starts_redirection=false
    if [[ $char == [\<\>] || ($char == '&' && $next == '>') ]]; then
      starts_redirection=true
    fi
    if $has_word; then
      if [[ -n $operator ]]; then
        redirections+=("$operator" "$word")
        operator=""
      elif $starts_redirection && [[ $word =~ ^[0-9]+$ ]]; then
        descriptor=$word
      else
        words+=("$word")
        words_may_expand+=("$word_may_expand")
      fi
      word="" has_word=false word_may_expand=""
    fi

    if $starts_redirection; then
      operator=$descriptor$char
      descriptor=""
      while [[ ${text:i+1:1} == [\<\>] ]]; do
        i=$((i + 1))
        operator+=${text:i:1}
      done
      if [[ ${text:i+1:1} == [\&\|-] ]]; then
        i=$((i + 1))
        operator+=${text:i:1}
      fi
    elif ((i == length)) || [[ $char != [[:blank:]] ]]; then
      # A line break, ;, &, |, a parenthesis or the end of the command ends the simple command.
      if ((${#words[@]} > 0)); then
        "$function" "${words[@]}"
      fi
      words=()
      words_may_expand=()
      redirections=()
      operator=""
    fi
  done
}

# Delimiter words can mix quoted, escaped and unquoted parts, such as E"OF".
heredoc_operator="<<(-?)[[:blank:]]*(([^[:space:]<>;&|()'\"\\\\]|'[^']*'|\"[^\"]*\"|\\\\.)+)"
# split_heredocs puts this character around a here-document's number. Commands don't contain it.
marker=$'\037'
heredoc_reference="$marker([0-9]+)$marker"

# Takes the bodies of the here-documents out of a command, because a body holds text and not commands, and a quote in
# the text would end the word that holds it, such as the --body word in --body "$(cat <<'EOF' ... EOF)":
# split_heredocs <command>
# Sets command_text to the command without the bodies, with the marker, a number and the marker in place of each
# delimiter, and heredocs to the bodies, in order. It takes each << as the start of a here-document, also in quoted
# text outside $( ), where the shell doesn't.
split_heredocs() {
  local line rest match prefix body body_line index=0 count j delimiter raw_delimiter dash character quote k expands
  local -a lines=() dashes=() delimiters=() expansions=()
  command_text=""
  heredocs=()
  heredoc_expands=()
  while IFS= read -r line; do
    lines+=("$line")
  done <<<"$1"
  count=${#lines[@]}
  while ((index < count)); do
    rest=${lines[index]}
    index=$((index + 1))
    line=""
    dashes=()
    delimiters=()
    expansions=()
    while [[ $rest =~ $heredoc_operator ]]; do
      match=${BASH_REMATCH[0]}
      prefix=${rest%%"$match"*}
      rest=${rest#*"$match"}
      # <<< starts a string, not a here-document.
      if [[ $prefix == *"<" ]]; then
        line+=$prefix$match
        continue
      fi
      dash=${BASH_REMATCH[1]} raw_delimiter=${BASH_REMATCH[2]}
      delimiter="" quote="" expands=yes
      for ((k = 0; k < ${#raw_delimiter}; k++)); do
        character=${raw_delimiter:k:1}
        if [[ -n $quote ]]; then
          if [[ $character == "$quote" ]]; then
            quote=""
          else
            delimiter+=$character
          fi
        elif [[ $character == [\'\"] ]]; then
          quote=$character expands=no
        elif [[ $character == \\ ]]; then
          k=$((k + 1))
          delimiter+=${raw_delimiter:k:1}
          expands=no
        else
          delimiter+=$character
        fi
      done
      line+="$prefix<<$dash$marker$((${#heredocs[@]} + ${#delimiters[@]}))$marker"
      dashes+=("$dash")
      delimiters+=("$delimiter")
      expansions+=("$expands")
    done
    command_text+=$line$rest$'\n'
    # The bodies follow the line, in the order of their operators. <<- removes the tabs at the start of each line.
    for ((j = 0; j < ${#delimiters[@]}; j++)); do
      body=""
      while ((index < count)); do
        body_line=${lines[index]}
        index=$((index + 1))
        if [[ -n ${dashes[j]} ]]; then
          body_line=${body_line#"${body_line%%[!$'\t']*}"}
        fi
        if [[ $body_line == "${delimiters[j]}" ]]; then
          break
        fi
        body+=$body_line$'\n'
      done
      heredocs+=("$body")
      heredoc_expands+=("${expansions[j]}")
    done
  done
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
# It reads the body as the command writes it, without running $( ) or filling in variables: from --body, from the
# file that --body-file names, or from a here-document. It reads what the check found in the command's words:
# body_source (empty, text or file), body_word and body_file. Run split_heredocs on the command first.
read_body() {
  local rest i index
  body=""
  case $body_source in
    "")
      block "Blocked: the command gives no body, so this hook can't check $1. $give_the_body"
      ;;
    text)
      if [[ ! $body_word =~ $heredoc_reference ]]; then
        body=$body_word
        return 0
      fi
      # The text of each here-document in the word, such as "$(cat <<'EOF' ... EOF)"
      rest=$body_word
      body_may_expand=""
      while [[ $rest =~ $heredoc_reference ]]; do
        i=${BASH_REMATCH[1]}
        rest=${rest#*"${BASH_REMATCH[0]}"}
        body+=${heredocs[i]}
        if [[ ${heredoc_note_expands[i]} == yes ]] && heredoc_has_expansion "${heredocs[i]}"; then
          body_may_expand=yes
        fi
      done
      ;;
    file)
      body_may_expand=""
      if [[ $body_file != - ]]; then
        read_body_file "$body_file"
        return 0
      fi
      # Standard input, from a here-document or a file
      for ((i = 0; i < ${#redirections[@]}; i += 2)); do
        if [[ ${redirections[i]} =~ ^0?\<\<-?$ && ${redirections[i + 1]} =~ $heredoc_reference ]]; then
          index=${BASH_REMATCH[1]}
          body=${heredocs[index]}
          if [[ ${heredoc_note_expands[index]} == yes ]] && heredoc_has_expansion "$body"; then
            body_may_expand=yes
          fi
          return 0
        fi
        if [[ ${redirections[i]} =~ ^0?\<$ ]]; then
          read_body_file "${redirections[i + 1]}"
          return 0
        fi
      done
      block "Blocked: this hook can't read a body from standard input, other than from a here-document, so it can't check $1. $give_the_body"
      ;;
  esac
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
