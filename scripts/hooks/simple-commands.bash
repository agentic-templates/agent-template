# shellcheck shell=bash
# Functions that split a shell command into its simple commands, for the checks in this folder. helpers.bash sources
# this file.

# Finds the end and contents of a $( ) or backtick substitution, without running it:
# read_substitution <text> <opening character's index>
# Sets substitution_end to the index of the character that ends the substitution, and substitution_body to its command.
read_substitution() {
  if [[ ${1:$2:1} == '`' ]]; then
    read_backtick_substitution "$1" "$2"
  else
    read_parenthesis_substitution "$1" "$2"
  fi
}

# read_backtick_substitution <text> <opening backtick's index>
read_backtick_substitution() {
  local text=$1 index=$(($2 + 1)) char next
  substitution_body=""
  while ((index < ${#text})); do
    char=${text:index:1} next=${text:index+1:1}
    if [[ $char == '`' ]]; then
      break
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
  substitution_end=$index
}

# read_parenthesis_substitution <text> <index of the $>
read_parenthesis_substitution() {
  local text=$1 start=$2 index=$(($2 + 2)) quote="" depth=1
  while ((index < ${#text})); do
    read_substitution_character
    if ((depth == 0)); then
      break
    fi
    index=$((index + 1))
  done
  substitution_body=${text:start+2:index-start-2}
  substitution_end=$index
}

# Reads the character at index in the command of a $( ) substitution, and changes index, quote and depth, which
# read_parenthesis_substitution keeps. A backslash makes the next character literal, and a nested substitution moves
# index to its end.
read_substitution_character() {
  local char=${text:index:1}
  if [[ $quote == "'" ]]; then
    if [[ $char == "'" ]]; then
      quote=""
    fi
  elif [[ $char == \\ ]]; then
    index=$((index + 1))
  elif [[ $char == '`' || ($char == '$' && ${text:index+1:1} == '(') ]]; then
    read_substitution "$text" "$index"
    index=$substitution_end
  elif [[ $char == '"' && $quote == '"' ]]; then
    quote=""
  elif [[ -z $quote ]]; then
    case $char in
      "'" | '"') quote=$char ;;
      '(') depth=$((depth + 1)) ;;
      ')') depth=$((depth - 1)) ;;
    esac
  fi
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
  local word="" has_word=false quote="" descriptor="" operator="" word_may_expand=""
  local substitution_body substitution_end
  local -a redirections=() words=() words_may_expand=()

  for ((i = 0; i <= length; i++)); do
    char=${text:i:1}
    next=${text:i+1:1}
    if [[ $quote != "'" && $word_may_expand != command ]] && starts_expansion "$char" "$next"; then
      word_may_expand=yes
    fi
    if ((i == length)); then
      # The end of the command ends the word and the simple command.
      read_separator
    elif [[ $quote != "'" && ($char == '`' || ($char == '$' && $next == '(')) ]]; then
      add_substitution
    elif [[ -n $quote ]]; then
      read_quoted_text
    elif [[ $char == [[:space:]\;\&\|\(\)\<\>\'\"\\#] ]]; then
      read_unquoted_character
    else
      # Most characters outside quotes only join the word. The loop adds them itself, because calling
      # read_unquoted_character for each of them made the splitter take a tenth more CPU time, as pull request #130
      # measured.
      word+=$char
      has_word=true
    fi
  done
}

# The functions from here to end_simple_command read the command's text at the index i for split_simple_commands, and
# change its variables, such as word, quote and words.

# Splits the command of the $( ) or backtick substitution that starts at i, and adds the substitution's text to the
# word.
add_substitution() {
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
}

# Adds the text in quotes at i to the word, or ends the quotes.
read_quoted_text() {
  local rest=${text:i} chunk
  # Inside quotes, the text up to the next character that matters joins the word in one step, because adding one
  # character at a time to a long word, such as a body in quotes, takes time that grows with the square of its
  # length. In single quotes only the quote and the marker matter, and in double quotes the quote, \, $ and `.
  if [[ $quote == "'" ]]; then
    chunk=${rest%%[\'"$marker"]*}
  else
    chunk=${rest%%[\"\\\$\`]*}
  fi
  if [[ -n $chunk ]]; then
    word+=$chunk
    i=$((i + ${#chunk} - 1))
  elif [[ $char == "$quote" ]]; then
    quote=""
  elif [[ $quote == '"' && $char == "\\" && $next == [\"\\\$\`] ]]; then
    word+=$next
    i=$((i + 1))
  else
    # split_heredocs takes a << in single quotes as a here-document too, where the shell doesn't, so the text that it
    # took out can't expand.
    if [[ $char == "$marker" && $rest =~ ^$heredoc_reference ]]; then
      heredoc_note_expands[BASH_REMATCH[1]]=no
    fi
    word+=$char
  fi
}

# Reads the character at i outside quotes, which ends the word, starts quotes or a comment, makes the next character
# literal, or joins the word.
read_unquoted_character() {
  if [[ $char == [[:space:]\;\&\|\(\)\<\>] ]]; then
    read_separator
  elif [[ $char == [\'\"] ]]; then
    quote=$char
    has_word=true
  elif [[ $char == \\ ]]; then
    # A backslash at the end of a line joins the line to the next one.
    if [[ $next != $'\n' ]]; then
      word+=$next
      has_word=true
    fi
    i=$((i + 1))
  elif [[ $char == '#' ]] && ! $has_word; then
    # A comment runs to the end of the line.
    while [[ -n ${text:i+1:1} && ${text:i+1:1} != $'\n' ]]; do
      i=$((i + 1))
    done
  else
    word+=$char
    has_word=true
  fi
}

# Ends the word at the character at i, which is a space, a tab, a line break, ;, &, |, a parenthesis, < or >, or the
# end of the command. Then reads the redirection operator that the character starts, or ends the simple command.
read_separator() {
  local starts_redirection=false
  if [[ $char == [\<\>] || ($char == '&' && $next == '>') ]]; then
    starts_redirection=true
  fi
  if $has_word; then
    end_word "$starts_redirection"
  fi
  if $starts_redirection; then
    read_redirection_operator
  elif [[ $char != [[:blank:]] ]]; then
    # A line break, ;, &, |, a parenthesis or the end of the command ends the simple command.
    end_simple_command
  fi
}

# Adds the word to the simple command's words, or to its redirections when it follows an operator. A number right
# before a redirection's operator, as in 2>, is the operator's file descriptor: end_word <whether a redirection starts>
end_word() {
  if [[ -n $operator ]]; then
    redirections+=("$operator" "$word")
    operator=""
  elif $1 && [[ $word =~ ^[0-9]+$ ]]; then
    descriptor=$word
  else
    words+=("$word")
    words_may_expand+=("$word_may_expand")
  fi
  word="" has_word=false word_may_expand=""
}

# Reads the redirection operator that starts at i, such as >>, <& or >|, after its file descriptor, if it has one.
read_redirection_operator() {
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
}

# Adds a step for the simple command, if it has words, and a step for the parenthesis that ends it, if one does.
end_simple_command() {
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
  local line index=0 j marked_line
  local -a lines=() dashes=() delimiters=() expansions=()
  command_text=""
  heredocs=()
  heredoc_expands=()
  while IFS= read -r line; do
    lines+=("$line")
  done <<<"$1"
  while ((index < ${#lines[@]})); do
    mark_heredoc_operators "${lines[index]}"
    index=$((index + 1))
    command_text+=$marked_line$'\n'
    # The bodies follow the line, in the order of their operators.
    for ((j = 0; j < ${#delimiters[@]}; j++)); do
      add_heredoc_body "${dashes[j]}" "${delimiters[j]}"
      heredoc_expands+=("${expansions[j]}")
    done
  done
}

# Sets marked_line to a line of a command with the marker, a number and the marker in place of each here-document's
# delimiter, and the arrays dashes, delimiters and expansions of split_heredocs to the operators' dashes, the delimiters
# and whether each body expands: mark_heredoc_operators <line>
mark_heredoc_operators() {
  local rest=$1 match prefix dash raw_delimiter delimiter expands
  marked_line=""
  dashes=()
  delimiters=()
  expansions=()
  while [[ $rest =~ $heredoc_operator ]]; do
    match=${BASH_REMATCH[0]} dash=${BASH_REMATCH[1]} raw_delimiter=${BASH_REMATCH[2]}
    prefix=${rest%%"$match"*}
    rest=${rest#*"$match"}
    # <<< starts a string, not a here-document.
    if [[ $prefix == *"<" ]]; then
      marked_line+=$prefix$match
      continue
    fi
    read_heredoc_delimiter "$raw_delimiter"
    marked_line+="$prefix<<$dash$marker$((${#heredocs[@]} + ${#delimiters[@]}))$marker"
    dashes+=("$dash")
    delimiters+=("$delimiter")
    expansions+=("$expands")
  done
  marked_line+=$rest
}

# Sets delimiter to the word that ends a here-document, without its quotes and backslashes, and expands to no when they
# make the body literal, or else to yes: read_heredoc_delimiter <delimiter as the command writes it>
read_heredoc_delimiter() {
  local raw_delimiter=$1 k character quote=""
  delimiter="" expands=yes
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
}

# Adds the lines from the line at index up to a here-document's delimiter to heredocs, and moves index past the
# delimiter. <<- removes the tabs at the start of each line: add_heredoc_body <dash, or empty> <delimiter>
add_heredoc_body() {
  local body="" body_line
  while ((index < ${#lines[@]})); do
    body_line=${lines[index]}
    index=$((index + 1))
    if [[ -n $1 ]]; then
      body_line=${body_line#"${body_line%%[!$'\t']*}"}
    fi
    if [[ $body_line == "$2" ]]; then
      break
    fi
    body+=$body_line$'\n'
  done
  heredocs+=("$body")
}
