# Tablekeeper Stage 1

From this directory, build and start the standalone service:

```sh
docker build --no-cache -t tablekeeper-stage1 .
docker run --rm --name tablekeeper-stage1 --cpus 2 --memory 2g -e PORT=8080 -p 18080:8080 tablekeeper-stage1
```

`PORT` defaults to 8080. The image contains Python and IANA timezone data; it needs no runtime network access, mounts, database, or setup command. `GET /health` returns `{"status":"ok"}` when ready. State is intentionally in memory and disappears on container restart.

For reproducible checks, start two containers in separate terminals or detached:

```sh
docker run --rm -d --name tk-s1-source --cpus 2 --memory 2g -e PORT=8080 -p 18080:8080 tablekeeper-stage1
docker run --rm -d --name tk-s1-dest --cpus 2 --memory 2g -e PORT=9090 -p 18081:9090 tablekeeper-stage1
python tools/acceptance.py --base-url http://127.0.0.1:18080 --destination-url http://127.0.0.1:18081
```

The runner resets state, exercises bookings, retries, swaps, concurrency, DST, and cross-container import. It exits nonzero on a failed assertion. It keeps credential-bearing exports only in memory.

To verify operation with no outbound network:

```sh
docker run --rm -d --name tk-s1-offline --network none --cpus 2 --memory 2g tablekeeper-stage1
docker exec tk-s1-offline python tools/acceptance.py --base-url http://127.0.0.1:8080
```

Local unit checks: `python -m unittest discover -s tests -v`. On Windows, install `tzdata` in the host Python environment for these checks; the Docker image already includes it.
