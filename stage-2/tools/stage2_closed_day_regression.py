"""Closed-day DOM/testid lifecycle regression using the official combined selector."""
import argparse
import asyncio
import copy
import json
from playwright.async_api import async_playwright
from stage2_acceptance import FIXTURE, expect, request
from stage2_ui_acceptance import signed_page

SELECTOR = "[data-testid='availability-grid'], [data-testid='no-slots']"

async def run(base):
    fixture = copy.deepcopy(FIXTURE)
    fixture["restaurants"][0]["opening_hours"] = [
        {"weekday": "mon", "opens": "18:00", "closes": "23:00"}]
    expect(request(base, "POST", "/_test/reset", fixture), 204)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        token = expect(request(base, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"}), 200)["token"]
        context, page = await signed_page(browser, base, token)
        await page.get_by_test_id("restaurant-select").select_option("r_arden")
        await page.get_by_test_id("party-size-input").fill("2")
        async def search(date):
            await page.get_by_test_id("date-input").fill(date)
            await page.get_by_test_id("search-button").click()
            await page.locator(SELECTOR).first.wait_for(state="visible", timeout=5000)
        # Initial closed-day search: only no-slots participates in the result contract.
        await search("2035-01-02")
        assert await page.locator(SELECTOR).count() == 1
        assert await page.get_by_test_id("no-slots").is_visible()
        assert await page.get_by_test_id("availability-grid").count() == 0
        assert await page.locator("#availability-grid").locator("[data-testid^='slot-']").count() == 0
        # Open, select a slot, close again; neither stale cells nor form may survive.
        await search("2035-01-01")
        assert await page.get_by_test_id("availability-grid").is_visible()
        assert await page.get_by_test_id("slot-t_1-18:00").count() == 1
        await page.get_by_test_id("slot-t_1-18:00").click()
        assert await page.get_by_test_id("booking-form").is_visible()
        await search("2035-01-02")
        assert await page.locator(SELECTOR).count() == 1
        assert await page.get_by_test_id("availability-grid").count() == 0
        assert await page.get_by_test_id("booking-form").count() == 0
        assert await page.locator("#availability-grid").locator("[data-testid^='slot-']").count() == 0
        await search("2035-01-08")
        assert await page.get_by_test_id("availability-grid").is_visible()
        assert await page.get_by_test_id("slot-t_1-18:00").count() == 1
        assert await page.get_by_test_id("no-slots").is_hidden()
        await browser.close()
    return {"initial_closed": "pass", "open_closed_open": "pass"}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.base_url.rstrip("/")))))
