#!/bin/bash
# Slice one saved 3MF plate with the selected installed Snapmaker Orca app.
set -eu
umask 077
fail() { printf '%s\n' "$*" >&2; exit 64; }
[[ $# == 3 || $# == 4 ]] || fail 'Usage: slice.sh INPUT.3mf PLATE OUTPUT.gcode [--allow-newer-file]'
if [[ $# == 4 ]]; then
    [[ "$4" == --allow-newer-file ]] || fail 'Only --allow-newer-file is accepted as fourth argument'
fi
input=$1 plate=$2 destination=$3
case "$input" in *.3mf|*.3MF) ;; *) fail 'Input must be a .3mf file' ;; esac
case "$destination" in *.gcode|*.GCODE) ;; *) fail 'Output must be a .gcode file' ;; esac
[[ "$plate" =~ ^[1-9][0-9]*$ ]] || fail 'PLATE must be a positive integer (1-based)'
[[ -f "$input" && -r "$input" ]] || fail 'Input is not a readable file'
input="$(cd "$(dirname "$input")" && pwd -P)/$(basename "$input")"
parent=$(cd "$(dirname "$destination")" && pwd -P) || fail 'Output directory must exist'
destination="$parent/$(basename "$destination")"
[[ ! -e "$destination" && ! -L "$destination" ]] || fail 'Output already exists'
app=${ORCA_APP:-/Applications/Snapmaker Orca.app}
binary="$app/Contents/MacOS/Snapmaker_Orca"
[[ -x "$binary" ]] || fail "Snapmaker Orca executable unavailable: $binary"
binary="$(cd "$(dirname "$binary")" && pwd -P)/$(basename "$binary")"
command=("$binary")
[[ $# != 4 ]] || command+=(--allow-newer-file)
# Same filesystem as destination permits atomic publication without overwrite.
scratch=$(mktemp -d "$parent/.orca-slice.XXXXXXXX")
trap 'rm -rf "$scratch"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$scratch/datadir" "$scratch/output" "$scratch/tmp"
cp "$input" "$scratch/input.3mf"
export TMPDIR="$scratch/tmp/" LC_ALL=C
unset DISPLAY WAYLAND_DISPLAY SNAPMAKER_ORCA_ALLOW_NEWER_FILE
printf 'Snapmaker Orca executable: %s\n' "$binary" >&2
cd "$scratch"
if "${command[@]}" --slice "$plate" --outputdir "$scratch/output" \
    --datadir "$scratch/datadir" "$scratch/input.3mf"; then
    :
else
    code=$?
    exit "$code"
fi
gcode="$scratch/output/plate_$plate.gcode"
[[ -s "$gcode" && ! -L "$gcode" ]] || fail "Native CLI produced no plate_$plate.gcode"
# link uses the exact target path, including when a directory appeared there.
if ! /bin/link "$gcode" "$destination"; then
    printf 'Could not publish G-code without overwriting: %s\n' "$destination" >&2
    exit 73
fi
printf 'G-code: %s\n' "$destination" >&2
