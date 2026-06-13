# Releasing BYOR

Releases are published to [PyPI](https://pypi.org/project/byor/) by the
`Release` workflow when a GitHub Release is published. The version lives in
`pyproject.toml`; git tags and GitHub Releases drive the publish.

## One-time setup: PyPI Trusted Publishing

The workflow authenticates with [Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC) — no API tokens are stored in GitHub. Before the first release, register
a **pending publisher** on PyPI (works before the project exists):

1. Sign in at <https://pypi.org/manage/account/publishing/>.
2. Add a pending publisher:
   - PyPI Project Name: `byor`
   - Owner: `RyanSaxe`
   - Repository: `byor`
   - Workflow name: `release.yml`
   - Environment: `pypi`
3. In the GitHub repo settings, create an Environment named `pypi`
   (Settings → Environments). Add reviewers there if you want a manual approval
   gate before each publish.

## Cutting a release

1. Make sure `main` (or the release branch) is green and `CHANGELOG.md`'s
   `[Unreleased]` section captures the changes.
2. Bump the version:

   ```bash
   uv version --bump patch   # or minor / major
   ```

3. In `CHANGELOG.md`, move `[Unreleased]` entries into a new
   `## [X.Y.Z] - YYYY-MM-DD` section and update the compare links at the bottom.
4. Commit and push:

   ```bash
   git add pyproject.toml uv.lock CHANGELOG.md
   git commit -m "chore: release vX.Y.Z"
   git push
   ```

5. Publish a [GitHub Release](https://github.com/RyanSaxe/byor/releases/new)
   with tag `vX.Y.Z` (matching the bumped version) and notes from the changelog.

The workflow then verifies the tag matches `pyproject.toml`, runs the gates,
builds the sdist and wheel, and publishes to PyPI. A mismatched tag fails the
build before anything is uploaded.

## Versioning

[Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`. While the API
is still settling, pre-1.0 minor bumps may carry breaking changes — keep those
loud in the changelog. Cut `1.0.0` once the CLI and rule format are stable.
