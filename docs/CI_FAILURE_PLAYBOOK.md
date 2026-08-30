# CI Failure Playbook

Goal: diagnose the failed stage first, preserve successful work, and rerun the smallest safe unit instead of restarting everything blindly.

## First response

1. Identify repository, workflow run ID, job ID, runner name, branch/tag, commit SHA, and current/failed step.
2. Check whether the runner is online, locally listening, and busy with another job.
3. Read the failed job log and classify the failure before changing anything.
4. Protect any valid artifact already produced. A notification/release failure must not cause a valid binary to be rebuilt unnecessarily.
5. Prefer rerunning one failed job. Use "rerun failed jobs" only when several dependent jobs failed. Start an entirely new run only when the source/workflow/configuration changed.

## Failure classes

### Job queued but never starts

**Signals**
- Workflow remains queued/waiting.
- No runner name is assigned, or the expected runner is offline.

**Checks/actions**
- Confirm a matching self-hosted runner registration exists for that repository.
- Confirm `Runner.Listener` is alive locally.
- Check labels required by `runs-on`.
- If all healthy matching runners are busy, wait rather than spawning duplicate builds.
- Restart only the missing listener; do not kill healthy listeners or active workers.

### Assigned / stuck starting

**Signals**
- Runner is assigned but no first workflow step starts for an abnormal period.

**Checks/actions**
- Confirm the assigned local listener is still alive.
- Inspect the runner diagnostic log and available disk/RAM.
- Check stale worker/listener state.
- Do not restart the whole runner fleet while unrelated builds are active.

### Checkout / GitHub transport failure

**Signals**
- `actions/checkout` timeout, HTTP/TLS reset, low-speed timeout, or fetch error.

**Checks/actions**
- Verify GitHub connectivity from the runner.
- Preserve the global HTTP/1.1 stability profile.
- Retry the checkout/job after connectivity is healthy.
- Avoid deleting a warm workspace unless corruption is confirmed.

### Flutter / package resolution failure

**Signals**
- `flutter pub get`, Dart dependency solving, or package download fails.

**Checks/actions**
- Distinguish dependency conflict from temporary network/download failure.
- Verify the persistent Flutter SDK version is the one expected by the project.
- Do not upgrade Flutter globally merely to fix one package failure without checking project constraints.

### Generated code / build_runner failure

**Signals**
- Drift/generated files conflict, builder exits non-zero, stale generated output.

**Checks/actions**
- Read the first real builder error rather than the final generic failure.
- Use the project's intended build_runner command and conflict policy.
- Clear only generated output proven stale; do not wipe all caches by default.

### Analyze / test failure

**Signals**
- `flutter analyze` or `flutter test` fails.

**Checks/actions**
- Treat as a product-quality failure, not an infrastructure failure.
- Fix source/test expectations on a development branch.
- Do not bypass the gate to obtain a green release build unless the gate itself is demonstrably incorrect.

### Gradle daemon / OOM / resource failure

**Signals**
- Daemon disappears, Java OOM, process killed, system thrashing.

**Checks/actions**
- Check host free RAM/swap and concurrent heavy jobs.
- Keep heavy compiles serialized through the Fujitsu build lock/class scheduler.
- Preserve the conservative shared Gradle profile rather than increasing workers aggressively.
- Retry after competing heavy work is gone.
- If repeatable, profile the failing module before increasing JVM memory.

### Android SDK / Java / toolchain missing

**Signals**
- Missing platform/build-tools, wrong Java, SDK path not found.

**Checks/actions**
- Verify persistent `JAVA_HOME`, `ANDROID_HOME`, and `ANDROID_SDK_ROOT`.
- Verify required API/build-tools already exist before downloading anything.
- Repair the persistent toolchain once; avoid reinstalling it in every workflow.

### Signing failure

**Signals**
- Keystore missing, alias/password mismatch, APK/AAB signature verification fails.

**Checks/actions**
- Never print signing secrets.
- Verify secret/config presence without echoing values.
- Confirm the expected signing identity/fingerprint.
- A binary with wrong or unverifiable signing is BLOCKED even if compilation succeeded.

### Artifact missing after successful compile

**Signals**
- Build step succeeds but NAS has no fresh APK/AAB/firmware.

**Checks/actions**
- Confirm the final file exists inside the runner workspace and has a modification time from this job.
- Inspect the NAS completion-hook log for `no-fresh-final-artifacts`, config missing, or NAS unreachable.
- Verify the collector's file pattern matches the actual output.
- Do not rebuild if the valid binary still exists locally; store/deliver that binary first.

### NAS unreachable

**Signals**
- SSH/rsync/tar transfer to the NAS fails.

**Checks/actions**
- The collector should retain/spool the local candidate instead of deleting it.
- Verify NAS network/SSH availability and free space.
- Retry storage from the existing binary when possible.
- Treat GitHub Actions artifact storage as fallback only when deliberately configured, not the default large-binary store.

### Telegram delivery failure

**Signals**
- Build/NAS storage is successful but message/document delivery fails.

**Checks/actions**
- Do not rebuild the app/firmware.
- Verify notifier configuration and Telegram reachability without exposing token/chat ID.
- Retry only delivery.
- NAS remains the source of truth.

### GitHub release/publish failure

**Signals**
- Build and checks pass but release/tag/upload step fails.

**Checks/actions**
- Preserve already-built signed artifacts.
- Fix permissions/tag/release metadata and rerun only publishing where possible.
- Do not compile again unless the binary itself must change.

### OpenWrt feed/download failure

**Signals**
- Feed update/install, source download, hash verification, or package fetch fails.

**Checks/actions**
- Determine whether the failure is upstream/transient or a pinned-source/hash mismatch.
- Preserve the existing download cache when valid.
- Do not "fix" a checksum mismatch by disabling verification.

### OpenWrt compile/package failure

**Signals**
- Toolchain/package compile error, kernel/package ABI mismatch, image generation failure.

**Checks/actions**
- Find the earliest package that failed and rebuild that scope with verbose output when needed.
- Keep target/subtarget/profile unchanged while diagnosing.
- Verify image generation produced the correct factory/sysupgrade/initramfs type before declaring success.

### Disk full / inode exhaustion

**Signals**
- `No space left on device`, extraction failures, Gradle/Flutter caches fail unexpectedly.

**Checks/actions**
- Check both free bytes and inodes.
- Remove safe transient build directories/logs first.
- Keep persistent SDKs, signing material, NAS spool containing undelivered artifacts, and useful dependency caches unless specifically proven disposable.

## Rerun policy

- **One job failed, source unchanged:** rerun that job.
- **Several failed jobs from one transient infrastructure event:** rerun failed jobs only.
- **Workflow/config/source changed:** start a fresh run on the new commit.
- **Only NAS/Telegram/release delivery failed:** retry delivery/publish; do not rebuild.
- **Quality gate failed:** fix code/test first; fresh run after commit.
- **Signing identity wrong:** correct signing configuration and rebuild/re-sign according to project policy; never publish the wrong identity.

## Escalation packet

When a failure is not obvious, collect this compact packet before changing the host:

- repository + workflow/run/job IDs;
- commit SHA + branch/tag;
- runner name + labels;
- failed/current step;
- first meaningful error and the final 50-100 relevant log lines;
- CPU/RAM/disk snapshot;
- runner listener state;
- scheduler/build-lock state;
- NAS collector result if an artifact was expected;
- whether a valid local/NAS artifact already exists.

This packet is enough for Hermes/ChatGPT or a human operator to diagnose most failures without destructive trial-and-error.
