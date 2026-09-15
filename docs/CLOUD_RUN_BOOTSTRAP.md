# First Cloud Run deployment: preview only

The teacher authorized Google Cloud hosting in the frontend chat on September
15, 2026. This adds a web entry point to the existing Python repository, not a
second backend. Other workflow/research work remains owned by its existing chat.

## What ships

- Root `Dockerfile`: Python 3.12, hash-pinned Gunicorn, non-root runtime, PORT
  binding, one worker, four threads. Request access logging is disabled.
- `school_workflow_hub.web:application`: serves the existing preview assets from
  an exact allowlist. No directory listing, arbitrary file serving or input echo.
- `web/`: unchanged frontend snapshot; SHA-256 provenance is recorded in
  `FRONTEND_PREVIEW_SHA256.json`. Research presentation is copied unchanged.
- `/healthz`: process health only. `/api/readiness`: all live capabilities false.
- Unknown API/auth routes return 503; write methods return 405. No school input,
  credential capture, model calls, schedules, database creation or workflows run.

## Cloud Run form

Use the existing private repository, branch `^main$`, Dockerfile `/Dockerfile`.
Service name `school-workflow-hub`; region `us-central1`; request-based billing;
minimum instances 0, service maximum instances 1; 1 CPU, 512 MiB memory.
These limits reduce consumption but do not enforce a dollar spending cap.

Select **Require authentication**, with **IAM** for the initial hosting check.
Do not grant `allUsers` or `allAuthenticatedUsers` invocation rights. A normal
browser may show 403 even to a signed-in Google user: Cloud Run IAM requires an
invocation token, not merely a Google browser session. Browser teacher sign-in
will require a subsequent verified IAP or application-login integration.

The existing OAuth client is not used in this first deployment. Do not place its
secret in build arguments, source, browser assets or the container image. No
environment variables or secrets beyond Cloud Run's PORT are required yet.

Cloud Build repository integration deploys matching pushes automatically once
the service is created. This is separate from the existing disabled GitHub
Actions fixture workflow; neither its enable variable nor its schedules change.
Review matching branch pushes accordingly. Configure artifact retention later
to avoid indefinite image accumulation.

## Acceptance after deployment

1. Record deployed commit, completed build and service revision. Do not treat
   a local test as a successful container build or hosted run.
2. Request the actual HTTPS `/healthz` URL without credentials: expect rejection.
3. From an authorized Cloud Shell, use `gcloud run services proxy` for this
   service/region and preview its forwarded port. Inspect dashboard assets and
   `/api/readiness`; all live capability flags must remain false. Tokens must
   not be pasted in chat, query strings, screenshots or application logs.
4. Verify an unauthorized identity cannot invoke the service. Do not enable
   public access to work around a 403.

Cloud Run platform request logging is separate from Gunicorn logging. Before
adding OAuth callbacks, configure and verify redaction/exclusion of callback
query parameters there; they may carry sensitive authorization codes.

## Local verification, September 15

The published backend baseline plus the seven hosting tests passed 329 tests.
The unchanged frontend passed its 30 Node tests. Gunicorn started locally on a
non-default PORT; HTTP checks passed for assets, health, readiness, missing/private
paths and rejected writes. Headless Chrome passed draft staging, incomplete
submission evidence, summary navigation, mobile width and runtime error checks.
The browser harness needed to wait for the summary route before clicking its
close-demo button; no frontend source changes were made.

Docker is not installed in the authoring environment, so the image itself has
not been built locally. The first Cloud Build is still required to verify that
layer and Cloud Run startup. No hosted authentication or school access has been
verified. Uncommitted workflow/research work in the other checkout was not added
to this deployment commit.

## Remaining before actual school use

Teacher browser login/allowlist; backend Google OAuth consent, refresh and private
secret storage; durable private state compatible with existing ledgers and run
claims; bounded real-source pilot; live contract transport; per-operation save
and execution verification. Local SQLite files cannot be the durable Cloud Run
store. No OAuth callback URL is operational yet.

References:
- https://docs.cloud.google.com/run/docs/quickstarts/build-and-deploy/deploy-python-service
- https://docs.cloud.google.com/run/docs/authenticating/developers
- https://docs.cloud.google.com/run/docs/container-contract
