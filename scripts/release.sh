#!/usr/bin/env bash
#
# Cut a modric-agent release in one shot: bump the version, commit it, tag, build the
# source tarball, publish a GitHub release with the tarball attached, and print the
# values to paste into Toil's [soil] config so the "Upgrade agent" button can use it.
#
# The version that matters is `project.version` in pyproject.toml *inside the tarball*
# (it drives both the filename and what the agent reports) — NOT the git tag. Because
# the build uses `git archive HEAD`, the bump must be committed before building; this
# script does that for you and then verifies the built tarball actually carries it.
#
# Usage:
#   scripts/release.sh [VERSION]     # e.g. 1.0.1  (leading "v" is fine too)
#     - VERSION given & different  -> bump pyproject.toml + commit, then release it
#     - VERSION omitted            -> release the current pyproject version as-is
#
# Env overrides:
#   REPO   GitHub repo (default: mangosteen-lab/modric-agent)
#   NOTES  release notes (default: "modric-agent <version>")
#
# Prereqs: gh CLI logged in (`gh auth login`), a clean working tree, push access.
set -euo pipefail

# Run from the repo root regardless of where the script is invoked.
cd "$(dirname "$0")/.."

REPO="${REPO:-mangosteen-lab/modric-agent}"

pyproject_version() {
  python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
}

# Normalise an optional VERSION arg (strip a leading "v").
REQUESTED="${1:-}"; REQUESTED="${REQUESTED#v}"

# --- preflight -------------------------------------------------------------
command -v gh >/dev/null       || { echo "error: gh CLI not found (install it first)"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "error: not logged in — run: gh auth login"; exit 1; }
if [ -n "$(git status --porcelain)" ]; then
  echo "error: working tree is not clean — commit or stash first (dist/ is ignored)."; exit 1
fi

# --- 1. bump + commit the version (so `git archive HEAD` includes it) ------
CURRENT="$(pyproject_version)"
if [ -n "$REQUESTED" ] && [ "$REQUESTED" != "$CURRENT" ]; then
  echo ">> Bumping version $CURRENT -> $REQUESTED"
  python3 - "$REQUESTED" <<'PY'
import re, sys, pathlib
version = sys.argv[1]
p = pathlib.Path("pyproject.toml")
text, n = re.subn(r'(?m)^version\s*=\s*".*"$', f'version = "{version}"', p.read_text(), count=1)
if n != 1:
    sys.exit("could not find a single `version = \"...\"` line in pyproject.toml")
p.write_text(text)
PY
  git commit -aqm "Release $REQUESTED"
fi

VERSION="$(pyproject_version)"
TAG="v$VERSION"
TARBALL="dist/modric-agent-$VERSION.tar.gz"
echo ">> Releasing $REPO @ $TAG (version $VERSION)"

# --- 2. build the tarball, then VERIFY it actually carries this version -----
# Guards against the classic footgun: an un-committed bump so the archive (HEAD) still
# holds the old version even though the filename says otherwise.
make release-tarball >/dev/null
[ -f "$TARBALL" ] || { echo "error: expected $TARBALL after build"; exit 1; }
IN_TAR="$(tar xzOf "$TARBALL" --wildcards '*/pyproject.toml' | sed -n 's/^version *= *"\(.*\)"/\1/p' | head -1)"
if [ "$IN_TAR" != "$VERSION" ]; then
  echo "error: tarball contains version '$IN_TAR' but expected '$VERSION'."
  echo "       Did the bump get committed? (build uses 'git archive HEAD')"; exit 1
fi
SHA256="$(python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$TARBALL")"
echo ">> Verified tarball version = $IN_TAR"
# SHA256SUMS asset — the installer (scripts/install.sh/.ps1) verifies the tarball against it.
( cd dist && printf '%s  %s\n' "$SHA256" "$(basename "$TARBALL")" > SHA256SUMS )

# --- 3. tag HEAD, push the commit + tag ------------------------------------
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || git tag "$TAG"
git push origin HEAD
git push origin "$TAG"

# --- 4. create the release (or upload the assets if it already exists) ------
if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  echo ">> Release $TAG exists; uploading/overwriting the assets."
  gh release upload "$TAG" "$TARBALL" dist/SHA256SUMS --repo "$REPO" --clobber
else
  gh release create "$TAG" "$TARBALL" dist/SHA256SUMS \
    --repo "$REPO" \
    --title "modric-agent $VERSION" \
    --notes "${NOTES:-modric-agent $VERSION}"
fi

# --- 5. resolve the asset download URL & print Toil config -----------------
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
