"""Bounded read-only production SEO evidence; no login, billing or API writes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
import time
import subprocess
import tempfile
from pathlib import Path
from email.parser import Parser
import xml.etree.ElementTree as ET


class Head(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.canonicals = []
        self.meta = {}
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'link' and attrs.get('rel') == 'canonical':
            self.canonicals.append(attrs.get('href'))
        if tag == 'meta':
            self.meta[attrs.get('name') or attrs.get('property')] = attrs.get('content')
        if tag == 'a':
            self.links.append(attrs.get('href', ''))


def probe(url):
    start = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory() as directory:
            body = Path(directory) / 'body'
            headers = Path(directory) / 'headers'
            completed = subprocess.run(['curl', '-sS', '-L', '--max-time', '35',
                                        '-D', str(headers), '-o', str(body), '-w', '%{json}', url],
                                       capture_output=True, text=True, check=True)
            timing = json.loads(completed.stdout)
            html = body.read_text(errors='replace')
            header_blocks = headers.read_text().strip().split('\n\n')
            final_headers = Parser().parsestr('\n'.join(header_blocks[-1].splitlines()[1:]))
            ttfb = timing['time_starttransfer']
            head = Head(html)
            result = dict(url=url, final_url=timing["url_effective"], status=timing["http_code"],
                          ttfb_seconds=round(ttfb, 3), canonicals=head.canonicals,
                          robots=final_headers.get('X-Robots-Tag'),
                          meta_robots=head.meta.get('robots'),
                          description=head.meta.get('description'),
                          stock_links=len(set(x for x in head.links if x.startswith('/stock/'))),
                          fallback_prompt='Fallback research prompt' in html,
                          unavailable_chart='Source data is currently unavailable' in html)
            if url.endswith('/sitemap.xml'):
                root = ET.fromstring(html)
                urls = [x.text for x in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
                result.update(sitemap_count=len(urls), unique_count=len(set(urls)),
                              query_urls=[x for x in urls if '?' in x])
            if url.endswith('/robots.txt'):
                result['body'] = html
            return result
    except Exception as error:
        return dict(url=url, error=str(error), elapsed=round(time.perf_counter()-start, 3))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='https://www.stockradarhq.com')
    args = parser.parse_args()
    paths = ['/', '/healthz', '/robots.txt', '/sitemap.xml', '/universe', '/universe?q=MSFT',
             '/newsletter', '/newsletter/latest', '/premium-decision/MSFT', '/stock/aapl',
             '/stock/BAE.L', '/stock/MSFT', '/stock/KO', '/stock/SPY', '/stock/HLMA.L',
             '/stock/AAPL']
    paths += ['/stock/AAPL?range='+r for r in ('1h', '24h', '1mo', '6mo', '1y', 'ytd')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(probe, [args.base_url.rstrip('/')+p for p in paths]))
    print(json.dumps(results, indent=2))
