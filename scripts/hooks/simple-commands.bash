# shellcheck shell=bash
# Functions that split a shell command into its simple commands, for the checks in this folder. helpers.bash sources
# this file.

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
  local text=$1 index char next substitution_body substitution_end
  for ((index = 0; index < ${#text}; index++)); do
    char=${text:index:1} next=${text:index+1:1}
    if [[ $char == \\ && $next == [\$\`\\] ]]; then
      index=$((index + 1))
    elif [[ $char == '`' || ($char == '$' && $next == '(') ]]; then
      read_substitution "$text" "$index"
      split_substitution "$substitution_body"
      index=$substitution_end
    fi
  done
}

# Splits the command of a substitution, which runs in a shell of its own: split_substitution <command>
split_substitution() {
  steps+=(begin)
  split_simple_commands "$1"
  steps+=(end)
}

# Calls a function with the words of each simple command of a shell command, including the commands inside
# substitutions: for_each_simple_command <function> <command>
# scripts/hooks/before-tool splits the command with split_command before it runs the checks, which then don't split it
# again. That cut the CPU time of scripts/check by 13 seconds, as pull request #122 measured.
# simple-commands.test checks that the function gets the same values as from the plain splitter in
# plain-simple-commands.bash, which splits the command for each check.
for_each_simple_command() {
  if [[ -z ${split_command_text+set} || $split_command_text != "$2" ]]; then
    split_command "$2"
  fi
  visit_simple_commands "$1" 0
}

# Splits a shell command into its simple commands, including the commands inside substitutions, and records them in
# the array steps for visit_simple_commands: split_command <command>
# Here-document bodies stay available to the checks that read issue and pull request bodies, in the array heredocs.
split_command() {
  local index
  steps=() step_first_words=() step_word_counts=() step_first_redirections=() step_redirection_counts=()
  split_words=() split_words_may_expand=() split_redirections=()
  split_heredocs "$1"
  heredoc_note_expands=(${heredoc_expands[@]+"${heredoc_expands[@]}"})
  split_simple_commands "$command_text"
  for ((index = 0; index < ${#heredocs[@]}; index++)); do
    if [[ ${heredoc_expands[index]} == yes ]]; then
      check_heredoc_substitutions "${heredocs[index]}"
    fi
  done
  split_command_text=$1
}

# Calls a function with the words of each simple command that split_command recorded, from a step until the end of
# the shell that runs the step's command: visit_simple_commands <function> <step>
# Before each call, the array redirections holds the command's redirections as pairs: the operator, such as < or 2>,
# then its target. The array simple_command_words holds the words, which block logs. The array words_may_expand holds,
# for each word, command when it has a $( ) or backticks that runs a command other than cat alone, reading a
# here-document, yes when it has other expansion syntax outside single quotes, or empty.
# A cd between ( and ), or in a substitution, changes the folder that follow_cd keeps only until the shell that runs it
# ends, because the commands in it run in a shell of their own. Sets visited_step to the step where it stopped.
visit_simple_commands() {
  local function=$1 visit_step=$2 outer_folder
  local -a redirections=() words_may_expand=() simple_command_words=() outer_folders=()
  while ((visit_step < ${#steps[@]})); do
    case ${steps[visit_step]} in
      call)
        simple_command_words=("${split_words[@]:step_first_words[visit_step]:step_word_counts[visit_step]}")
        words_may_expand=("${split_words_may_expand[@]:step_first_words[visit_step]:step_word_counts[visit_step]}")
        redirections=(${split_redirections[@]+"${split_redirections[@]:step_first_redirections[visit_step]:step_redirection_counts[visit_step]}"})
        "$function" "${simple_command_words[@]}"
        # env -C changes the folder only for the command that env runs.
        if [[ -n ${folder_after_command+set} ]]; then
          command_folder=$folder_after_command
          unset folder_after_command
        fi
        ;;
      "(") outer_folders+=("${command_folder-}") ;;
      ")")
        if ((${#outer_folders[@]} > 0)); then
          command_folder=${outer_folders[${#outer_folders[@]} - 1]}
          unset 'outer_folders[${#outer_folders[@]} - 1]'
        fi
        ;;
      begin)
        outer_folder=${command_folder-}
        visit_simple_commands "$function" $((visit_step + 1))
        visit_step=$visited_step
        command_folder=$outer_folder
        ;;
      end) break ;;
    esac
    visit_step=$((visit_step + 1))
  done
  visited_step=$visit_step
}

# Splits a shell command into simple commands, and adds a step for each one to the array steps, with its words in
# split_words and split_words_may_expand, and its redirections in split_redirections, as visit_simple_commands reads
# them: split_simple_commands <command>
# Simple commands end at &&, ||, ;, |, &, a parenthesis and a line break. Words end at spaces and tabs.
# Quotes and backslashes are removed as the shell removes them, but nothing is expanded, so "$HOME" stays $HOME.
# Redirections, such as "< .env" or "2>/dev/null", aren't words.
# It leaves substitution text in the surrounding word, because body checks inspect the original text.
# Each parenthesis is a step of its own, so that visit_simple_commands can tell which commands run in a shell of their
# own.
split_simple_commands() {
  local text=$1
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

    if [[ $quote != "'" && $word_may_expand != command ]] && starts_expansion "$char" "$next"; then
      word_may_expand=yes
    fi

    if [[ $quote != "'" && ($char == '`' || ($char == '$' && $next == '(')) ]]; then
      read_substitution "$text" "$i"
      # read_body reads the here-document that cat prints in place of the output, because they're the same text.
      # $(( )) is arithmetic, and runs no command.
      if [[ ${text:i:3} != '$((' && ! $substitution_body =~ $cat_heredoc ]]; then
        word_may_expand=command
      fi
      split_substitution "$substitution_body"
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
        step_first_words[${#steps[@]}]=${#split_words[@]}
        step_word_counts[${#steps[@]}]=${#words[@]}
        step_first_redirections[${#steps[@]}]=${#split_redirections[@]}
        step_redirection_counts[${#steps[@]}]=${#redirections[@]}
        steps+=(call)
        split_words+=("${words[@]}")
        split_words_may_expand+=("${words_may_expand[@]}")
        split_redirections+=(${redirections[@]+"${redirections[@]}"})
      fi
      words=()
      words_may_expand=()
      redirections=()
      operator=""
      if [[ $char == [\(\)] ]]; then
        steps+=("$char")
      fi
    fi
  done
}

# Delimiter words can mix quoted, escaped and unquoted parts, such as E"OF".
heredoc_operator="<<(-?)[[:blank:]]*(([^[:space:]<>;&|()'\"\\\\]|'[^']*'|\"[^\"]*\"|\\\\.)+)"
# split_heredocs puts this character around a here-document's number. Commands don't contain it.
marker=$'\037'
heredoc_reference="$marker([0-9]+)$marker"
# The command of a $( ) or backticks that prints a here-document and nothing else, as split_heredocs leaves it
cat_heredoc="^[[:space:]]*cat[[:space:]]*<<-?$heredoc_reference[[:space:]]*\$"

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
