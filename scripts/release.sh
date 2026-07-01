#!/usr/bin/env bash
#
# Build a modric-agent source tarball, tag it, publish a GitHub release with the
# tarball attached, and print the values to paste into Toil's [soil] upgrade config
# so the machines panel's "Upgrade agent" button can hot-upgrade agents.
#
# Usage:
#   scripts/release.sh [TAG]        # TAG defaults to v<version-from-pyproject.toml>
#
# Env overrides:
#   REPO   GitHub repo (default: mangosteen-lab/modric-agent)
#   NOTES  release notes (default: "modric-agent <version>")
#
# Prereqs: gh CLI logged in (`gh auth login`), a clean working tree, push access.
# Bump `project.version` in pyproject.toml before releasing a new version.
set -euo pipefail

# Run from the repo root regardless of where the script is invoked.
cd "$(dirname "$0")/.."

REPO="${REPO:-mangosteen-lab/modric-agent}"

VERSION="$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
TAG="${1:-v$VERSION}"
TARBALL="dist/modric-agent-$VERSION.tar.gz"

# --- preflight -------------------------------------------------------------
command -v gh >/dev/null   || { echo "error: gh CLI not found (install it first)"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "error: not logged in — run: gh auth login"; exit 1; }
if [ -n "$(git status --porcelain)" ]; then
  echo "error: working tree is not clean — commit or stash first (dist/ is ignored)."; exit 1
fi

echo ">> Releasing $REPO @ $TAG (version $VERSION)"

# --- 1. build the tarball (git archive of HEAD) ----------------------------
# Done before tagging so a build failure doesn't leave a pushed tag behind.
make release-tarball >/dev/null
[ -f "$TARBALL" ] || { echo "error: expected $TARBALL after build"; exit 1; }
SHA256="$(python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$TARBALL")"

# --- 2. tag HEAD and push it ----------------------------------------------
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo ">> Tag $TAG already exists locally; reusing it."
else
  git tag "$TAG"
fi
git push origin "$TAG"

# --- 3. create the release (or upload the asset if it already exists) ------
if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  echo ">> Release $TAG exists; uploading/overwriting the tarball."
  gh release upload "$TAG" "$TARBALL" --repo "$REPO" --clobber
else
  gh release create "$TAG" "$TARBALL" \
    --repo "$REPO" \
    --title "modric-agent $VERSION" \
    --notes "${NOTES:-modric-agent $VERSION}"
fi

# --- 4. resolve the asset download URL & print Toil config -----------------
URL="$(gh release view "$TAG" --repo "$REPO" --json assets \
        --jq ".assets[] | select(.name==\"modric-agent-$VERSION.tar.gz\") | .url")"

cat <<EOF

Release published: https://github.com/$REPO/releases/tag/$TAG

Paste into Toil [soil] (then restart Toil):
  latest_version           = $VERSION
  upgrade_artifact_url     = $URL
  upgrade_artifact_sha256  = $SHA256

Note: if $REPO is private, machines cannot download this asset without auth —
host the tarball where the agents can reach it unauthenticated instead.
EOF
