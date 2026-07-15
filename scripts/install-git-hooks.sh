#!/usr/bin/env bash
# Install local git hooks (optional — run once after clone).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS="$ROOT/.git/hooks"

if [[ ! -d "$ROOT/.git" ]]; then
  echo "Not a git repository: $ROOT" >&2
  exit 1
fi

mkdir -p "$HOOKS"

cat > "$HOOKS/pre-commit" << 'HOOK'
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

STAGED_PY=$(git diff --cached --name-only --diff-filter=ACM | grep -E '\.(py)$' || true)
if [[ -n "$STAGED_PY" ]]; then
  if command -v ruff >/dev/null 2>&1; then
    echo "$STAGED_PY" | xargs ruff check
  elif python3 -m ruff --version >/dev/null 2>&1; then
    echo "$STAGED_PY" | xargs python3 -m ruff check
  else
    echo "pre-commit: ruff not installed (pip install ruff); skipping Python lint" >&2
  fi
fi

for f in $(git diff --cached --name-only --diff-filter=ACM | grep -E '\.sh$' || true); do
  if command -v shellcheck >/dev/null 2>&1; then
    shellcheck "$f"
  fi
done
HOOK

chmod +x "$HOOKS/pre-commit"
echo "Installed pre-commit hook → $HOOKS/pre-commit"
echo "  - ruff check on staged .py files"
echo "  - shellcheck on staged .sh files (if shellcheck installed)"
