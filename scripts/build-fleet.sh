#!/usr/bin/env bash
set -euo pipefail
ROOT="${GITHUB_WORKSPACE:-$(pwd)}"
STATE="$ROOT/.pwa-state.tsv"
[ -r /home/abeyy/bin/redmi-build-env.sh ] && . /home/abeyy/bin/redmi-build-env.sh
export PATH="/home/abeyy/.local/bin:/home/abeyy/bin:/home/abeyy/flutter/bin:/home/abeyy/tools/flutter/bin:$PATH"
GH_LOCAL=(env -u GH_TOKEN -u GITHUB_TOKEN gh)
"${GH_LOCAL[@]}" auth setup-git >/dev/null
touch "$STATE"
FORCE=0
[ "${GITHUB_EVENT_NAME:-}" = 'workflow_dispatch' ] && FORCE=1

get_old() { awk -v k="$1" '$1==k{print $2}' "$STATE" | tail -1; }
set_new() { local k="$1" v="$2"; awk -v k="$k" '$1!=k' "$STATE" > "$STATE.tmp" || true; printf '%s\t%s\n' "$k" "$v" >> "$STATE.tmp"; mv "$STATE.tmp" "$STATE"; }
remote_sha() { local repo="$1" branch="$2" enc; enc=$(python3 - "$branch" <<'PY'
import sys, urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=''))
PY
); "${GH_LOCAL[@]}" api "repos/$repo/commits/$enc" --jq .sha; }
clone_src() { local repo="$1" branch="$2" dest="$3"; git clone --depth 1 --branch "$branch" "https://github.com/${repo}.git" "$dest"; }
should_build() { local slug="$1" sha="$2" old; old=$(get_old "$slug"); [ "$FORCE" -eq 1 ] || [ "$old" != "$sha" ]; }

with_build_lock() {
  if command -v redmi-build-lock >/dev/null 2>&1; then
    REDMI_BUILD_CLASS=light redmi-build-lock bash -lc "$1"
  else
    bash -lc "$1"
  fi
}

build_x7() {
  local tmp sha
  sha=$(remote_sha 'AbeyyN/X7-Core-Academy' 'main')
  should_build x7 "$sha" || { echo 'X7 unchanged'; return; }
  tmp=$(mktemp -d /tmp/pwa-x7.XXXXXX)
  clone_src 'AbeyyN/X7-Core-Academy' 'main' "$tmp/src"
  cd "$tmp/src/apps/x7-kids-academy/unified"
  flutter create . --platforms=web --org my.abeyytechxy --project-name x7kidsacademy
  flutter pub get
  with_build_lock '
    set -euo pipefail
    flutter analyze
    flutter test
    flutter build web --release --base-href /AppCompiler-PWA/x7/
  '
  rm -rf "$ROOT/x7"; mkdir -p "$ROOT/x7"; cp -a build/web/. "$ROOT/x7/"
  cp "$ROOT/x7/index.html" "$ROOT/x7/404.html" || true
  set_new x7 "$sha"
}

build_xma7() {
  local tmp sha
  sha=$(remote_sha 'AbeyyN/XMA7' 'feature/v1.1-collaborative-pilot')
  should_build xma7 "$sha" || { echo 'XMA7 unchanged'; return; }
  tmp=$(mktemp -d /tmp/pwa-xma7.XXXXXX)
  clone_src 'AbeyyN/XMA7' 'feature/v1.1-collaborative-pilot' "$tmp/src"
  test -s "$tmp/src/pwa/index.html"; test -s "$tmp/src/pwa/manifest.webmanifest"; test -s "$tmp/src/pwa/sw.js"
  rm -rf "$ROOT/xma7"; mkdir -p "$ROOT/xma7"; cp -a "$tmp/src/pwa/." "$ROOT/xma7/"
  find "$ROOT/xma7" -type f -print0 | xargs -0 -r sed -i 's#/XMA7-PWA/#/AppCompiler-PWA/xma7/#g; s#XMA7-PWA#AppCompiler-PWA/xma7#g' || true
  cp "$ROOT/xma7/index.html" "$ROOT/xma7/404.html" || true
  set_new xma7 "$sha"
}

build_sk() {
  local tmp sha
  sha=$(remote_sha 'AbeyyN/SK-Gong-Kapas' 'main')
  should_build sk-gong-kapas "$sha" || { echo 'SK unchanged'; return; }
  tmp=$(mktemp -d /tmp/pwa-sk.XXXXXX)
  clone_src 'AbeyyN/SK-Gong-Kapas' 'main' "$tmp/src"
  cd "$tmp/src"
  flutter create . --platforms=web --org my.abeyytechxy --project-name sk_gong_kapas
  flutter pub get
  with_build_lock '
    set -euo pipefail
    flutter analyze
    flutter test
    flutter build web --release --base-href /AppCompiler-PWA/sk-gong-kapas/
  '
  rm -rf "$ROOT/sk-gong-kapas"; mkdir -p "$ROOT/sk-gong-kapas"; cp -a build/web/. "$ROOT/sk-gong-kapas/"
  cp "$ROOT/sk-gong-kapas/index.html" "$ROOT/sk-gong-kapas/404.html" || true
  set_new sk-gong-kapas "$sha"
}

build_xsahub() {
  local tmp sha
  sha=$(remote_sha 'AbeyyN/XSAHub' 'main')
  should_build xsahub "$sha" || { echo 'XSAHub unchanged'; return; }
  tmp=$(mktemp -d /tmp/pwa-xsahub.XXXXXX)
  clone_src 'AbeyyN/XSAHub' 'main' "$tmp/src"
  cd "$tmp/src"
  python3 -m venv .icon-venv
  .icon-venv/bin/pip -q install pillow
  .icon-venv/bin/python tool/generate_icon.py
  flutter create . --platforms=web --project-name xsahub --org my.abeyytechxy
  python3 tool/prepare_source.py
  python3 tool/prepare_web.py
  flutter pub get
  with_build_lock '
    set -euo pipefail
    flutter analyze --no-fatal-infos --no-fatal-warnings
    flutter build web --release --base-href /AppCompiler-PWA/xsahub/
  '
  rm -rf "$ROOT/xsahub"; mkdir -p "$ROOT/xsahub"; cp -a build/web/. "$ROOT/xsahub/"
  cp "$ROOT/xsahub/index.html" "$ROOT/xsahub/404.html" || true
  set_new xsahub "$sha"
}

cd "$ROOT"
build_x7
build_xma7
build_sk
build_xsahub

cat > "$ROOT/index.html" <<'HTML'
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AbeyyTechXy PWA Fleet</title></head><body><h1>AbeyyTechXy PWA Fleet</h1><ul><li><a href="x7/">X7 Core Academy</a></li><li><a href="xma7/">XMA7</a></li><li><a href="sk-gong-kapas/">SK Gong Kapas</a></li><li><a href="xsahub/">XSAHub</a></li></ul></body></html>
HTML
touch "$ROOT/.nojekyll"
cd "$ROOT"
git config user.name 'AbeyyTechXy Redmi PWA Builder'
git config user.email '86111714+AbeyyN@users.noreply.github.com'
git add -A
if ! git diff --cached --quiet; then
  git commit -m "deploy: refresh PWA fleet ${GITHUB_RUN_ID:-manual}"
  git push origin main
else
  echo 'No PWA changes to publish.'
fi

for path in x7 xma7 sk-gong-kapas xsahub; do
  url="https://abeyyn.github.io/AppCompiler-PWA/$path/"
  echo "VERIFY $url"
  for i in $(seq 1 18); do timeout 20s curl -fsSL "$url" -o /tmp/pwa-verify.html && break || sleep 10; done
done

if [ -x /home/abeyy/bin/redmi-notify-job ]; then
  /home/abeyy/bin/redmi-notify-job success 'AbeyyN/AppCompiler-PWA' 'Central PWA Fleet' main "${GITHUB_RUN_ID:-manual}" || true
fi
