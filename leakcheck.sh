#!/usr/bin/env bash
# Preflight leak scan for a repo you intend to publish.
#
# Checks what git would actually PUBLISH: tracked/unignored file contents,
# plus the commit-author metadata baked into history.
#
# Generic patterns are built in. Your own identifiers (handles, home paths,
# aliases) belong in .leakpatterns, which must stay gitignored - otherwise the
# scanner publishes the very strings it exists to catch.
set -o pipefail
RED=$'\033[31m'; YEL=$'\033[33m'; GRN=$'\033[32m'; NC=$'\033[0m'
SELF=$(basename "$0"); hits=0

if git rev-parse --git-dir >/dev/null 2>&1; then
  FILES=$(git ls-files -co --exclude-standard); MODE="git-tracked (respects .gitignore)"
else
  FILES=$(find . -type f -not -path './.git/*' -not -path './node_modules/*' | sed 's|^\./||')
  MODE="plain directory (no git yet)"
fi
FILES=$(printf '%s\n' "$FILES" | grep -vx "$SELF" | grep -vx ".leakpatterns")
echo "mode: $MODE"; echo

check(){
  local label="$1" pat="$2" out
  out=$(printf '%s\n' "$FILES" | grep -v '^$' \
        | xargs -r -d'\n' grep -InEe "$pat" --binary-files=without-match 2>/dev/null | head -8)
  [ -z "$out" ] && return
  printf '%s  %s\n' "${RED}LEAK${NC}" "$label"
  printf '%s\n' "$out" | sed 's/^/        /'
  hits=$((hits+1))
}

# --- generic, safe to publish ---
check "home directory path"  '/home/[a-z0-9_-]+|/Users/[a-z0-9_-]+'
check "private key material" 'BEGIN [A-Z ]*PRIVATE KEY|PRIVATE_KEY|MNEMONIC|SEED_PHRASE'
check "API key or token"     'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}'
check "AI session URL"       'claude\.ai/code/session|chatgpt\.com/c/'
check "raw IPv4 address"     '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b'

# --- your identifiers, loaded from an ignored file ---
if [ -f .leakpatterns ]; then
  while IFS= read -r p; do
    [ -z "$p" ] || [ "${p:0:1}" = "#" ] && continue
    check "personal identifier: $p" "$p"
  done < .leakpatterns
  if git rev-parse --git-dir >/dev/null 2>&1 && ! git check-ignore -q .leakpatterns 2>/dev/null; then
    printf '%s  %s\n' "${RED}LEAK${NC}" ".leakpatterns is NOT gitignored - it would publish your identifiers"
    hits=$((hits+1))
  fi
else
  echo "${YEL}note${NC}  no .leakpatterns file - only generic patterns were checked"
fi

if git rev-parse HEAD >/dev/null 2>&1; then
  me=$(git config user.email)
  meta=$(git log --format='%an <%ae>%n%cn <%ce>' | sort -u | grep -v "<$me>")
  if [ -n "$meta" ]; then
    printf '%s  %s\n' "${RED}LEAK${NC}" "foreign author identity in commit history"
    printf '%s\n' "$meta" | sed 's/^/        /'; hits=$((hits+1))
  fi
fi

echo; echo "commits will be authored as:"
n=$(git config user.name 2>/dev/null); e=$(git config user.email 2>/dev/null)
if [ -z "$n" ] || [ -z "$e" ]; then
  echo "        ${RED}UNSET - would fall back to your global identity${NC}"; hits=$((hits+1))
else
  echo "        $n <$e>"
fi
echo
[ "$hits" -eq 0 ] && echo "${GRN}clean - safe to push${NC}" || echo "${YEL}$hits issue(s) - fix before pushing${NC}"
exit $hits
