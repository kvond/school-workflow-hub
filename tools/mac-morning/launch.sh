#!/bin/sh
set -eu
diagnostic_dir="${SCHOOL_MORNING_LOG_DIR:-${HOME}/Library/Logs/School Morning}"
case "$diagnostic_dir" in /*) ;; *) diagnostic_dir= ;; esac
stage=configuration
command_result=null
finish() {
 result=$1
 trap - 0
 # Only controlled stage tokens and numeric results are recorded. Never log
 # command output, arguments, sites, profile configuration or sign-in data.
 if [ -n "$diagnostic_dir" ] && [ ! -L "$diagnostic_dir" ] &&
    /bin/mkdir -p "$diagnostic_dir" && /bin/chmod 700 "$diagnostic_dir"; then
  temporary="$diagnostic_dir/.launch-status-$$.tmp"
  if (umask 077; set -C; printf '{"schema":"school-morning-launch-status/v1","stage":"%s","exit_code":%s,"command_exit_code":%s}\n' \
       "$stage" "$result" "$command_result" > "$temporary"); then
   /bin/mv -f "$temporary" "$diagnostic_dir/last-launch.private.json" || true
  fi
 fi
 exit "$result"
}
trap 'finish "$?"' 0
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
stage=codex_open
if /usr/bin/open -b com.openai.codex; then command_result=0; else command_result=$?; exit 65; fi
url="chrome-extension://$extension_id/morning-setup.html"
# Process names only. No command lines, profile contents or login data.
if /usr/bin/pgrep -x 'Google Chrome' >/dev/null 2>&1; then
 stage=chrome_handoff
 [ -x /usr/bin/sandbox-exec ] || exit 65
 if "$resources/bounded-handoff" /usr/bin/sandbox-exec -f "$resources/handoff.sb" \
  "$chrome_app/Contents/MacOS/Google Chrome" "--profile-directory=$profile" "$url"; then
  command_result=0
 else command_result=$?; exit 66; fi
 stage=chrome_focus
 if /usr/bin/open -a "$chrome_app"; then command_result=0; else command_result=$?; exit 65; fi
else
 stage=chrome_start
 # Start the same official application; do not request a new instance.
 if /usr/bin/open -a "$chrome_app" --args "--profile-directory=$profile" "$url"; then
  command_result=0
 else command_result=$?; exit 65; fi
fi
stage=complete
