"""Focused real-browser Stage 2 flow/recovery checks and responsive screenshots."""

import argparse
import asyncio
import copy
import json
from pathlib import Path

from playwright.async_api import async_playwright, expect as browser_expect
from stage2_acceptance import FIXTURE, expect, request


def browser_fixture():
    fixture = copy.deepcopy(FIXTURE)
    other = copy.deepcopy(fixture["restaurants"][0])
    other.update(id="r_bistro", name="The Little Bistro",
                 tables=[{"id": "b_1", "label": "Blue Booth", "capacity": 4}], combinable=[])
    fixture["restaurants"].append(other)
    return fixture


async def signed_page(browser, base, token, display="Ada", viewport=None):
    context = await browser.new_context(viewport=viewport or {"width": 1280, "height": 850})
    value = json.dumps({"token": token, "display_name": display, "user_id": "u_ada"})
    await context.add_init_script(f"localStorage.setItem('tablekeeper-session', JSON.stringify({value}));")
    page = await context.new_page()
    await page.goto(base + "/")
    await page.locator('[data-testid="restaurant-select"] option').first.wait_for(state="attached")
    return context, page


async def search(page, restaurant="r_arden", size="4"):
    await page.get_by_test_id("restaurant-select").select_option(restaurant)
    await page.get_by_test_id("date-input").fill("2035-01-01")
    await page.get_by_test_id("party-size-input").fill(size)
    await page.get_by_test_id("search-button").click()
    await page.get_by_test_id("slot-" + ("b_1" if restaurant == "r_bistro" else "t_3") + "-18:00").wait_for()


async def run(base, stage1, out):
    out.mkdir(parents=True, exist_ok=True)
    checks = 0
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            expect(request(base, "POST", "/_test/reset", browser_fixture()), 204)
            context = await browser.new_context(viewport={"width": 1280, "height": 850})
            page = await context.new_page()
            await page.goto(base + "/signup")
            await page.get_by_test_id("signup-display-name").fill("Mira")
            await page.get_by_test_id("signup-email").fill("mira@example.com")
            await page.get_by_test_id("signup-password").fill("strong password")
            await page.get_by_test_id("signup-submit").click()
            await page.get_by_test_id("current-user").wait_for()
            assert "Mira" in await page.get_by_test_id("current-user").inner_text()
            checks += 1
            await page.get_by_test_id("logout-button").click()
            await page.goto(base + "/login")
            await page.get_by_test_id("login-email").fill("mira@example.com")
            await page.get_by_test_id("login-password").fill("strong password")
            await page.get_by_test_id("login-submit").click()
            await page.get_by_test_id("current-user").wait_for()
            assert "Mira" in await page.get_by_test_id("current-user").inner_text()
            checks += 1
            await search(page)
            assert await page.get_by_test_id("slot-t_1-18:00").get_attribute("data-available") == "false"
            pair = page.get_by_test_id("slot-t_2+t_1-18:00")
            assert await pair.get_attribute("data-available") == "true"
            await pair.click()
            summary = await page.get_by_test_id("booking-summary").inner_text()
            assert "Garden" in summary and "Window" in summary and "18:00" in summary
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            await page.screenshot(path=str(out / "desktop-search-booking.png"), full_page=True)
            await page.get_by_test_id("booking-submit").click()
            await page.get_by_test_id("confirmation").wait_for(state="visible")
            reference = await page.get_by_test_id("confirmation-reference").inner_text()
            assert len(reference) >= 6 and (await page.get_by_test_id("confirmation-tables").inner_text()).count("Garden") == 1
            await page.get_by_test_id("booking-submit").click()
            await page.get_by_test_id("confirmation").wait_for(state="visible")
            assert await page.get_by_test_id("confirmation-reference").inner_text() == reference
            checks += 3
            await page.goto(base + "/lookup")
            await page.get_by_test_id("lookup-reference-input").fill(reference)
            await page.get_by_test_id("lookup-submit").click()
            await page.get_by_test_id("reservation-detail").wait_for(state="visible")
            assert await page.get_by_test_id("reservation-status").inner_text() == "confirmed"
            labels = await page.get_by_test_id("reservation-tables").inner_text()
            assert "Garden" in labels and "Window" in labels
            await page.get_by_test_id("reservation-cancel-button").click()
            await browser_expect(page.get_by_test_id("reservation-status")).to_have_text("cancelled")
            assert await page.get_by_test_id("reservation-status").inner_text() == "cancelled"
            assert not await page.get_by_test_id("reservation-cancel-button").is_visible()
            checks += 2
            await context.close()

            # Search A is delayed and fails after B's grid and booking form render.
            expect(request(base, "POST", "/_test/reset", browser_fixture()), 204)
            token = expect(request(base, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"}), 200)["token"]
            context, page = await signed_page(browser, base, token)
            release_a = asyncio.Event()
            saw_a = asyncio.Event()
            async def delay_a(route):
                if "restaurant_id=r_arden" in route.request.url:
                    saw_a.set()
                    await release_a.wait()
                    await route.fulfill(status=503, content_type="application/json", body='{"error":{"code":"late_error","message":"late"}}')
                else:
                    await route.continue_()
            await page.route("**/availability?*", delay_a)
            await page.get_by_test_id("restaurant-select").select_option("r_arden")
            await page.get_by_test_id("date-input").fill("2035-01-01")
            await page.get_by_test_id("party-size-input").fill("4")
            await page.get_by_test_id("search-button").click()
            await asyncio.wait_for(saw_a.wait(), 5)
            await search(page, "r_bistro")
            await page.get_by_test_id("slot-b_1-18:00").click()
            release_a.set()
            await asyncio.sleep(.3)
            assert "The Little Bistro" in await page.get_by_test_id("booking-summary").inner_text()
            assert "Blue Booth" in await page.get_by_test_id("booking-summary").inner_text()
            assert await page.get_by_test_id("slot-b_1-18:00").is_visible()
            assert not await page.get_by_test_id("slot-t_3-18:00").count()
            checks += 1
            await context.close()

            # A committed pair loses its HTTP response, then the unchanged form replays it.
            expect(request(base, "POST", "/_test/reset", browser_fixture()), 204)
            token = expect(request(base, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"}), 200)["token"]
            context, page = await signed_page(browser, base, token)
            await search(page)
            await page.get_by_test_id("slot-t_2+t_1-18:00").click()
            attempts = []
            committed = []
            async def lose_first(route):
                attempts.append((route.request.post_data_json, route.request.headers["idempotency-key"]))
                if len(attempts) == 1:
                    response = await route.fetch()
                    assert response.status == 201
                    committed.append(await response.json())
                    await route.abort("failed")
                else:
                    await route.continue_()
            await page.route("**/reservations", lose_first)
            await page.get_by_test_id("booking-submit").click()
            await page.get_by_test_id("booking-uncertain").wait_for()
            assert (await page.get_by_test_id("booking-uncertain").inner_text()).strip()
            assert not await page.get_by_test_id("confirmation").is_visible()
            assert await page.get_by_test_id("booking-error").count() == 0
            await page.get_by_test_id("booking-submit").click()
            await page.get_by_test_id("confirmation").wait_for(state="visible")
            assert await page.get_by_test_id("confirmation-reference").inner_text() == committed[0]["reference"]
            assert attempts[0] == attempts[1]
            assert await page.get_by_test_id("booking-uncertain").count() == 0
            checks += 2
            await context.close()

            # Confirmed conflict refreshes availability without discarding form inputs.
            expect(request(base, "POST", "/_test/reset", browser_fixture()), 204)
            token = expect(request(base, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"}), 200)["token"]
            context, page = await signed_page(browser, base, token)
            await search(page, size="2")
            await page.get_by_test_id("slot-t_1-18:00").click()
            before = await page.get_by_test_id("booking-summary").inner_text()
            body = {"restaurant_id": "r_arden", "table_id": "t_1", "starts_at_local": "2035-01-01T18:00", "party_size": 2}
            expect(request(base, "POST", "/reservations", body, token, "other-client"), 201)
            await page.get_by_test_id("booking-submit").click()
            await page.get_by_test_id("booking-error").wait_for()
            assert await page.get_by_test_id("booking-uncertain").count() == 0
            assert await page.get_by_test_id("booking-summary").inner_text() == before
            assert await page.get_by_test_id("booking-party-size").input_value() == "2"
            assert await page.get_by_test_id("booking-form").is_visible()
            assert not await page.get_by_test_id("confirmation").is_visible()
            await page.wait_for_function("document.querySelector('[data-testid=\"slot-t_1-18:00\"]')?.dataset.available === 'false'")
            checks += 1
            await context.close()

            # Legacy service commits a single-table request and loses the response.
            # Import happens between browser requests; token/form/key/body must survive.
            legacy = browser_fixture()
            for restaurant in legacy["restaurants"]:
                restaurant.pop("combinable", None)
            expect(request(stage1, "POST", "/_test/reset", legacy), 204)
            token = expect(request(stage1, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"}), 200)["token"]
            old_export = expect(request(stage1, "GET", "/_test/export"), 200)
            expect(request(base, "POST", "/_test/import", old_export), 204)
            context, page = await signed_page(browser, base, token)
            await search(page, size="2")
            await page.get_by_test_id("slot-t_1-18:00").click()
            old_attempts = []
            old_committed = []
            async def old_loss(route):
                old_attempts.append((route.request.post_data_json, route.request.headers["idempotency-key"]))
                if len(old_attempts) == 1:
                    response = await route.fetch(url=stage1 + "/reservations")
                    assert response.status == 201
                    old_committed.append(await response.json())
                    await route.abort("failed")
                else:
                    await route.continue_()
            await page.route("**/reservations", old_loss)
            await page.get_by_test_id("booking-submit").click()
            await page.get_by_test_id("booking-uncertain").wait_for()
            upgraded_export = expect(request(stage1, "GET", "/_test/export"), 200)
            expect(request(base, "POST", "/_test/import", upgraded_export), 204)
            await page.get_by_test_id("booking-submit").click()
            await page.get_by_test_id("confirmation").wait_for(state="visible")
            assert await page.get_by_test_id("confirmation-reference").inner_text() == old_committed[0]["reference"]
            assert old_attempts[0] == old_attempts[1]
            assert await page.get_by_test_id("current-user").is_visible()
            checks += 1
            await context.close()

            expect(request(base, "POST", "/_test/reset", browser_fixture()), 204)
            token = expect(request(base, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"}), 200)["token"]
            context, page = await signed_page(browser, base, token, viewport={"width": 375, "height": 812})
            await search(page)
            await page.get_by_test_id("slot-t_2+t_1-18:00").click()
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            await page.screenshot(path=str(out / "mobile-375-search-booking.png"), full_page=True)
            checks += 1
            await context.close()
        finally:
            await browser.close()
    return {"checks": checks, "screenshots": ["desktop-search-booking.png", "mobile-375-search-booking.png"],
            "late_search_guard": True, "lost_pair_response_recovered": True,
            "legacy_upgrade_recovered_without_reload": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--stage1-url", required=True)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[3] / "checks" / "s2-builder-01")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.base_url.rstrip("/"), args.stage1_url.rstrip("/"), args.out)), sort_keys=True))
