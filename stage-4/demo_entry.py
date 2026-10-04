"""Public disposable showcase of the approved Tablekeeper Stage 4 implementation.

This entrypoint lives only on the separate demo branch. It starts with two
example restaurants and blocks internal test/reset/import/export operations.
Data is intentionally in-memory and not persistent across restarts.
"""
import copy
import os
from urllib.parse import urlsplit

from app.server import Handler, Server, validate_fixture
from app.state import STORE
from app.validation import ApiError
from tools.stage2_acceptance import FIXTURE


class PublicDemoHandler(Handler):
    def dispatch(self, method):
        if urlsplit(self.path).path.startswith("/_test"):
            raise ApiError(404, "not_found")
        return super().dispatch(method)


def main():
    fixture = copy.deepcopy(FIXTURE)
    fixture["users"] = []
    fixture["reservations"] = []
    bistro = copy.deepcopy(fixture["restaurants"][0])
    bistro.update(
        id="r_bistro",
        name="The Little Bistro",
        tables=[{"id": "b_1", "label": "Blue Booth", "capacity": 4}],
        combinable=[],
    )
    fixture["restaurants"].append(bistro)
    STORE.reset(validate_fixture(fixture))
    port = int(os.environ.get("PORT", "8080"))
    Server(("0.0.0.0", port), PublicDemoHandler).serve_forever()


if __name__ == "__main__":
    main()
