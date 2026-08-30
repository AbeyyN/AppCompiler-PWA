# AbeyyTechXy Build Operations Baseline

This document is the single operating baseline for the Fujitsu self-hosted build fleet, NAS artifact storage, Telegram delivery, and release handoff.

## Pipeline

1. GitHub Actions assigns the job to an eligible Fujitsu self-hosted runner.
2. The project performs its own quality gates and build/signing steps.
3. A successful runner job invokes the global completion hook.
4. The completion hook stores fresh final artifacts on the NAS first.
5. The NAS collector generates `SHA256SUMS.txt` and `build-info.json`.
6. The collector refreshes the project's `latest` view and keeps timestamped history.
7. Tagged/version refs are mirrored into the project's `releases` directory.
8. Existing Telegram delivery runs after NAS storage, unless the workflow already sent its own notification.
9. The build is accepted only after the manual test checklist passes.

## NAS layout

```text
CI-Artifacts/
├── Apps/
│   └── <repo>/
│       ├── latest/
│       ├── releases/
│       └── history/
├── OpenWrt/
│   └── <repo>/
│       ├── latest/
│       ├── releases/
│       └── history/
├── Packages/
│   └── <repo>/
│       ├── latest/
│       ├── releases/
│       └── history/
└── .system/
```

For app builds, APK/AAB/IPA files are captured. Explicit PWA/release/build ZIPs are captured as packages. OpenWrt repositories are treated specially and preserve flashable images plus reproducibility metadata such as checksums, buildinfo, profiles, feeds information, and manifests.

`latest` and versioned release views are hard-linked to history wherever possible to avoid wasting NAS capacity. Test/history retention is capped so old transient builds do not grow without bound.

The LAN artifact portal is `http://builds.home`.

## Telegram contract

- NAS storage is the first delivery target.
- Telegram is the convenience delivery/notification path, not the source of truth.
- An explicit workflow notifier takes precedence over the fallback global delivery helper.
- Duplicate delivery must be suppressed per repository/run ID.
- Telegram failure must not delete or invalidate an otherwise valid NAS artifact.
- Never place bot tokens or chat IDs in source-controlled files or logs.

## CI optimization baseline

The Fujitsu host should favor stability and cache reuse over maximum process count:

- persistent Flutter/Android/Gradle toolchains;
- shared persistent Gradle user home;
- Gradle daemon and build cache enabled;
- conservative worker count on the older Fujitsu CPU;
- no redundant SDK/toolchain reinstall on every job;
- shallow checkout where full history is unnecessary;
- warm workspaces where safe;
- build locks/classes to prevent multiple heavy compiles fighting for the same CPU/RAM;
- path-scoped workflows so docs/infra-only edits do not start expensive product builds;
- NAS-first final artifact storage instead of duplicating every large binary in GitHub Actions artifact storage;
- retry only the failed job/stage when the successful stages are reusable.

Do not restart listeners, purge Gradle caches, delete workspaces, or retune the host while a heavy build is actively compiling unless the running job itself is broken.

## Observability

Two surfaces have different jobs:

- **Fujitsu Dashboard** on port `9860`: runner inventory, online/busy state, queued/assigned/running jobs, current step, waiting/stuck detection, recent failure context, and Hermes CI Doctor state.
- **NAS build portal** at `builds.home`: final stored artifacts, latest build, releases, checksums, and retained history.

Together they represent the lifecycle:

```text
QUEUED → ASSIGNED → COMPILING/TESTING → STORED ON NAS → TELEGRAM → TESTED → RELEASED/ARCHIVED
```

## Change-safety rules

1. Infrastructure-only work must not modify application source unless the product task explicitly requires it.
2. Avoid touching a workflow currently executing unless fixing that exact failed run.
3. Prefer idempotent installers/hooks so re-running infrastructure setup is safe.
4. Preserve a known-good artifact and its checksum before replacing `latest`.
5. OpenWrt builds require stronger recovery discipline: keep original firmware/config backup and verify the exact target before flashing.
6. A successful compile is not automatically a release; the acceptance checklist remains mandatory.
