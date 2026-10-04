# Tablekeeper Stage 3

This standalone Python 3.12 service serves the Stage 1–3 JSON API and the inherited same-origin browser experience at `/`, `/signup`, `/login`, and `/lookup`. Assets, IANA timezone data, and ephemeral state are packaged locally; no runtime network, database, or mount is required.

Build and run from this directory:

```sh
docker build --no-cache -t tablekeeper-stage3-final .
docker run --rm --name tablekeeper-stage3-final --cpus 2 --memory 2g -e PORT=8080 -p 18200:8080 tablekeeper-stage3-final
```

`GET /health` returns `{"status":"ok"}` when ready. With the accepted Stage 1 and Stage 2 services on ports 18081 and 18101, the focused gates are:

```sh
python -m unittest discover -s tests -v
python tools/stage3_acceptance.py --base-url http://127.0.0.1:18200 --stage2-url http://127.0.0.1:18101
python tools/stage3_concurrency.py --base-url http://127.0.0.1:18200
python tools/stage3_migration.py --stage1-url http://127.0.0.1:18081 --stage2-url http://127.0.0.1:18101 --destination http://127.0.0.1:18200
```

The runners exit nonzero on failure and keep credential-bearing exports in memory.
