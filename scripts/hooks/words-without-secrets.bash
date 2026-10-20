# shellcheck shell=bash
# Functions that leave the values that could be secrets out of an action's words, before log_block in helpers.bash
# writes them to the log. helpers.bash sources this file.

# The parts of a name that say that its value could be a secret, in any case. Names of other values hold them too,
# such as --path, but a list of whole names would miss some secrets.
secret_name='(token|key|secret|pass|pwd|pat|cookie|auth|bearer|credential)'
# A name that holds a secret's name, then an = or a colon, as in GH_TOKEN=, "api_key": or Cookie:
named_secret="[A-Za-z0-9_.-]*${secret_name}[A-Za-z0-9_.-]*[\"']?[[:blank:]]*[:=][[:blank:]]*"
# The word Bearer and the spaces after it, as in "Bearer abc"
bearer='(^|[^A-Za-z0-9_.-])bearer[[:blank:]]*'
# A URL up to its password, and the password, which can hold a /, as in https://someone:password@example.com
url_password='(://[^:/@[:space:]]*:)[^[:space:]]*@'
left_out='[left out]'

# Sets safe_words to the words, joined by spaces, with each value that could hold a secret left out:
# - each word that holds a token's shape from scripts/token-shapes
# - the word after a flag whose name holds a secret's name, such as --token or --pat, or after -p, unless that word is
#   a flag too
# - the password in user:password after -u or --user, and in a URL
# - the rest of a word after a name that holds a secret's name and an = or a colon, as in GH_TOKEN= or Cookie:, or
#   after Bearer, or the next word when nothing follows them
# Line breaks and other control characters become spaces, so that the words take one line. Fails when it can't read
# the token shapes: words_without_secrets <word>...
words_without_secrets() {
  local word original follows="" token_shape
  local -a words=("$@") kept=()
  read_token_shapes || return 1
  # bash 3.2 takes longer for each function call the more arguments the calling function has, so the loop reads the
  # words from an array, with no arguments left. With them, 3,000 words took 10 seconds instead of 1, as issue #96
  # measured.
  set --
  for original in "${words[@]}"; do
    word=$original
    leave_secrets_out_of_word
    kept+=("$word")
    find_what_follows "$original"
  done
  safe_words="${kept[*]}"
  # tr, because bash 3.2 takes minutes to replace the characters in a text of 60 KB with line breaks. Words without a
  # control character skip tr and the processes that it takes.
  if [[ $safe_words =~ [[:cntrl:]] ]]; then
    safe_words=$(printf '%s' "$safe_words" | tr '[:cntrl:]' ' ')
  fi
}

# Leaves out of word each value that could hold a secret, as words_without_secrets lists them. It reads what the word
# before leaves out of this one in follows, as find_what_follows sets it, and the shapes of tokens in token_shape.
leave_secrets_out_of_word() {
  if [[ $follows == word && $word != -* ]]; then
    word=$left_out
  elif [[ $follows == password && $word == *:* ]]; then
    word=${word%%:*}:$left_out
  fi
  if [[ $word =~ $token_shape ]]; then
    word=$left_out
  fi
  shopt -s nocasematch
  if [[ $word =~ $url_password ]]; then
    word=${word/"${BASH_REMATCH[0]}"/"${BASH_REMATCH[1]}$left_out@"}
  fi
  if [[ $word =~ $named_secret ]]; then
    leave_out_after "${BASH_REMATCH[0]}"
  elif [[ $word =~ $bearer ]]; then
    leave_out_after "${BASH_REMATCH[0]}"
  fi
  shopt -u nocasematch
}

# Sets follows to what a word leaves out of the word after it: word for the whole word, as after --token, password
# for the password in user:password, as after -u, or nothing: find_what_follows <word>
find_what_follows() {
  follows=""
  shopt -s nocasematch
  if [[ $1 == -p || ($1 == -* && $1 != *=* && $1 =~ $secret_name) ]]; then
    follows=word
  elif [[ $1 =~ ${named_secret}$ || $1 =~ ${bearer}$ ]]; then
    follows=word
  elif [[ $1 == -u || $1 == --user ]]; then
    follows=password
  fi
  shopt -u nocasematch
}

# Sets token_shape to one extended regular expression that matches each shape that scripts/token-shapes lists, or fails
# when it can't read the file.
read_token_shapes() {
  local kind shape
  token_shape=""
  while IFS=$'\t' read -r kind _ shape _; do
    if [[ $kind != '#'* ]]; then
      token_shape+="${token_shape:+|}($shape)"
    fi
  done <"$hooks_folder/../token-shapes" || return 1
}

# Leaves out the text in word after the first time that a part appears in it, unless nothing follows the part, because
# then the value is the next word: leave_out_after <part>
leave_out_after() {
  local before=${word%%"$1"*}
  if ((${#before} + ${#1} < ${#word})); then
    word=$before$1$left_out
  fi
}
