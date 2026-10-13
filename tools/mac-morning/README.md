# School Morning for the existing Mac

`build_app.py` creates a new double-clickable **School Morning.app**. It opens
the existing Codex application and the explicitly verified school Chrome
profile, then visits the installed helper's `morning-setup.html` page. That
page opens only missing Hub and Infinite Campus tabs. PDF previews do not count
as dashboards. Real duplicate dashboards or IC tabs remain open, with their
actual counts and a request to check unsaved work before closing extras.

The app does not start grading, toggle the weekday opt-in, clear recovery,
change helper storage, sign in, open another AI app, start Terminal or create
another keep-awake process. The existing DeskCommander background service and
keep-awake arrangement remain independent. Chrome alarms still require the Mac
to be awake and the correct school profile to be open and signed in.

Build with explicit local arguments after the exact account/profile and installed
helper have been checked. Never put the actual school account, origins, extension
ID, profile metadata or generated app in the repository:

```sh
python3 tools/mac-morning/build_app.py \
  --output /a/new/local/School\ Morning.app \
  --profile 'Profile 77' \
  --extension-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --local-state '/an/existing/Chrome/Local State' \
  --school-account teacher@school.example.test
```

The example is synthetic. The builder checks only the selected profile's exact
`user_name` metadata against the supplied account, its existing profile directory
and the official Chrome application. It does not copy a browser profile or
persist the supplied account in the app. Existing output paths and symlinks are
refused. The local compiler builds the two small native executables; nothing is
downloaded or installed. App construction does not launch it.

Double-click the new app after the helper package containing the setup page is
loaded. Check the Hub and IC tabs for a sign-in request. The setup page confirms
tab counts, not authentication or a completed grade run. A later click reuses
the oldest setup page; revisiting that page opens any missing school tab. A
Desktop Finder alias or symlink may point to the app after confirming the path
does not already exist.

When Chrome is already running, its exact executable receives the selected
profile and page through a thirty-second bounded command. That command is inside
the built-in Mac sandbox with signals to outside processes denied and writes to
the three exact canonical singleton paths denied. It can neither kill the
existing browser nor take over its singleton files. An unsuccessful handoff
shows a native message and does not retry without the restriction. Only the
newly spawned, still-unreaped command PID may be stopped. Cold startup uses the
existing Chrome application through LaunchServices without requesting another
instance. The restriction is necessary because Chrome's ordinary singleton
failure path can terminate an unresponsive browser. The relevant implementation
is Chromium's
[`process_singleton_posix.cc`](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/browser/process_singleton_posix.cc).

For local diagnosis, the launcher writes only a controlled stage token and
numeric exit results to `~/Library/Logs/School Morning/last-launch.private.json`.
The folder is private and the file has mode 0600. It contains no command output,
arguments, sites, profile configuration or sign-in data. Diagnostic failure does
not relax the browser guard. The native alert keeps the teacher's next action
plain; the status file is for a worker diagnosing a failed launch.
An explicitly authorized worker may use `bounded-handoff --capture-stderr`
with a private, size-limited stderr capture to diagnose the exact guarded child.
The ordinary app suppresses command output. This option does not alter the
sandbox or allow an unrestricted Chrome invocation.

Synthetic verification:

```sh
python3 -m unittest discover -s tools/mac-morning -p 'test_*.py'
node --test browser-extension/tests/morning-setup.test.cjs
```

These checks use synthetic profiles, tabs, files and disposable processes.
Normal school-profile launch, helper reload, live tab reuse and rendering must
be checked separately by the worker who owns the browser. Do not remove a Mac
security warning or grant new Automation permissions to make the app run.
