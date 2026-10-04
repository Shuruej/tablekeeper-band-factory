# Tablekeeper Factory — submission guide

This repository contains four independently runnable Tablekeeper stages, five generic agent-seat mandates, and a factory record. The implementation was produced and reviewed in a Band room. The room's genuine full-session export must be included at the root as `room.json`; the release must not claim provenance verification before that export is checked.

## Repository structure

- `stage-1/` to `stage-4/`: independent Docker applications, each with `Dockerfile`, `RUN.md`, and test tools.
- `mandates/`: generic Architect, Builder, Red Team QA, Verifier, and Commander instructions including harness and model.
- `FACTORY.md`: ownership, collaboration, tradeoffs, failure/recovery evidence and observed costs.
- `room.json`: unmodified complete Band **New Session** room export; obtained manually, not synthesized.

## Start the final Stage 4 app

Requires Docker and a free local port. From `stage-4/`:

```sh
docker build --no-cache -t tablekeeper-stage4-final .
docker run --rm --name tablekeeper-stage4-final --cpus 2 --memory 2g -e PORT=8080 -p 18300:8080 tablekeeper-stage4-final
```

Open `http://localhost:18300`, and verify `http://localhost:18300/health` returns `{"status":"ok"}`. **An empty restaurant list on a clean start is expected:** the application intentionally stores state in memory and begins without restaurant fixtures. To review a populated demonstration, use the optional fixture step below on a **fresh disposable container only**.

## Populate a fresh LOCAL DEMO (optional, destructive reset)

Requires Python 3.12+. From `stage-4/`, in a separate terminal while the container above is running, execute the code below. It uses the shipped approved Stage 2 UI fixture data rather than editing application code. **Never run against an existing booking session**: `/_test/reset` replaces the entire state including users, tokens and reservations. This fixture endpoint is for testing, not a production restaurant onboarding facility.

```sh
python -c 'import copy,json,urllib.request; from tools.stage2_acceptance import FIXTURE; f=copy.deepcopy(FIXTURE); b=copy.deepcopy(f["restaurants"][0]); b.update(id="r_bistro", name="The Little Bistro", tables=[{"id":"b_1","label":"Blue Booth","capacity":4}], combinable=[]); f["restaurants"].append(b); data=json.dumps(f).encode(); req=urllib.request.Request("http://127.0.0.1:18300/_test/reset",data=data,headers={"Content-Type":"application/json"},method="POST"); print("Fixture reset HTTP",urllib.request.urlopen(req).status)'
```

Expect HTTP 204 and two selectable restaurants, with four tables total. Refresh the browser, sign in or create a new account, then try search → available seating → booking → My Reservations → cancel. The preloaded fixture user credentials are in the shipped `stage-4/tools/stage2_acceptance.py`; use test accounts only. To reset without losing any real data, stop this disposable container and start a new one.

## Stage-by-stage testing

Follow each `stage-N/RUN.md` on a fresh container. From the separate organizer kickoff repository, after installing the documented Python/Playwright prerequisites:

```sh
python -m harness check /path/to/submission-repository --track tablekeeper
python -m harness run --track tablekeeper --repo /path/to/submission-repository --all --mode isolated --out /path/to/unique-empty-evidence-directory
```

On Windows, the official participant guide specifies WSL2 for isolated runs. The `--out` directory must be new and absent before each run. Judges also run private checks not available to entrants; shipped test success does not guarantee private-suite success.

## Evidence and trust checks

Export the **complete** original `New Session` room from Band Desktop → **Open in Band** → console room menu **Download → Download full session**. Save at repository root as `room.json`. The export may contain private data or credentials. Inspect before any public push; if credentials appear, rotate them and follow the participant guide's `[REDACTED]` redaction procedure for only the exposed secret. Do not synthesize or omit room events.

Verify generic mandates, reciprocal agent handoffs, git provenance and secrets before publishing. Publish the public repository together with a presentation and an actual screen recording of the working Band room and its handoffs; no claim of full autonomy is made for a run with human steering.

**Local release note:** This documentation version adds a reproducible demo setup. Stage application source, tests, and mandates are unchanged from the approved polished build.
