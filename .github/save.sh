#!/usr/bin/env bash
# Commit whatever is staged and get it onto main, or fail loudly.
#
#   bash .github/save.sh "commit message" [branch]
#
# Stage the files first (git add ...). Nothing staged is a clean exit.
# 2026-11-09: the Monday harvest lost its commit to a rebase conflict with the 6-hourly archive search and
# still reported success, because the old retry loop ended on `sleep`. This script:
#   1. rebases on the branch and pushes, up to five times;
#   2. when the rebase conflicts, starts again from the branch as it stands and lays this job's own files
#      on top (the files this job wrote win; every other file keeps what is on the branch);
#   3. exits 1 when the push never landed, so the run goes red instead of green with nothing saved.
set -u
MSG="$1"
BRANCH="${2:-main}"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

if git diff --cached --quiet; then
  echo "save: nothing to commit"
  exit 0
fi
git commit -q -m "$MSG"
MINE=$(git rev-parse HEAD)

for i in 1 2 3 4 5; do
  if git fetch -q origin "$BRANCH"; then
    if ! git rebase -q "origin/$BRANCH" 2>/dev/null; then
      git rebase --abort 2>/dev/null || true
      echo "save: rebase conflict on try $i; replaying this job's files over origin/$BRANCH"
      git reset -q --hard "origin/$BRANCH"
      # every file this job's commit added or changed, taken whole from that commit
      git diff --name-only --diff-filter=AM "$MINE~1" "$MINE" -z | xargs -0 -r git checkout "$MINE" --
      git diff --name-only --diff-filter=D "$MINE~1" "$MINE" -z | xargs -0 -r git rm -q --ignore-unmatch --
      git add -A
      if git diff --cached --quiet; then
        echo "save: the branch already holds this job's output"
        exit 0
      fi
      git commit -q -m "$MSG"
    fi
    if git push -q origin "HEAD:$BRANCH"; then
      echo "save: pushed $(git rev-parse --short HEAD) to $BRANCH on try $i"
      exit 0
    fi
  fi
  sleep $((i * 7))
done
echo "::error::save: could not push '$MSG' to $BRANCH after 5 tries. Nothing was saved."
exit 1
