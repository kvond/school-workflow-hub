#!/bin/sh
set -eu
resources=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
{
 IFS= read -r schema
 IFS= read -r profile
 IFS= read -r extension_id
 IFS= read -r chrome_app
 IFS= read -r chrome_data
 # Reject added or incomplete configuration; never source it as shell code.
 if IFS= read -r extra; then exit 64; fi
} < "$resources/launch.conf"
[ "$schema" = 'school-morning/v1' ] || exit 64
case "$profile" in
 'Profile '*) number=${profile#Profile }; case "$number" in ''|*[!0-9]*) exit 64;; esac;;
 *) exit 64;;
esac
[ "${#extension_id}" -eq 32 ] || exit 64
case "$extension_id" in *[!a-p]*) exit 64;; esac
[ "$chrome_app" = '/Applications/Google Chrome.app' ] || exit 64
case "$chrome_data" in /*) ;; *) exit 64;; esac
[ -d "$chrome_data/$profile" ] || exit 64
[ -x "$chrome_app/Contents/MacOS/Google Chrome" ] || exit 64
[ -x "$resources/bounded-handoff" ] || exit 64
[ -f "$resources/handoff.sb" ] || exit 64
/usr/bin/open -b com.openai.codex || exit 65
url="chrome-extension://$extension_id/morning-setup.html"
# Process names only. No command lines, profile contents or login data.
if /usr/bin/pgrep -x 'Google Chrome' >/dev/null 2>&1; then
 [ -x /usr/bin/sandbox-exec ] || exit 65
 "$resources/bounded-handoff" /usr/bin/sandbox-exec -f "$resources/handoff.sb" \
  "$chrome_app/Contents/MacOS/Google Chrome" "--profile-directory=$profile" "$url" || exit 66
 /usr/bin/open -a "$chrome_app" || exit 65
else
 # Start the same official application; do not request a new instance.
 /usr/bin/open -a "$chrome_app" --args "--profile-directory=$profile" "$url" || exit 65
fi
