async def run(page, context):
    await page.goto(
        context["fixture_origin"] + "/",
        wait_until="domcontentloaded",
    )
    return {
        "title": await page.locator("#fixture-title").inner_text(),
        "status": await page.locator("#fixture-status").inner_text(),
        "probe": context["input"]["probe"],
    }
