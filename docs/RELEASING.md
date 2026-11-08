# Container builds and releases

<p align="center"><strong>English</strong> · <a href="nb/README.md">Norsk bokmål</a> · <a href="pl/README.md">Polski</a> · <a href="ja/README.md">日本語</a> · <a href="zh/README.md">简体中文</a> · <a href="es/README.md">Español</a></p>

Translated introductions are linked above. This detailed technical guide is maintained in English.

`VERSION` and the runtime `__version__` share one display version. For the initial
release, `1.0.0-snapshot` promotes to `v1.0.0`. The HTTP status includes the application version, source commit,
build date and image flavor. Local images use `unknown`/`dev` build metadata.

## Development and validation

Run `task validate` for lightweight source/publication/release checks and
`task test:cpu` for the full container test suite. `task test:gpu` adds numerical
GPU tests. `task docker:build:tiny` and `task docker:build:full` build the two local
variants. Both include exact dependency source archives under `/app/runtime-sources`.
`task docker:evidence` exports runtime notices and model/source manifests into
ignored `.ci-build/evidence`. These local build/export commands publish nothing.

Qualify the exact packaged candidate on GPU, including a 1024×1024 fixed-seed
generation and an offline restart. A full-image qualification must start with an
empty cache and networking disabled to prove it includes every required asset.
Run benchmarks separately; do not interpret build or smoke-test timings as
performance results. Do not prune unrelated Docker images, caches or data volumes.

## Automated publication

Pull requests run lightweight validation and the full CPU container suite, without
registry login or publication. On the canonical repository, pushes to `main`
publish moving `latest`/`latest_tiny` tags to Docker Hub and GHCR. Stable Git tags
`vX.Y` or `vX.Y.Z` publish their full and `_tiny` equivalents. A manual workflow
can select an existing stable tag and optionally update moving tags. Every build
checks out the exact commit that passed validation.

Configure repository secrets `DOCKER_USERNAME` and `DOCKER_TOKEN` with access to
`hangrylabs/flux2-klein-studio`. GHCR uses the workflow's `GITHUB_TOKEN` with
`packages:write`; verify package visibility after the first publication. No Hugging
Face secret is required for the pinned public assets. Actions are pinned to full
commit SHAs. Publications are serialized and run only on Linux amd64.

Application code is licensed under [Apache-2.0](../LICENSE). The distribution gate
requires this `LICENSE` before runner cleanup, registry login or publication.
Model and dependency terms remain documented separately in
[THIRD_PARTY.md](../THIRD_PARTY.md) and [NOTICE](../NOTICE).

Only the standard and tiny images are published. The build collects runtime
notices, verifies reviewed wheel versions/native hashes and resolves exact Debian
source identities. Both application variants include the verified archives,
manifest and checksums at `/app/runtime-sources`, so they travel with the matching
binaries. There are no source-image tags or separate source-image publication.
Missing notices, unavailable source versions or changed reviewed native files
fail collection. `tools/debian-source-index.txt` retains reviewed descriptor
checksums for the older tzdata version in the pinned base; its exact `.dsc` and
archives are included. Stable application tags are checked for prior existence
and never overwritten, including partial releases.

Full/tiny builds attach provenance and SBOM attestations. The workflow compares
the top-level OCI index digest of every tag in both registries to the corresponding
build result. `.ci-build/image-plan.json`, `published-images.json`, runtime
licenses and pinned asset/source manifests are retained as a 30-day Actions
artifact. Preserve release evidence longer when making a stable release.

Hosted-runner disk cleanup applies only to the disposable GitHub runner. The
project does not clean the developer's Docker installation.

## Promote a snapshot

First review the source, CPU/GPU qualification and release-note bullets. Then:

```shell
task release:dry-run
task release:dry-run -- --show-diff
```

The dry run validates both version/document transformations entirely in memory,
even with a dirty checkout. It creates no files, builds, commits, tags or pushes.
The real command below is for an explicitly authorized release:

```shell
task release
```

It requires clean `main` synchronized with `origin/main`, promotes the current
snapshot, runs `task validate`, commits it, creates an annotated stable tag,
commits the next development snapshot, and atomically pushes `main` and that tag.
The default next version increments the minor number and preserves the number of
version components: `1.0.0-snapshot` promotes to `v1.0.0` and then starts
`1.1.0-snapshot`. Pass `--next-version 1.0.1-snapshot` to select a newer snapshot
explicitly.
`--skip-validation` is available only when the unchanged candidate has already
passed the required checks. It does not waive GPU qualification or notice review.

The helper returns after pushing; it does not wait for CI or create a GitHub
Release page. Confirm both registries' digests and package visibility, add actual
digest-pinned deployment commands to the docs, then announce availability. Never
invent digests or move a published release tag. If publication fails partway,
inspect the recorded artifacts and existing registry tags before retrying; use a
new release version when immutable binary tags already exist.
