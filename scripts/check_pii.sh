#!/usr/bin/env bash
set -euo pipefail
FAIL=0

echo "=== Checking for real names in tracked files ==="
if git grep -i 'dylan' -- ':!sample-archive/' ':!*.db' 2>/dev/null | grep -v 'check_pii.sh'; then
  echo "ERROR: found 'dylan' in tracked files"
  FAIL=1
fi

echo "=== Checking git log for real names ==="
if git log --format='%an %ae %cn %ce' | grep -i 'dylan'; then
  echo "ERROR: found 'dylan' in git history"
  FAIL=1
fi

echo "=== Checking for phone numbers outside sample-archive ==="
if git grep -E '\+[0-9 ]{8,}' -- ':!sample-archive/' 2>/dev/null | grep -v 'check_pii.sh'; then
  echo "ERROR: found phone-number pattern outside sample-archive/"
  FAIL=1
fi

echo "=== Checking sample-archive is gitignored ==="
if ! grep -q 'sample-archive' .gitignore 2>/dev/null; then
  echo "ERROR: sample-archive/ not in .gitignore"
  FAIL=1
fi

echo "=== Checking articles.db is gitignored ==="
if ! grep -q 'articles.db\|^\*\.db' .gitignore 2>/dev/null; then
  echo "WARNING: articles.db not in .gitignore (may contain scraped PII)"
fi

if [ $FAIL -eq 0 ]; then
  echo "All checks passed."
else
  echo "FAILED — do not make repo public until issues are resolved."
  exit 1
fi
