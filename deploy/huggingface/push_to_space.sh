#!/usr/bin/env bash
#
# Publish this project as a Hugging Face Space.
#
#   ./deploy/huggingface/push_to_space.sh <owner>/<space-name>
#
# A Space is its own git repo. This assembles one from the project — the source,
# the packs, the packaging metadata — plus the two files a Space needs at its
# root: a Dockerfile, and a README whose front-matter tells Spaces to build it.
#
# The Space's README is *not* this project's README. It is the card visitors
# land on, and it leads with the fact that a hosted instance is shared, because
# the app itself promises the opposite when you run it locally.
#
# Requires: git, and `hf auth login` with write access to the target.

set -euo pipefail

SPACE="${1:-}"
if [[ -z "$SPACE" || "$SPACE" != */* ]]; then
    echo "usage: $0 <owner>/<space-name>" >&2
    echo "  e.g. $0 sriramarun/synthetic-data-designer" >&2
    exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

echo "==> Checking you are logged in"
hf auth whoami >/dev/null || {
    echo "Not logged in. Run: hf auth login" >&2
    exit 1
}

echo "==> Creating the Space if it does not exist"
hf repo create "$SPACE" --repo-type space --space-sdk docker --exist-ok

echo "==> Cloning https://huggingface.co/spaces/$SPACE"
git clone "https://huggingface.co/spaces/$SPACE" "$STAGING/space"
cd "$STAGING/space"

echo "==> Assembling"
# Only what the image builds from. No tests, no docs, no generated data, and no
# .git — the Space has its own history.
rm -rf src packs pyproject.toml Dockerfile README.md
cp -R "$ROOT/src" "$ROOT/packs" .
cp "$ROOT/pyproject.toml" .
cp "$HERE/Dockerfile" .
cp "$HERE/README.md" .
find . -path ./.git -prune -o -name '__pycache__' -type d -print0 | xargs -0 rm -rf

echo "==> Pushing"
git add -A
if git diff --cached --quiet; then
    echo "Nothing changed."
    exit 0
fi
git commit -m "Deploy Synthetic Data Designer"
git push

echo
echo "Done: https://huggingface.co/spaces/$SPACE"
echo "The first build takes a few minutes. Watch the Logs tab for progress."
