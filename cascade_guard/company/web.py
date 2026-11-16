"""Add a company's own web pages as documents: fetch a page (and the pages it links to on the same site).

Fetching a URL that a customer typed is a classic way to make a server read things it should not (its own
network, cloud metadata addresses). So: only http and https, only public addresses (every address the name resolves
to is checked, again after each redirect), no credentials in the URL, a size cap and a time limit.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

MAX_BYTES = 3 * 1024 * 1024
TIMEOUT = 12.0
AGENT = "CascadeGuardBot/1.0 (+reads pages its owner asked it to add)"


class FetchError(ValueError):
    pass


def is_public_host(host: str) -> bool:
    """True when every address the host resolves to is an ordinary public internet address."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return False
    addrs = {i[4][0] for i in infos}
    if not addrs:
        return False
    for a in addrs:
        ip = ipaddress.ip_address(str(a).split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified or not ip.is_global):
            return False
    return True


def check_url(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("Enter a full web address that starts with https://")
    if u.username or u.password:
        raise FetchError("Addresses with a username or password in them are not fetched.")
    if u.port not in (None, 80, 443, 8080, 8443):
        raise FetchError("That port is not fetched.")
    if not is_public_host(u.hostname):
        raise FetchError("That address is not a public website.")
    if not u.path:
        u = u._replace(path="/")
    return urldefrag(u.geturl())[0]


def fetch(url: str) -> tuple[str, bytes, str]:
    """(final url, body, content type), following up to 4 redirects, each checked like the first address."""
    import httpx
    for _ in range(5):
        url = check_url(url)
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": AGENT}) as c, \
                    c.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    raise FetchError(f"The site answered {r.status_code} for {url}")
                body = b""
                for part in r.iter_bytes():
                    body += part
                    if len(body) > MAX_BYTES:
                        raise FetchError("That page is larger than 3 MB.")
                return url, body, r.headers.get("content-type", "").split(";")[0].strip().lower()
        except httpx.HTTPError as e:
            raise FetchError(f"Could not reach {urlparse(url).hostname} ({type(e).__name__}).") from e
    raise FetchError("Too many redirects.")


class _Page(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template"}
    BLOCK = {"p", "div", "li", "tr", "br", "section", "article", "table", "ul", "ol", "dd", "dt", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.links: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        elif re.fullmatch(r"h[1-6]", tag) and not self._skip:
            self.out.append("\n\n# ")
        elif tag in self.BLOCK and not self._skip:
            self.out.append("\n")
        elif tag in ("td", "th") and not self._skip:
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif (re.fullmatch(r"h[1-6]", tag) or tag in ("p", "table", "ul", "ol")) and not self._skip:
            self.out.append("\n\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.out.append(data)


def page_text(html_bytes: bytes) -> tuple[str, str, list[str]]:
    """(title, readable text, links) of an HTML page, without menus, scripts and footers."""
    from cascade_guard.company.kb import _decode
    p = _Page()
    p.feed(_decode(html_bytes))
    text = "".join(p.out)
    text = "\n".join(re.sub(r"[ \t\xa0]+", " ", ln).strip(" |") for ln in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return re.sub(r"\s+", " ", p.title).strip()[:150], text, p.links


def doc_name(url: str, title: str) -> str:
    u = urlparse(url)
    path = u.path.strip("/") or "home"
    return f"{u.hostname}/{path}"[:180] if not title else f"{title[:110]} ({u.hostname}/{path})"[:190]


def import_site(workspace: str, url: str, *, max_pages: int = 1, by: str = "", max_chunks: int | None = None,
                store=None) -> dict:
    """Add a page, and up to max_pages - 1 pages it links to on the same site. Returns what was added or skipped."""
    from cascade_guard.company import kb
    start = check_url(url)
    host = urlparse(start).hostname
    queue, seen = [start], {start}
    added: list[dict] = []
    skipped: list[dict] = []
    while queue and len(added) + len(skipped) < max_pages:
        u = queue.pop(0)
        try:
            final, body, ctype = fetch(u)
            if ctype == "application/pdf" or final.lower().endswith(".pdf"):
                doc = kb.add_document(workspace, doc_name(final, ""), body, by=by, max_chunks=max_chunks, store=store)
            elif "html" in ctype or ctype.startswith("text/") or not ctype:
                title, text, links = page_text(body)
                if len(text) < 80:
                    raise FetchError("the page has almost no text (it may need JavaScript to show)")
                doc = kb.add_document(workspace, doc_name(final, title) + ".txt", text.encode(), by=by,
                                      max_chunks=max_chunks, store=store)
                for href in links:
                    nxt = urldefrag(urljoin(final, href))[0]
                    if (urlparse(nxt).hostname == host and nxt not in seen and len(seen) < max_pages * 6
                            and not re.search(r"\.(jpe?g|png|gif|webp|svg|zip|mp4|mp3|css|js|ico|xml)(\?|$)", nxt, re.I)):
                        seen.add(nxt)
                        queue.append(nxt)
            else:
                raise FetchError(f"{ctype} pages are not read")
            added.append({"url": final, "name": doc["name"], "passages": doc["passages"]})
        except (FetchError, kb.DocumentError) as e:
            skipped.append({"url": u, "why": str(e)})
            if "document limit" in str(e):
                break
    return {"added": added, "skipped": skipped}
