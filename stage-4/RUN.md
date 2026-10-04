# Tablekeeper Stage 4

This standalone Python 3.12 service implements Stage 1–4 JSON endpoints and the final polished same-origin browser at `/`, `/signup`, `/login` and `/lookup`. Assets and IANA timezone data are packaged locally. State is **in-memory only**, so a fresh container has no restaurants until initialized.

## Clean build and run

From this `stage-4/` directory:

```sh
docker build --no-cache -t tablekeeper-stage4-final .
docker run --rm --name tablekeeper-stage4-final --cpus 2 --memory 2g -e PORT=8080 -p 18300:8080 tablekeeper-stage4-final
```

Visit `http://localhost:18300` and `http://localhost:18300/health` (expected `{"status":"ok"}`). A fresh container showing **No restaurants yet** is the designed unseeded state, not an API error.

## Optional populated reviewer demo

Run this **only against a new disposable container**, because `/_test/reset` REPLACES ALL current restaurant, user, token and reservation state. From this stage directory in a second terminal (Python 3.12+):

```sh
python -c 'import copy,json,urllib.request; from tools.stage2_acceptance import FIXTURE; f=copy.deepcopy(FIXTURE); b=copy.deepcopy(f["restaurants"][0]); b.update(id="r_bistro", name="The Little Bistro", tables=[{"id":"b_1","label":"Blue Booth","capacity":4}], combinable=[]); f["restaurants"].append(b); data=json.dumps(f).encode(); req=urllib.request.Request("http://127.0.0.1:18300/_test/reset",data=data,headers={"Content-Type":"application/json"},method="POST"); print("Fixture reset HTTP",urllib.request.urlopen(req).status)'
```

Expect HTTP **204**, two restaurants and four tables. Refresh the web app; then search → select seating → book → log in → My Reservations → cancel. This uses an approved shipped fixture, not a permanent data store or a production onboarding mechanism. Existing reservations are NOT preserved by reset.

## Focused tests

With earlier stage instances running on 18081, 18101 and 18200 as needed, use:

```sh
python -m unittest discover -s tests -v
python tools/stage4_acceptance.py --base-url http://127.0.0.1:18300 --stage3-url http://127.0.0.1:18200
python tools/stage4_concurrency.py --base-url http://127.0.0.1:18300
python tools/stage4_migration.py --stage1-url http://127.0.0.1:18081 --stage2-url http://127.0.0.1:18101 --stage3-url http://127.0.0.1:18200 --destination http://127.0.0.1:18300
```

The tests mutate the test instance: never run them on a session containing data you want to keep. Shipped tests are not the organizers' private graded suite.
