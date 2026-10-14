# Running the preflight on Google Cloud

The preflight runs as a **Cloud Run Job**. **Cloud Scheduler** starts it on Thursday afternoon
for the coming week and again on Sunday evening to catch weekend edits. **Cloud Build** tests
and rebuilds it on every push to `main`. Secrets live in **Secret Manager**, and the VT baseline
lives in a **Cloud Storage** bucket. Claude on **Vertex AI** gives the optional second opinion on each
deck's VT design.

```
GitHub push ──► Cloud Build (pytest → image → Artifact Registry → update job)
Scheduler (Thu 3:47 PM, Sun 5:47 PM ET) ──► Cloud Run Job "weekly-preflight"
      reads: Agenda (Sheets), week folders + decks (Drive), Schoology (REST)
      writes: "Preflight — Wk NN" Doc + "Print packet — Wk NN.pdf" in your output folder
```

The job reads your files as you, with read-only Drive and Sheets scopes. The one extra scope,
`drive.file`, lets it write only files it creates itself: the report and the packet. It never edits
the Agenda, a deck, a folder, or Schoology.

## One-time setup

1. **Google Cloud project.** Use an existing project or create one, with billing enabled.

2. **OAuth client** (so the job can act as your school account).
   In the Cloud console, open APIs & Services > Credentials and choose Create credentials > OAuth client ID > Desktop app.
   Download the JSON as `client_secret.json`. If the school's Workspace limits third-party apps,
   your Workspace admin may need to allow this client ID.

3. **Sign in once, on your computer:**
   ```bash
   pip install -r requirements.txt
   python -m preflight.cli authorize client_secret.json     # opens a browser; writes secrets/token.json
   cp config.example.yaml config.yaml                       # fill in IDs (see below)
   python -m preflight.cli run                               # dry run: prints next week's preflight
   ```

4. **Create the cloud resources:**
   ```bash
   PROJECT_ID=your-project SCHOOLOGY_KEY=... SCHOOLOGY_SECRET=... ./deploy/setup.sh
   gcloud run jobs execute weekly-preflight --region us-east1 --wait
   ```
   You can leave out the Schoology key and add it later; until then the report notes that Schoology was not checked.
   Get the key and secret from your district's Schoology site at `/api`.

5. **Connect GitHub to Cloud Build** so a push to `main` tests and redeploys. In the console,
   open Cloud Build > Repositories, connect the GitHub repository, then run:
   ```bash
   gcloud builds triggers create github --region us-east1 --name preflight-main \
     --repo-owner kvond --repo-name <repo> --branch-pattern '^main$' --build-config cloudbuild.yaml
   ```

## config.yaml

| Key | What to put |
|---|---|
| `agenda_id` | ID of "Agenda 26-27 CLEAN" (from its URL) |
| `output_folder_id` | Drive folder where the weekly Doc and print packet go |
| `gcs_bucket` | the bucket `setup.sh` made (`<project>-preflight`) |
| `courses[].meets`, `friday_side` | weekdays each course meets; A or B Friday |
| `courses[].students`, `groups` | print counts (per student / per lab group) |
| `courses[].sections` | Schoology section IDs |
| `courses[].drive_root` | the folder holding that course's `Week NN - …` folders, so the job can find the real folder when an Agenda link is dead |
| `llm_review`, `vertex_project` | turn on Claude's VT review (Vertex AI → Model Garden → enable Claude first) |

To change config later, run `gcloud secrets versions add preflight-config --data-file config.yaml`.

## Changing the schedule

```bash
gcloud scheduler jobs update http preflight-thursday --location us-east1 --schedule "47 15 * * 4"
```

## Cost

The job runs about twice a week for a minute or two, which fits within Cloud Run's free tier.
Secret Manager, Scheduler and Storage cost cents per month. If `llm_review` is on, Vertex AI bills
for one Claude call per lesson deck per run.
