import asyncio
from pyppeteer import launch

async def test():
    browser = await launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
    page = await browser.newPage()
    await page.goto('https://httpbin.org/html')
    print('pyppeteer OK:', await page.title())
    await browser.close()

asyncio.get_event_loop().run_until_complete(test())
