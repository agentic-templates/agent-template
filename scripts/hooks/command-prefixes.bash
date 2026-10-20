# shellcheck shell=bash
# Functions that find a simple command's name after the words before it, such as sudo, env and timeout, for the checks
# in this folder. helpers.bash sources this file.

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

# Sets the options of a wrapper that runs the command after it to wrapper_options, between spaces, or fails for a name
# that isn't such a wrapper: find_wrapper_options <name>
# The wrappers are the ones that Claude Code drops before it matches a command against its permission rules, as
# https://code.claude.com/docs/en/permissions#process-wrappers lists them, so that a rule that allows a command also
# allows it behind them. The options are those of the GNU and macOS versions of the programs, and of bash and zsh for
# time and command. An option that takes a value ends in =.
find_wrapper_options() {
  case $1 in
    timeout) wrapper_options=" -f -p -v -k= -s= --foreground --preserve-status --verbose --kill-after= --signal= " ;;
    time) wrapper_options=" -a -h -l -p -q -v -f= -o= --append --portability --quiet --verbose --format= --output= " ;;
    nice) wrapper_options=" -n= --adjustment= " ;;
    stdbuf) wrapper_options=" -i= -o= -e= --input= --output= --error= " ;;
    command) wrapper_options=" -p -v -V " ;;
    nohup | builtin | noglob | xargs) wrapper_options=" " ;;
    *) return 1 ;;
  esac
}

# Sets wrapper_words to the number of words that the wrapper at the start of the words takes: its name, its options
# from find_wrapper_options, their values, and the duration of timeout. Fails when the wrapper doesn't run the command
# after it: command -v and command -V look the command up instead, and Claude Code doesn't drop xargs with an option.
# read_wrapper <word>...
read_wrapper() {
  local name=${1##*/} i=1
  local -a words=("$@")
  wrapper_letters=""
  if [[ $name == xargs && ${2-} == -* ]]; then
    return 1
  fi
  while ((i < ${#words[@]})) && [[ ${words[i]} == -?* ]]; do
    if [[ ${words[i]} == -- ]]; then
      i=$((i + 1))
      break
    fi
    read_wrapper_option "$name" "${words[i]}"
    i=$((i + wrapper_option_words))
  done
  if [[ $name == command && $wrapper_letters == *[vV]* ]]; then
    return 1
  fi
  if [[ $name == timeout ]]; then
    i=$((i + 1))
  fi
  wrapper_words=$i
  if ((wrapper_words > ${#words[@]})); then
    wrapper_words=${#words[@]}
  fi
}

# Reads one of a wrapper's options, as find_wrapper_options lists them, and sets wrapper_option_words to the number of
# words that it takes: 2 when its value is the next word, otherwise 1. Adds the letters of a group of short options, as
# in -fk5, to wrapper_letters. Blocks every other option, because the hooks can't tell then which word is the command,
# as with --sig, which GNU's timeout reads as --signal: read_wrapper_option <wrapper> <option>
read_wrapper_option() {
  local name=$1 option=$2 position letter
  wrapper_option_words=1
  # The obsolete form of nice's adjustment, as in -10, --10 or -+10
  if [[ $name == nice && $option =~ ^-[-+]?[0-9] ]]; then
    return 0
  fi
  if [[ $option == --* ]]; then
    read_long_wrapper_option "$name" "$option"
    return 0
  fi
  # The group ends at the first option that takes a value. Its value is the rest of the word, or the next word.
  for ((position = 1; position < ${#option}; position++)); do
    letter=${option:position:1}
    if [[ $wrapper_options == *" -$letter= "* ]]; then
      if ((position == ${#option} - 1)); then
        wrapper_option_words=2
      fi
      return 0
    fi
    if [[ $wrapper_options != *" -$letter "* ]]; then
      block_wrapper_option "$name" "$option"
    fi
    wrapper_letters+=$letter
  done
}

# Reads one of a wrapper's long options for read_wrapper_option, which takes a value after = or in the next word:
# read_long_wrapper_option <wrapper> <option>
read_long_wrapper_option() {
  if [[ $2 == *=* && $wrapper_options == *" ${2%%=*}= "* ]]; then
    wrapper_option_words=1
  elif [[ $wrapper_options == *" $2= "* ]]; then
    wrapper_option_words=2
  elif [[ $wrapper_options == *" $2 "* ]]; then
    wrapper_option_words=1
  else
    block_wrapper_option "$1" "$2"
  fi
}

# Blocks an option of a wrapper that read_wrapper_option doesn't read: block_wrapper_option <wrapper> <option>
block_wrapper_option() {
  local options=${wrapper_options//=/}
  options=${options# }
  options=${options% }
  block "Blocked: the hooks can't tell which command $1 runs after its option $2, so they can't check that command. Run the command without that option${options:+, or with one that the hooks read: $options}."
}

# Changes shell_runs_command, as drop_command_prefixes describes it, for a wrapper that it drops:
# set_shell_runs_command <the wrapper's word> <the word's index among the simple command's words>
# The shell reads time as its own keyword only as the first word and unquoted, and otherwise runs the program time, as
# for 'time' or \time. The hooks see each word after its quotes are removed, so they can't tell which of the two runs
# the command. The shell runs a wrapper that a word names with its path, such as /usr/bin/time, as a program.
set_shell_runs_command() {
  case $1 in
    builtin) ;;
    time)
      if (($2 > 0)); then
        shell_runs_command=no
      else
        shell_runs_command=maybe
      fi
      ;;
    command | noglob)
      if [[ $shell_runs_command == yes ]]; then
        shell_runs_command=maybe
      fi
      ;;
    *) shell_runs_command=no ;;
  esac
}

# Sets the array command_words to a simple command's words from the command's name on, without the words before it:
# sudo, env, env's options and their values, the words that set variables, and the wrappers that read_wrapper reads,
# with their options. Sets these variables too:
# - assignment_words: an array of the words before the command's name that set variables, the shell's and env's, but
#   not a wrapper's option values, such as the file in time -o NAME=value.
# - runs_through_env: true if env is among the words before the command's name, otherwise false.
# - shell_runs_command: yes when the shell runs the command itself, as after builtin, no when another program runs it,
#   as after sudo, env or timeout, and maybe after time, command or noglob. set_shell_runs_command says why for time.
#   After command, bash runs a builtin such as cd itself, but zsh runs the program cd. zsh runs the command after
#   noglob itself, but bash has no noglob.
# drop_command_prefixes <word>...
# The shell reads a word as setting a variable when it starts with a name and =, as in NAME=value, but env reads each
# word with = that way.
drop_command_prefixes() {
  local -a prefixed=("$@")
  local i=0 word is_after_env=false reads_env_options=false
  assignment_words=()
  runs_through_env=false shell_runs_command=yes
  while ((i < ${#prefixed[@]})); do
    word=${prefixed[i]}
    if [[ $reads_env_options == true && $word == -- ]]; then
      reads_env_options=false
    elif [[ $reads_env_options == true && $word == -* ]]; then
      read_env_option "$word" "${prefixed[i + 1]-}"
      i=$((i + env_option_words - 1))
    elif [[ $word =~ ^[A-Za-z_][A-Za-z0-9_]*= || ($is_after_env == true && $word == *=*) ]]; then
      reads_env_options=false
      assignment_words+=("$word")
    elif [[ $word == sudo ]]; then
      is_after_env=false
      reads_env_options=false
      shell_runs_command=no
    elif [[ ${word##*/} == env ]]; then
      is_after_env=true
      reads_env_options=true
      runs_through_env=true
      shell_runs_command=no
    elif find_wrapper_options "${word##*/}" && read_wrapper "${prefixed[@]:i}"; then
      is_after_env=false
      reads_env_options=false
      set_shell_runs_command "$word" "$i"
      i=$((i + wrapper_words - 1))
    else
      break
    fi
    i=$((i + 1))
  done
  command_words=(${prefixed[@]+"${prefixed[@]:i}"})
  command_word_may_expand=(${words_may_expand[@]+"${words_may_expand[@]:i}"})
}
