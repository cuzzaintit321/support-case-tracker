#!/usr/bin/env bash
# Run only when ready to publish this complete project as a public GitHub repo.
set -euo pipefail
cd -- "$(dirname -- "$0")"
command -v gh >/dev/null || { echo 'Install GitHub CLI first.'; exit 1; }
gh auth status
gh auth setup-git
if [ -e .git ]; then
    echo 'A Git checkout already exists here. Review git status before publishing manually.'
    exit 1
fi
if gh repo view cuzzaintit321/support-case-tracker >/dev/null 2>&1; then
    echo 'Repository already exists. No files changed; review it before uploading.'
    exit 1
fi
git init -b main
git add .
git -c user.name='Jason Edwards' \
    -c user.email='228125969+cuzzaintit321@users.noreply.github.com' \
    commit -m 'Add Support Case Tracker with SQLite, tests, and documentation'
gh repo create cuzzaintit321/support-case-tracker --public --source . --remote origin --push
