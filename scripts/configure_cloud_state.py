#!/usr/bin/env python3
"""Prepare the existing service's private Firestore store from Cloud Shell.

Dry-run by default. No credential output, downloaded service-account keys, public
access changes, student inputs, new schedules, or teaching write activation.
"""
import argparse
import json
import re
import subprocess
from urllib.parse import urlsplit


def call(args, step):
    result = subprocess.run(["gcloud", *args, "--quiet", "--format=json"],
                            text=True, capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError("Cloud setup failed at " + step + "; inspect that operation in Cloud Console.")
    return json.loads(result.stdout or "{}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--service", required=True)
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--configure-service", action="store_true")
    args = parser.parse_args()
    for value in (args.project, args.region, args.service):
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,62}", value):
            parser.error("Invalid resource identifier")
    origin = urlsplit(args.public_origin)
    if (origin.scheme != "https" or not (origin.hostname or "").endswith(".run.app")
            or origin.path or origin.query or origin.fragment or origin.username or origin.port):
        parser.error("Use the existing service's HTTPS run.app origin, without a path")
    if not args.apply:
        print("Plan: enable Firestore; reuse/create the default Standard Native database in " + args.region)
        print("Enable database delete protection; give the existing runtime identity database-scoped data access.")
        print("Disable indexing of the hub's opaque payload field. No teaching actions are enabled.")
        if args.configure_service:
            print("Configure Firestore variables and allowed probe origins on the existing Cloud Run service.")
        return
    project = "--project=" + args.project
    run_scope = [args.service, project, "--region=" + args.region]
    service = call(["run", "services", "describe", *run_scope], "service inspection")
    email = service["spec"]["template"]["spec"]["serviceAccountName"]
    if not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.gserviceaccount\.com", email):
        raise RuntimeError("The service has no explicit runtime identity")
    call(["services", "enable", "firestore.googleapis.com", project], "API enablement")
    print("Firestore API enabled", flush=True)
    databases = call(["firestore", "databases", "list", project], "database inspection")
    if isinstance(databases, dict):
        databases = databases.get("databases", [])
    database = next((x for x in databases if x["name"].endswith("/databases/(default)")), None)
    if database:
        if database.get("type") != "FIRESTORE_NATIVE" or database.get("locationId") != args.region:
            raise RuntimeError("Existing default database has a different mode or location; no database was replaced")
        call(["firestore", "databases", "update", "--database=(default)", "--delete-protection", project],
             "database protection")
    else:
        call(["firestore", "databases", "create", "--database=(default)", "--edition=standard",
              "--location="+args.region, "--type=firestore-native", "--delete-protection", project],
             "database creation")
    print("Default Native database ready with delete protection", flush=True)
    condition = ('expression=resource.name=="projects/' + args.project + '/databases/(default)",'
                 'title=hub-default-database,description=Hub runtime access to its default state database')
    call(["projects", "add-iam-policy-binding", args.project, "--member=serviceAccount:"+email,
          "--role=roles/datastore.user", "--condition="+condition], "runtime database permission")
    call(["firestore", "indexes", "fields", "update", "payload", "--collection-group=hub_state_v1",
          "--database=(default)", "--disable-indexes", project], "payload index exemption")
    print("Runtime permission and payload index exemption ready", flush=True)
    if args.configure_service:
        canonical = service["status"]["url"]
        origins = ",".join(sorted({canonical, args.public_origin}))
        values = {"HUB_STATE_BACKEND": "firestore", "HUB_STATE_PROJECT": args.project,
                  "HUB_STATE_DATABASE": "(default)", "HUB_STATE_SERVICE_ACCOUNT": email,
                  "HUB_STATE_PROBE_ORIGINS": origins}
        encoded = "^|^" + "|".join(k+"="+v for k, v in values.items())
        call(["run", "services", "update", *run_scope, "--update-env-vars="+encoded], "service configuration")
        print("Service configured. Signed-in storage acceptance and restart checks are still required.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError) as error:
        raise SystemExit(str(error)) from None
