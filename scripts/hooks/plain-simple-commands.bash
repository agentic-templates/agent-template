# shellcheck shell=bash
# The splitter that simple-commands.bash replaced, which the tests compare with it, as "Add complexity only for a
# requirement or a measurement" in docs/code.md asks. It splits a command each time that a check calls
# plain_for_each_simple_command, and calls the function while it splits. simple-commands.test sources this file after
# helpers.bash, which has the functions that both splitters share.

# Here-document quotes are ordinary text. Only backslashes can make a substitution literal.
plain_check_heredoc_substitutions() {
  local function=$1 text=$2 index char next substitution_body substitution_end
  for ((index = 0; index < ${#text}; index++)); do
    char=${text:index:1} next=${text:index+1:1}
    if [[ $char == \\ && $next == [\$\`\\] ]]; then
      index=$((index + 1))
    elif [[ $char == '`' || ($char == '$' && $next == '(') ]]; then
      read_substitution "$text" "$index"
      plain_split_substitution "$function" "$substitution_body"
      index=$substitution_end
    fi
  done
}

# Splits the command of a substitution, which runs in a shell of its own, so that a cd in it changes the folder of
# the next simple command, which follow_cd keeps, only inside it: plain_split_substitution <function> <command>
plain_split_substitution() {
  local outer_folder=${command_folder-}
  plain_split_simple_commands "$@"
  command_folder=$outer_folder
}

# Splits shell text and checks each simple command, including commands inside substitutions:
# plain_for_each_simple_command <function> <command>
# Here-document bodies stay available to the checks that read issue and pull request bodies.
plain_for_each_simple_command() {
  local function=$1 text=$2 command_text index
  local -a heredocs=() heredoc_expands=() heredoc_note_expands=()
  split_heredocs "$text"
  heredoc_note_expands=(${heredoc_expands[@]+"${heredoc_expands[@]}"})
  plain_split_simple_commands "$function" "$command_text"
  for ((index = 0; index < ${#heredocs[@]}; index++)); do
    if [[ ${heredoc_expands[index]} == yes ]]; then
      plain_check_heredoc_substitutions "$function" "${heredocs[index]}"
    fi
  done
}

# Splits a shell command into simple commands, and calls a function with the words of each one:
# plain_split_simple_commands <function> <command>
# Simple commands end at &&, ||, ;, |, &, a parenthesis and a line break. Words end at spaces and tabs.
# Quotes and backslashes are removed as the shell removes them, but nothing is expanded, so "$HOME" stays $HOME.
# Redirections, such as "< .env" or "2>/dev/null", aren't words. Before each call, the array redirections
# holds them as pairs: the operator, such as < or 2>, then its target. The array simple_command_words holds the
# words, which block logs. block can't read the array words, because some checks have a local array of that name.
# It leaves substitution text in the surrounding word, because body checks inspect the original text. The array
# words_may_expand holds, for each word, command when it has a $( ) or backticks that runs a command other than cat
# alone, reading a here-document, yes when it has other expansion syntax outside single quotes, or empty.
# A cd between ( and ) changes the folder that follow_cd keeps only until the ), because the commands between them run
# in a shell of their own.
plain_split_simple_commands() {
  local function=$1 text=$2
  local length=${#text} i char next
  local word="" has_word=false quote="" descriptor="" operator="" starts_redirection word_may_expand=""
  local substitution_body substitution_end rest chunk
  local -a redirections=()
  local -a words=() words_may_expand=() simple_command_words=() outer_folders=()

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
      plain_split_substitution "$function" "$substitution_body"
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
        simple_command_words=("${words[@]}")
        "$function" "${words[@]}"
        # env -C changes the folder only for the command that env runs.
        if [[ -n ${folder_after_command+set} ]]; then
          command_folder=$folder_after_command
          unset folder_after_command
        fi
      fi
      words=()
      words_may_expand=()
      redirections=()
      operator=""
      if [[ $char == '(' ]]; then
        outer_folders+=("${command_folder-}")
      elif [[ $char == ')' ]] && ((${#outer_folders[@]} > 0)); then
        command_folder=${outer_folders[${#outer_folders[@]} - 1]}
        unset 'outer_folders[${#outer_folders[@]} - 1]'
      fi
    fi
  done
}
