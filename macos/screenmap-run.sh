#!/bin/zsh
# Any Android repository → screen map in one shot. Backs the macOS launcher (screenmap-picker.applescript); also usable from a terminal.
# usage: macos/screenmap-run.sh <repo dir>
#   output: <tool>/out/<repo basename>/<basename>.html (+ graph.json, code.json, run.log). The only stdout line is that HTML path
#           (empty when nothing was built); everything else goes to run.log.
#   exit 0 built, verify passed · 1 built but verify FAIL (the map is still usable) · 2 nothing built (not an Android repo, extract/build error)
# config: <repo>/screenmap.json if present, else the adapter whose folder is named like the repo or whose config lists the repo
#         folder name in `repo_names` (`python3 -m screenmap adapter <name>`; its controls.json is passed too), else built-in defaults.
# A git repository is read through `git archive HEAD` (uncommitted changes excluded, repository untouched); anything else is read in place.
setopt null_glob
cd "$(dirname "$0")/.."
REPO="${1:?usage: screenmap-run.sh <repo dir>}"; REPO="${REPO%/}"; NAME=$(basename "$REPO")
OUT="$PWD/out/$NAME"; LOG="$OUT/run.log"; HTML="$OUT/$NAME.html"
mkdir -p "$OUT"; rm -f "$HTML"; : > "$LOG"

ARGS=(--name "$NAME" --build-anyway)
if [ -f "$REPO/screenmap.json" ]; then
  ARGS+=(--config "$REPO/screenmap.json")      # from the real path: an uncommitted file is not in the archive
else
  CFG=$(python3 -m screenmap adapter "$NAME" 2>> "$LOG")   # by the original folder name, never the archive copy's
  if [ -n "$CFG" ]; then
    ARGS+=(--config "$CFG")
    [ -f "$(dirname "$CFG")/controls.json" ] && ARGS+=(--controls "$(dirname "$CFG")/controls.json")
    echo "▶ 어댑터 $CFG" >> "$LOG"
  else
    echo "▶ 어댑터 없음 — 기본 설정" >> "$LOG"
  fi
fi

SRC="$REPO"
if [ -d "$REPO/.git" ]; then
  WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
  BR=$(git -C "$REPO" branch --show-current); CM=$(git -C "$REPO" rev-parse --short HEAD)
  echo "▶ $BR @ $CM 아카이브 추출 (작업 트리 미커밋 변경은 제외)" >> "$LOG"
  git -C "$REPO" archive HEAD | tar -x -C "$WORK"
  SRC="$WORK"; ARGS+=(--branch "$BR" --commit "$CM" --repo-name "$NAME")
fi

echo "$ python3 -m screenmap all <src> -o $OUT ${ARGS[*]}" >> "$LOG"
python3 -m screenmap all "$SRC" -o "$OUT" "${ARGS[@]}" >> "$LOG" 2>&1; RC=$?   # --build-anyway: exit 1 = built, verify FAIL

NODES=$(grep -o 'extract: nodes=[0-9]*' "$LOG" | grep -o '[0-9]*$')
if [ ! -f "$HTML" ] || [ "${NODES:-0}" -eq 0 ]; then
  echo "no screens found (nodes=${NODES:-0}) — is this an Android (Kotlin) repository?" >> "$LOG"
  rm -f "$HTML"; echo ""; exit 2
fi
echo "$HTML"; exit $RC
