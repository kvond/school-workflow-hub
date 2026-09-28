#!/usr/bin/env python3
"""Package exact, explicit origins. Standard library only; no remote dependencies."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

FILES = ("background.js", "protocol.js", "plan.js", "executor.js", "hub-content.js", "ic-content.js",
         "ic-adapter.js", "entry.js", "hub-entry.js", "ic-entry.js", "popup.html", "popup.css", "popup.js")


def origin(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use an exact HTTPS origin.") from error
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or port is not None
            or not parsed.hostname or value != "https://" + parsed.hostname
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", parsed.hostname)
            or "." not in parsed.hostname or ".." in parsed.hostname):
        raise argparse.ArgumentTypeError("Use an exact HTTPS origin without path, port, or credentials.")
    return value


def package(hub_origin, ic_origin, output):
    hub_origin, ic_origin = origin(hub_origin), origin(ic_origin)
    if hub_origin == ic_origin:
        raise ValueError("Dashboard and Infinite Campus origins must differ.")
    source = Path(__file__).resolve().parent
    output = Path(output).resolve()
    archive = output.with_name(output.name + ".zip")
    # Never silently replace a package, source code, or another directory.
    if output.exists() or archive.exists():
        raise ValueError("Output directory and adjacent ZIP must not already exist.")
    if source == output or source in output.parents:
        raise ValueError("Write the package outside the extension source directory.")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest["host_permissions"] = [hub_origin + "/*", ic_origin + "/*"]
    manifest["content_scripts"] = [
        {"matches": [hub_origin + "/*"], "js": ["config.js", "protocol.js", "entry.js", "hub-content.js", "hub-entry.js"],
         "run_at": "document_idle"},
        {"matches": [ic_origin + "/*"], "js": ["config.js", "protocol.js", "plan.js", "executor.js", "ic-adapter.js",
                                              "ic-content.js", "entry.js", "ic-entry.js"], "run_at": "document_idle"},
    ]
    config = {"HUB_ORIGIN": hub_origin, "IC_ORIGIN": ic_origin}
    contents = {name: (source / name).read_bytes() for name in FILES}
    contents["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    contents["config.js"] = ("globalThis.HubPostingConfig=Object.freeze(" + json.dumps(config, sort_keys=True) + ");\n").encode()
    output.mkdir(parents=True)
    for name, data in contents.items():
        (output / name).write_bytes(data)
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zipped:
        for name in sorted(contents):
            entry = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            zipped.writestr(entry, contents[name])
    return output, archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub-origin", required=True, type=origin)
    parser.add_argument("--ic-origin", required=True, type=origin)
    parser.add_argument("--output", required=True, type=Path,
                        help="New unpacked directory; ZIP is written alongside it.")
    args = parser.parse_args()
    try:
        folder, archive = package(args.hub_origin, args.ic_origin, args.output)
    except ValueError as error:
        parser.error(str(error))
    print("Unpacked extension: " + str(folder))
    print("ZIP: " + str(archive))
    print("Install only in the authorized school profile. Live acceptance remains required.")


if __name__ == "__main__":
    main()
