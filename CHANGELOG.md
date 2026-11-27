# Changelog

## [0.5.0](https://github.com/otaviobarros/seatrial/compare/v0.4.0...v0.5.0) (2026-11-27)


### Features

* keep results across runs and show them with seatrial trend ([#52](https://github.com/otaviobarros/seatrial/issues/52)) ([fe09b70](https://github.com/otaviobarros/seatrial/commit/fe09b706988de531e1013fc0d810fb6a4c22fb01))
* measure each case's noise floor with an A/A run ([#51](https://github.com/otaviobarros/seatrial/issues/51)) ([e769070](https://github.com/otaviobarros/seatrial/commit/e7690707c6421ed7b96315f3ef87c3375fa74aa7))
* pool a verdict per skill across its cases ([#53](https://github.com/otaviobarros/seatrial/issues/53)) ([c806ebe](https://github.com/otaviobarros/seatrial/commit/c806ebe6d6e88fd99d35882272215de40a43b7a6))


### Bug Fixes

* evaluate the seatrial skill in CI and stop counting harmless Git files as changes ([#58](https://github.com/otaviobarros/seatrial/issues/58)) ([1e8099d](https://github.com/otaviobarros/seatrial/commit/1e8099d8788cab9e8faf782c0500241b6a217d85))
* **skill:** plan compare runs and ask for a budget before trials ([#59](https://github.com/otaviobarros/seatrial/issues/59)) ([46129a5](https://github.com/otaviobarros/seatrial/commit/46129a5ecc808ad4cc20edb136d998d3592ca89f))


### Documentation

* add the end-to-end workflow and close gaps after recent features ([#56](https://github.com/otaviobarros/seatrial/issues/56)) ([0da8ecb](https://github.com/otaviobarros/seatrial/commit/0da8ecb6f9d21d57ea580b91a208e318ac95b555))
* keep the nightly base-ref example out of release-please version bumps ([#57](https://github.com/otaviobarros/seatrial/issues/57)) ([60ab9f3](https://github.com/otaviobarros/seatrial/commit/60ab9f3b4653c2824b01acf1a2f1bf92705922e5))

## [0.4.0](https://github.com/otaviobarros/seatrial/compare/v0.3.0...v0.4.0) (2026-11-25)


### Features

* compare cost between arms and report effect sizes ([#49](https://github.com/otaviobarros/seatrial/issues/49)) ([162138d](https://github.com/otaviobarros/seatrial/commit/162138d05bc292db10ca5c42f9331d4511a65f67))
* correct verdicts for multiple comparisons across cases ([#45](https://github.com/otaviobarros/seatrial/issues/45)) ([6aabf5d](https://github.com/otaviobarros/seatrial/commit/6aabf5dd599d72a1a7a3ef8654ae983bdd80b90c))
* record tool calls and add trajectory grading helpers ([#38](https://github.com/otaviobarros/seatrial/issues/38)) ([b4ca33f](https://github.com/otaviobarros/seatrial/commit/b4ca33f45680d2a9df74201d122264fd4182a13a))
* run trials sequentially and stop when the verdict is clear ([#46](https://github.com/otaviobarros/seatrial/issues/46)) ([e410d3b](https://github.com/otaviobarros/seatrial/commit/e410d3b6e8ad0c16c18ffcf53481720213498923))
* select cases from changed files with --changed REF ([#43](https://github.com/otaviobarros/seatrial/issues/43)) ([466699b](https://github.com/otaviobarros/seatrial/commit/466699b059c410fe1755ed31657e77e112ed2c47))
* serve stub MCP tools from a case ([#50](https://github.com/otaviobarros/seatrial/issues/50)) ([93c65fb](https://github.com/otaviobarros/seatrial/commit/93c65fb664b93582773622bb09f200cf375e6270))
* ship a reusable GitHub workflow for pull request and nightly runs ([#48](https://github.com/otaviobarros/seatrial/issues/48)) ([ecaf944](https://github.com/otaviobarros/seatrial/commit/ecaf944b8c9b170ae200bf7129b936144b5531f2))
* show Wilson intervals, a combined verdict reading, and --format comment ([#44](https://github.com/otaviobarros/seatrial/issues/44)) ([7fa3fd3](https://github.com/otaviobarros/seatrial/commit/7fa3fd3aec82262a30be91160b78c2f58cda1f2b))

## [0.3.0](https://github.com/otaviobarros/seatrial/compare/v0.2.0...v0.3.0) (2026-11-23)


### Features

* compare a skill against running without it ([#16](https://github.com/otaviobarros/seatrial/issues/16)) ([867b430](https://github.com/otaviobarros/seatrial/commit/867b430ffa475a4326a6f9bfa04270b12f597daf))
* test whether the agent picks up a skill on its own ([#17](https://github.com/otaviobarros/seatrial/issues/17)) ([ab520e7](https://github.com/otaviobarros/seatrial/commit/ab520e73d3b0db440a7078194890a6c9b3ad2541))


### Bug Fixes

* record the skill an explicit Claude prompt invokes ([#24](https://github.com/otaviobarros/seatrial/issues/24)) ([ab9bf0b](https://github.com/otaviobarros/seatrial/commit/ab9bf0b8342595997416f9f792ede8de821244d5))
* stop the plan when a case cannot load ([#23](https://github.com/otaviobarros/seatrial/issues/23)) ([749017a](https://github.com/otaviobarros/seatrial/commit/749017a02757a73e9c6b806071bb425ead5a795f))

## [0.2.0](https://github.com/otaviobarros/seatrial/compare/v0.1.0...v0.2.0) (2026-11-22)


### Features

* hold cases out of tuning with holdout = true ([#14](https://github.com/otaviobarros/seatrial/issues/14)) ([4bbc7ec](https://github.com/otaviobarros/seatrial/commit/4bbc7ecc8e3a0ce4e5b34649b4dfcc747e7ad07c))

## 0.1.0 (2026-11-21)


### Features

* add seatrial package, docs, and CI ([8fef77a](https://github.com/otaviobarros/seatrial/commit/8fef77a279e988c353eea93e5c6cade4df1eb3ab))
