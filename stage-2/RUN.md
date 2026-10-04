# Tablekeeper Stage 2

This standalone Python 3.12 service serves both the Stage 1/2 JSON API and the same-origin browser experience at `/`, `/signup`, `/login`, and `/lookup`. Assets, IANA timezone data, and state storage are packaged locally; no runtime network, database, mounts, or external UI build is required. State is intentionally in memory and disappears when the container stops.

Build and run from this directory:

```sh
docker build --no-cache -t tablekeeper-stage2 .
docker run --rm --name tablekeeper-stage2 --cpus 2 --memory 2g -e PORT=8080 -p 18100:8080 tablekeeper-stage2
```

`GET /health` returns `{"status":"ok"}` when ready. To run the focused Stage 2 API/browser checks, start the accepted Stage 1 image separately on port 18101 and Stage 2 on 18100 (detached examples):

```sh
docker run --rm -d --name tk-s2-test --cpus 2 --memory 2g -e PORT=8080 -p 18100:8080 tablekeeper-stage2
docker run --rm -d --name tk-s1-test --cpus 2 --memory 2g -e PORT=8080 -p 18101:8080 tablekeeper-stage1
python -m unittest discover -s tests -v
python tools/stage2_acceptance.py --base-url http://127.0.0.1:18100 --stage1-url http://127.0.0.1:18101
python tools/stage2_ui_acceptance.py --base-url http://127.0.0.1:18100 --stage1-url http://127.0.0.1:18101
```

The browser runner uses Python Playwright plus installed Chromium as a test-only dependency (`python -m pip install playwright` and `python -m playwright install chromium`). It writes 375px and desktop screenshots to `D:\HACKATHON\band-work\checks\s2-builder-01` by default, or `--out <directory>`. Neither Playwright nor a browser is installed in the service image. The runners exit nonzero on failure and keep credential-bearing exports in memory.
