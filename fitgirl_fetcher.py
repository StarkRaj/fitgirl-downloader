import re
import asyncio
import random
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

def get_tor_session():
    session = requests.session()
    # Tor uses the 9050 port as the default socks port
    session.proxies = {'http':  'socks5h://127.0.0.1:9050',
                       'https': 'socks5h://127.0.0.1:9050'}
    return session

class FitgirlFetcher:
    def fetch_downloadable_links(self, url, server_name = "fuckingfast"):
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # Find all <a> tags (which define hyperlinks)
            links = soup.find_all('a')
            download_links = []
            # Extract and print the href attribute from each <a> tag
            for link in links:
                href = link.get('href')
                if href and server_name in href:  # Ensure the href attribute exists
                    download_links.append(href)
            return download_links
        else:
            print(f"Failed to retrieve the page. Status code: {response.status_code}")

    def fetch_file_url(self, page_url):
        # Send a GET request to the website
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Language": "en-US,en;q=0.9",
        }
        response = requests.get(page_url, timeout=10, headers=headers)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            script_tags = soup.find_all('script')

            for script in script_tags:
                if script.string:  # Ensure the script has content
                    # Use regex to find window.open links
                    matches = re.findall(r'window\.open\(["\'](.*?)["\']', script.string)
                    if matches:
                        return matches[0]
        else:
            print(f"Failed to retrieve the page. Status code: {response.status_code}")

    def get_file_url_torrent(self, url: str) -> str:
        session = get_tor_session()
        response = session.get(url, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            script_tags = soup.find_all('script')

            for script in script_tags:
                if script.string:  # Ensure the script has content
                    # Use regex to find window.open links
                    matches = re.findall(r'window\.open\(["\'](.*?)["\']', script.string)
                    if matches:
                        return matches[0]
        else:
            print(f"Failed to retrieve the page. Status code: {response.status_code}")

    async def _extract_download_link(self, url: str) -> str:
        """Internal function to extract download link with improved stealth"""
        async with async_playwright() as p:
            width = random.randint(1280, 1920)
            height = random.randint(800, 1080)
            
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-features=IsolateOrigins,site-per-process'
                ]
            )
            
            context = await browser.new_context(
                viewport={'width': width, 'height': height},
                user_agent='Mozilla/5.0 ... Chrome/121...',
                locale='en-US',
                timezone_id='Asia/Kolkata',
                has_touch=False,
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Upgrade-Insecure-Requests": "1"
                }
            )
            
            page = await context.new_page()
            await page.mouse.move(100, 200)
            await page.wait_for_timeout(random.randint(1000, 3000))
            await page.mouse.wheel(0, 500)
            
            stealth = Stealth()
            await stealth.apply_stealth_async(page)
            
            await asyncio.sleep(random.uniform(0.5, 1.5))
            
            try:
                # Navigate with extended timeout
                await page.goto(url, wait_until='networkidle', timeout=60000)
                
                # Check for rate limiting
                rate_limited = await page.evaluate("""() => {
                    return document.body.textContent.includes('rate limited') || 
                        document.body.textContent.includes('Rate Limit') ||
                        document.body.textContent.includes('Too many requests');
                }""")
                
                if rate_limited:
                    await browser.close()
                    raise Exception("Rate limiting detected")
                
                # Add slight delay to let any scripts initialize
                await asyncio.sleep(random.uniform(1, 2))
                
                download_link = await page.evaluate("""() => {
                    const scripts = document.querySelectorAll('script');
                    let dlLink = null;
                    
                    for (const script of scripts) {
                        const content = script.textContent || script.innerText;
                        if (!content) continue;
                        
                        if (content.includes('function download()')) {
                            // Try multiple regex patterns
                            let match = content.match(/window\\.open\\("(https:\\/\\/fuckingfast\\.co\\/dl\\/[^"]+)"/);
                            if (!match) {
                                match = content.match(/window\.open\("(https:\/\/fuckingfast\.co\/dl\/[^"]+)"/);
                            }
                            if (match && match[1]) {
                                dlLink = match[1];
                                break;
                            }
                        }
                    }

                    // Fallback: look for download button or direct link
                    if (!dlLink) {
                        const downloadBtns = [...document.querySelectorAll('a[href*="fuckingfast.co/dl/"]')];
                        if (downloadBtns.length > 0) {
                            dlLink = downloadBtns[0].href;
                        }
                    }

                    return dlLink;
                }""")
                
                await browser.close()
                
                if not download_link:
                    return "Download link not found"
                    
                return download_link
                
            except Exception as e:
                await browser.close()
                raise

# if __name__ == "__main__":
#     fetcher = FitgirlFetcher()
#     url = "https://fuckingfast.co/820vu2kleznl#Ghost_of_Tsushima_DC_--_fitgirl-repacks.site_--_.part01.rar"
#     link = asyncio.run(fetcher._extract_download_link(url))
#     print(f"Extracted Download Link: {link}")