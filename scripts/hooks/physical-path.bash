# shellcheck shell=bash
# Functions that resolve a path to the file it names. helpers.bash sources this file.

# Prints a path with symlinks and .. resolved in its folders as the system resolves them when it opens the path, so a
# .. after a symlink leads to the parent of the link's target. It does so also when the file or its folders don't
# exist yet, so that two ways to write one path, such as /tmp and /private/tmp on macOS, give the same result. A
# relative folder starts from the current folder: physical_path <path> <folder that a relative path starts from>
physical_path() {
  local path=$1 folder rest=""
  if [[ $path != /* ]]; then
    path=$2/$path
  fi
  # The loop below cuts the path at each /, so a relative path without one would never get shorter.
  if [[ $path != /* ]]; then
    path=$PWD/$path
  fi
  # The deepest folder that exists can hold symlinks. The parts after it can't, so they're resolved as text.
  folder=$path
  while [[ -n $folder && ! -d $folder ]]; do
    rest=/${folder##*/}$rest
    folder=${folder%/*}
  done
  folder=$(CDPATH='' cd -P -- "${folder:-/}" && pwd -P)
  textual_path "$folder$rest"
}

# Prints an absolute path with each . and .. in it resolved as text, without following any symlink: textual_path <path>
textual_path() {
  local rest=${1#/} path="" part
  while [[ -n $rest ]]; do
    part=${rest%%/*}
    rest=${rest#"$part"}
    rest=${rest#/}
    case $part in
      "" | .) ;;
      ..) path=${path%/*} ;;
      *) path=$path/$part ;;
    esac
  done
  echo "${path:-/}"
}
