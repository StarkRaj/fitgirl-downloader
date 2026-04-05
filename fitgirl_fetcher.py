import re
import requests
from bs4 import BeautifulSoup

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
        response = requests.get(page_url, timeout=10)
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