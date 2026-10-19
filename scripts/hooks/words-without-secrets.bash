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

# Prints the words, joined by spaces, with each value that could hold a secret left out:
# - each word that holds a token's shape from scripts/token-shapes
# - the word after a flag whose name holds a secret's name, such as --token or --pat, or after -p, unless that word is
#   a flag too
# - the password in user:password after -u or --user, and in a URL
# - the rest of a word after a name that holds a secret's name and an = or a colon, as in GH_TOKEN= or Cookie:, or
#   after Bearer, or the next word when nothing follows them
# Line breaks and other control characters become spaces, so that the words take one line. Fails when it can't read
# the token shapes: words_without_secrets <word>...
words_without_secrets() {
  local word original follows="" leaves_out_next token_shape
  local -a kept=()
  token_shape=$(token_shapes) || return 1
  for original in "$@"; do
    word=$original
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
    kept+=("$word")

    leaves_out_next=""
    if [[ $original == -p || ($original == -* && $original != *=* && $original =~ $secret_name) ]]; then
      leaves_out_next=word
    elif [[ $original =~ ${named_secret}$ || $original =~ ${bearer}$ ]]; then
      leaves_out_next=word
    elif [[ $original == -u || $original == --user ]]; then
      leaves_out_next=password
    fi
    follows=$leaves_out_next
    shopt -u nocasematch
  done
  # tr, because bash 3.2 takes minutes to replace the characters in a text of 60 KB with line breaks.
  printf '%s' "${kept[*]}" | tr '[:cntrl:]' ' '
}

# Prints one extended regular expression that matches each shape that scripts/token-shapes lists, or fails when it
# can't read the file.
token_shapes() {
  local kind shape pattern=""
  while IFS=$'\t' read -r kind _ shape _; do
    if [[ $kind != '#'* ]]; then
      pattern+="${pattern:+|}($shape)"
    fi
  done <"$hooks_folder/../token-shapes" || return 1
  printf '%s\n' "$pattern"
}

# Leaves out the text in word after the first time that a part appears in it, unless nothing follows the part, because
# then the value is the next word: leave_out_after <part>
leave_out_after() {
  local before=${word%%"$1"*}
  if ((${#before} + ${#1} < ${#word})); then
    word=$before$1$left_out
  fi
}
