# Release Test Checklist

Use this checklist for every candidate artifact before calling it stable/released. Record the repository, version, commit SHA, workflow run ID, device/target, artifact filename, and SHA-256 with the test result.

## Common artifact checks

- [ ] Candidate came from the intended repository, branch/tag, commit, and workflow run.
- [ ] Artifact exists in NAS `latest` or the intended versioned release directory.
- [ ] `SHA256SUMS.txt` exists and the candidate checksum matches.
- [ ] `build-info.json` points to the expected repository/run/commit/runner.
- [ ] Filename/version/build number is correct.
- [ ] No debug/test secret, temporary branding, diagnostic overlay, or unintended development endpoint is present.

## Android APK/AAB

### Install and upgrade

- [ ] Clean APK install succeeds.
- [ ] Upgrade over the previous supported build succeeds without losing expected local data.
- [ ] App launches after cold start and after device reboot.
- [ ] Version/build number shown by Android matches the intended candidate.
- [ ] Release signing is valid and uses the expected signing identity.
- [ ] AAB exists for Play-bound releases and is non-empty/valid.

### Core behavior

- [ ] First-run/onboarding path works.
- [ ] Main navigation/back behavior works without accidental app exits or dead ends.
- [ ] Required permissions are requested only when needed and denial is handled safely.
- [ ] Offline mode behaves as designed.
- [ ] Reconnect after offline use behaves as designed.
- [ ] Persistent/local data survives app restart.
- [ ] Remote/cloud data, if used, is consistent after refresh/relogin.
- [ ] No obvious crash, ANR, infinite loading state, or broken screen is observed.
- [ ] Links, downloads, updater flows, and external intents used by the app work.

### Regression and device coverage

- [ ] Every issue fixed in this version is explicitly re-tested.
- [ ] At least one clean install and one upgrade install are tested.
- [ ] Test on the main target Android version/device class.
- [ ] If compatibility changed, test one older supported Android version/device class.
- [ ] Network-dependent apps are tested on the real router/network type relevant to the release.
- [ ] Quick CPU/RAM/battery sanity check shows no obvious runaway behavior during normal use.

### Play candidate gate

- [ ] No debug signing.
- [ ] Package/application ID is correct.
- [ ] Target/compile SDK policy checks pass.
- [ ] Required privacy/data disclosures still match actual app behavior.
- [ ] Release notes match what is actually inside the binary.

## PWA

- [ ] Correct base path and deployment URL.
- [ ] Hard refresh loads successfully.
- [ ] App works after browser cache/service-worker update.
- [ ] Responsive layout works on phone and desktop widths.
- [ ] Offline/cache behavior matches the product design.
- [ ] No stale version assets are mixed with the new build.
- [ ] Main workflows behave consistently with the corresponding app build where parity is expected.

## OpenWrt / firmware

### Before flashing

- [ ] Exact router/device model and hardware revision confirmed.
- [ ] Current firmware/version recorded.
- [ ] Current configuration backup created.
- [ ] Recovery method is known and available before flashing.
- [ ] Factory/sysupgrade/initramfs image type is correct for the intended operation.
- [ ] Candidate checksum matches NAS checksum.
- [ ] Target/profile metadata matches the physical device.
- [ ] Critical device-specific calibration/MAC/EEPROM data is preserved when applicable.

### After flashing

- [ ] Device boots normally without boot loop.
- [ ] LAN access works.
- [ ] SSH works.
- [ ] LuCI/web management works when included.
- [ ] WAN obtains connectivity as expected.
- [ ] DNS works.
- [ ] 2.4 GHz/5 GHz radios and expected SSIDs work.
- [ ] Ethernet ports/VLANs behave correctly.
- [ ] Reboot retains configuration.
- [ ] Sysupgrade persistence behavior matches the intended settings.
- [ ] Expected packages/services start successfully.
- [ ] Logs contain no new critical errors attributable to the build.
- [ ] Recovery/failsafe path remains viable.

## Final decision

**PASS** only when all release-critical checks relevant to the artifact are complete. Mark the candidate **BLOCKED** if there is a reproducible crash, signing/version mismatch, checksum mismatch, data-loss regression, broken upgrade path, wrong firmware target, failed network baseline, or unavailable recovery path for firmware testing.

Record any non-blocking known issue in release notes instead of silently accepting it.
