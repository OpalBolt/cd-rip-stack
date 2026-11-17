#!/usr/bin/env bash
# Open a PR for a branch the agent prepared. The agent writes the PR text to
# .scratch/prs/<branch-with-dashes>.md — first line is the title, the rest is
# the body. You run this script: it pushes and opens the PR (agents have no
# GitHub access by policy). Review and merge happens in the GitHub UI.
# Usage: scripts/open-pr.sh [branch]   (default: current branch)
set -euo pipefail

branch="${1:-$(git branch --show-current)}"
body=".scratch/prs/${branch//\//-}.md"

if [ ! -f "$body" ]; then
    echo "no PR text found at $body" >&2
    exit 1
fi

git push -u origin "$branch"

title=$(head -1 "$body")
tmp=$(mktemp)
tail -n +2 "$body" > "$tmp"

gh pr create --base main --head "$branch" --title "$title" --body-file "$tmp"
rm -f "$tmp"
echo
gh pr view --json url -q .url
