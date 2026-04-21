from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from typing import Iterable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

EPUB_AUTHOR = "URL2EPUB"

BLOCKED_TAGS = {
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "noscript",
    "iframe",
    "svg",
}


@dataclass
class Article:
    title: str
    source_url: str
    author: str | None = None
    content_html: str | None = None
    markdown_content: str | None = None
    asset_dir: Path | None = None


class DefuddleError(RuntimeError):
    """Raised when Defuddle is unavailable or fails to parse content."""


class PandocError(RuntimeError):
    """Raised when Pandoc is unavailable or fails to build the EPUB."""


class WechatToolError(RuntimeError):
    """Raised when the WeChat-specific extraction tool is unavailable or fails."""


def fetch_html(url: str, timeout: int = 20) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def fetch_binary(url: str, timeout: int = 20) -> tuple[bytes, str | None]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get_content_type()


def extract_url(url: str, *, timeout: int = 20, allow_fallback: bool = False) -> Article:
    if is_wechat_url(url):
        try:
            return extract_wechat_article_from_url(url)
        except WechatToolError:
            if not allow_fallback:
                raise

    html = fetch_html(url, timeout=timeout)
    return extract_article(html, url, allow_fallback=allow_fallback)


def extract_article(
    html: str,
    source_url: str,
    *,
    allow_fallback: bool = False,
) -> Article:
    try:
        return extract_article_with_defuddle(html, source_url)
    except DefuddleError:
        if not allow_fallback:
            raise

    title = extract_title(html) or hostname_label(source_url)
    content_html = fallback_extract_content(html)
    if not content_html.strip():
        content_html = "<p>Unable to extract readable content from this page.</p>"

    author = extract_author(html)
    return Article(
        title=title,
        source_url=source_url,
        author=author,
        content_html=content_html,
    )


def extract_article_with_defuddle(html: str, source_url: str) -> Article:
    result = run_defuddle(html, source_url)
    title = clean_text(string_value(result.get("title"))) or hostname_label(source_url)
    content_html = string_value(result.get("content")).strip()
    if not content_html:
        raise DefuddleError("Defuddle returned empty content.")

    return Article(
        title=title,
        source_url=source_url,
        author=clean_text(string_value(result.get("author"))) or None,
        content_html=content_html,
    )


def extract_wechat_article_from_url(url: str) -> Article:
    command = wechat_tool_command()
    if not command:
        raise WechatToolError("wechat-article-to-markdown is not installed.")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        try:
            subprocess.run(
                [*command, url],
                cwd=tmp,
                text=True,
                capture_output=True,
                check=True,
                timeout=120,
            )
        except OSError as exc:
            raise WechatToolError(f"Failed to launch wechat-article-to-markdown: {exc}") from exc
        except subprocess.CalledProcessError as exc:
            stderr = clean_text(exc.stderr)
            raise WechatToolError(
                f"wechat-article-to-markdown failed: {stderr or exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise WechatToolError("wechat-article-to-markdown timed out.") from exc

        markdown_files = sorted((tmp / "output").glob("*/*.md"))
        if not markdown_files:
            raise WechatToolError("No Markdown output was produced for the WeChat article.")

        markdown_path = markdown_files[0]
        title = clean_text(markdown_path.stem) or hostname_label(url)
        markdown_content = markdown_path.read_text(encoding="utf-8")

        persistent_assets = None
        images_dir = markdown_path.parent / "images"
        if images_dir.exists():
            persistent_assets = Path(tempfile.mkdtemp(prefix="url2epub-wechat-assets-"))
            shutil.copytree(images_dir, persistent_assets / "images", dirs_exist_ok=True)
            persistent_assets = persistent_assets / "images"

    return Article(
        title=title,
        source_url=url,
        markdown_content=markdown_content,
        asset_dir=persistent_assets,
    )


def run_defuddle(html: str, source_url: str) -> dict[str, object]:
    command = defuddle_command()
    if not command:
        raise DefuddleError("Defuddle CLI is not installed.")

    html_with_base = inject_base_href(html, source_url)
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=True) as handle:
            handle.write(html_with_base)
            handle.flush()
            completed = subprocess.run(
                [*command, "parse", handle.name, "--json"],
                text=True,
                capture_output=True,
                check=True,
                timeout=30,
            )
    except OSError as exc:
        raise DefuddleError(f"Failed to launch Defuddle: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = clean_text(exc.stderr)
        raise DefuddleError(f"Defuddle failed: {stderr or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DefuddleError("Defuddle timed out while parsing content.") from exc

    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DefuddleError("Defuddle returned invalid JSON.") from exc

    if not isinstance(data, dict):
        raise DefuddleError("Defuddle returned an unexpected payload.")
    return data


def defuddle_command() -> list[str] | None:
    explicit = os.environ.get("URL2EPUB_DEFUDDLE_CMD")
    if explicit:
        return explicit.split()

    local_candidates = [
        Path.cwd() / "node_modules" / ".bin" / "defuddle",
        Path(__file__).resolve().parents[1] / "node_modules" / ".bin" / "defuddle",
    ]
    for candidate in local_candidates:
        if candidate.exists():
            return [str(candidate)]

    if shutil.which("defuddle"):
        return ["defuddle"]
    if shutil.which("npx"):
        return ["npx", "--yes", "defuddle"]
    return None


def wechat_tool_command() -> list[str] | None:
    explicit = os.environ.get("URL2EPUB_WECHAT_CMD")
    if explicit:
        return explicit.split()
    if shutil.which("wechat-article-to-markdown"):
        return ["wechat-article-to-markdown"]
    return None


def pandoc_command() -> list[str] | None:
    if shutil.which("pandoc"):
        return ["pandoc"]
    return None


def is_wechat_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "mp.weixin.qq.com" or host.endswith(".mp.weixin.qq.com")


def fallback_extract_content(html: str) -> str:
    stripped = strip_ignored_blocks(html)
    container = first_tag_contents(stripped, "article")
    if not container:
        container = first_tag_contents(stripped, "main")
    if not container:
        container = first_tag_contents(stripped, "body")
    if not container:
        container = stripped

    parser = ArticleHTMLParser()
    parser.feed(container)
    parser.close()
    return parser.to_html()


def build_epub(
    articles: Iterable[Article],
    output_path: str | Path,
    book_title: str | None = None,
    language: str = "en",
) -> Path:
    article_list = list(articles)
    if not article_list:
        raise ValueError("At least one article is required to build an EPUB.")

    pandoc = pandoc_command()
    if not pandoc:
        raise PandocError("Pandoc is not installed.")

    title = book_title or article_list[0].title
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        css_path = tmp / "epub.css"
        css_path.write_text(DEFAULT_CSS, encoding="utf-8")
        html_book = build_html_book(tmp, title, article_list, language)
        command = [
            *pandoc,
            str(html_book),
            "--from=html",
            "--to=epub",
            "--standalone",
            "--toc",
            "--css",
            str(css_path),
            "--metadata",
            f"title={title}",
            "--metadata",
            f"author={EPUB_AUTHOR}",
            "--metadata",
            f"lang={language}",
            "--resource-path",
            str(tmp),
            "--output",
            str(output),
        ]
        try:
            subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=True,
                timeout=60,
            )
        except OSError as exc:
            raise PandocError(f"Failed to launch Pandoc: {exc}") from exc
        except subprocess.CalledProcessError as exc:
            stderr = clean_text(exc.stderr)
            raise PandocError(f"Pandoc failed: {stderr or exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise PandocError("Pandoc timed out while generating the EPUB.") from exc

    return output


def build_html_book(
    workspace: Path,
    title: str,
    articles: list[Article],
    language: str,
) -> Path:
    sections: list[str] = []

    for index, article in enumerate(articles, start=1):
        chapter_dir = workspace / f"chapter_{index:03d}"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        if article.markdown_content is not None:
            sections.append(render_markdown_article_html(article, chapter_dir))
        else:
            localized = localize_article_images(article, chapter_dir / "assets")
            sections.append(render_article_section_html(localized))

    book_path = workspace / "book.html"
    body = "\n".join(sections)
    book_path.write_text(
        f"""<!DOCTYPE html>
<html lang="{escape(language)}">
  <head>
    <meta charset="utf-8"/>
    <title>{escape(title)}</title>
  </head>
  <body>
{body}
  </body>
</html>
""",
        encoding="utf-8",
    )
    return book_path


def render_markdown_article_html(article: Article, chapter_dir: Path) -> str:
    pandoc = pandoc_command()
    if not pandoc:
        raise PandocError("Pandoc is not installed.")

    if article.asset_dir and article.asset_dir.exists():
        shutil.copytree(article.asset_dir, chapter_dir / "images", dirs_exist_ok=True)

    path = chapter_dir / "chapter.md"
    path.write_text(render_article_markdown(article), encoding="utf-8")

    try:
        completed = subprocess.run(
            [*pandoc, str(path), "--from=markdown", "--to=html"],
            cwd=chapter_dir,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
    except OSError as exc:
        raise PandocError(f"Failed to launch Pandoc: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = clean_text(exc.stderr)
        raise PandocError(f"Pandoc failed: {stderr or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PandocError("Pandoc timed out while rendering Markdown content.") from exc

    return completed.stdout.strip()


def render_article_markdown(article: Article) -> str:
    body = article.markdown_content or ""
    heading = f"# {article.title}"
    starts_with_heading = clean_text(body).startswith(f"# {article.title}")

    parts: list[str] = []
    if not starts_with_heading:
        parts.append(heading)
        parts.append("")
    if article.author:
        parts.append(f"*By {article.author}*")
        parts.append("")
    parts.append(f"[Source]({article.source_url})")
    parts.append("")
    parts.append(body.strip())
    parts.append("")
    return "\n".join(parts)


def render_article_section_html(article: Article) -> str:
    byline = f"<p><em>By {escape(article.author)}</em></p>" if article.author else ""
    content = replace_unsupported_embeds(article.content_html or "")
    return (
        "<section>"
        f"<h1>{escape(article.title)}</h1>"
        f"{byline}"
        f'<p><a href="{escape(article.source_url, quote=True)}">Source</a></p>'
        f"{content}"
        "</section>"
    )


def localize_article_images(article: Article, assets_dir: Path) -> Article:
    if not article.content_html:
        return article

    assets_dir.mkdir(parents=True, exist_ok=True)
    index = {"value": 0}

    def replace(match: re.Match[str]) -> str:
        img_tag = match.group(0)
        src = match.group("src")
        localized = localize_image_source(src, article.source_url, assets_dir, index)
        if not localized:
            return strip_img_web_attrs(img_tag)
        updated = img_tag.replace(src, localized, 1)
        return strip_img_web_attrs(updated)

    localized_html = IMG_TAG_RE.sub(replace, article.content_html)
    return Article(
        title=article.title,
        source_url=article.source_url,
        author=article.author,
        content_html=localized_html,
        markdown_content=article.markdown_content,
        asset_dir=article.asset_dir,
    )


def localize_image_source(
    src: str,
    base_url: str,
    assets_dir: Path,
    index: dict[str, int],
) -> str | None:
    source = clean_text(src)
    if not source:
        return None

    index["value"] += 1
    if source.startswith("data:"):
        payload = decode_data_uri(source)
        if not payload:
            return None
        binary, media_type = payload
        suffix = suffix_for_media_type(media_type) or ".bin"
        filename = f"image-{index['value']:03d}{suffix}"
        target = assets_dir / filename
        target.write_bytes(binary)
        return f"assets/{filename}"

    absolute_url = urljoin(base_url, source)
    try:
        binary, media_type = fetch_binary(absolute_url)
    except Exception:
        return None

    suffix = suffix_for_url(absolute_url) or suffix_for_media_type(media_type) or ".bin"
    filename = f"image-{index['value']:03d}{suffix}"
    target = assets_dir / filename
    target.write_bytes(binary)
    return f"assets/{filename}"


def decode_data_uri(uri: str) -> tuple[bytes, str] | None:
    match = re.match(
        r"data:(?P<media>[^;,]+)?(?P<base64>;base64)?,(?P<data>.*)",
        uri,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None

    media_type = match.group("media") or "application/octet-stream"
    payload = match.group("data")
    try:
        if match.group("base64"):
            return base64.b64decode(payload), media_type
    except ValueError:
        return None
    return payload.encode("utf-8"), media_type


def default_output_name(articles: Iterable[Article], explicit_title: str | None = None) -> str:
    article_list = list(articles)
    if explicit_title:
        base = explicit_title
    elif article_list:
        base = article_list[0].title
    else:
        base = "book"
    return slugify(base) + ".epub"


def extract_title(html: str) -> str | None:
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<title[^>]*>(.*?)</title>",
        r"<h1[^>]*>(.*?)</h1>",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(strip_tags(match.group(1)))
    return None


def extract_author(html: str) -> str | None:
    patterns = [
        r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+property=["\']article:author["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']byl["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(match.group(1))
    return None


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def string_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


def slugify(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "book"


def hostname_label(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or "article"
    return host.removeprefix("www.")


def inject_base_href(html: str, source_url: str) -> str:
    base_tag = f'<base href="{escape(source_url, quote=True)}"/>'
    if re.search(r"<head\b[^>]*>", html, flags=re.IGNORECASE):
        return re.sub(
            r"(<head\b[^>]*>)",
            rf"\1{base_tag}",
            html,
            count=1,
            flags=re.IGNORECASE,
        )
    if re.search(r"<html\b[^>]*>", html, flags=re.IGNORECASE):
        return re.sub(
            r"(<html\b[^>]*>)",
            rf"\1<head>{base_tag}</head>",
            html,
            count=1,
            flags=re.IGNORECASE,
        )
    return f"<html><head>{base_tag}</head><body>{html}</body></html>"


def strip_ignored_blocks(html: str) -> str:
    cleaned = html
    for tag in BLOCKED_TAGS:
        cleaned = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return cleaned


def first_tag_contents(html: str, tag: str) -> str | None:
    match = re.search(
        rf"<{tag}\b[^>]*>(.*?)</{tag}>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else None


def strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def replace_unsupported_embeds(html: str) -> str:
    return IFRAME_RE.sub(replace_iframe_with_note, html)


def replace_iframe_with_note(match: re.Match[str]) -> str:
    attrs = match.group("attrs") or ""
    note = iframe_note_from_attrs(attrs)
    return f'<p><em>{escape(note)}</em></p>'


def iframe_note_from_attrs(attrs: str) -> str:
    for attr in ("title", "aria-label", "aria-describedby", "name", "data-title"):
        value = extract_html_attr(attrs, attr)
        if value:
            value = clean_text(value)
            if value:
                return f"Interactive content omitted from EPUB: {value}"

    src = extract_html_attr(attrs, "src")
    if src:
        parsed = urlparse(src)
        host = parsed.netloc.removeprefix("www.")
        if host:
            return f"Interactive content omitted from EPUB ({host})."

    return "Interactive content omitted from EPUB."


def extract_html_attr(attrs: str, name: str) -> str | None:
    match = re.search(
        rf'\b{name}\s*=\s*["\']([^"\']+)["\']',
        attrs,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return None


def suffix_for_media_type(media_type: str | None) -> str | None:
    if not media_type:
        return None
    return mimetypes.guess_extension(media_type.split(";", 1)[0].strip().lower())


def suffix_for_url(url: str) -> str | None:
    path = urlparse(url).path
    suffix = Path(path).suffix
    if suffix and len(suffix) <= 6:
        return suffix
    return None


class ArticleHTMLParser(HTMLParser):
    allowed = {"p", "h1", "h2", "h3", "blockquote", "pre", "code", "ul", "ol", "li", "a"}
    block_like = {"p", "h1", "h2", "h3", "blockquote", "pre", "li"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.text_buffer: list[str] = []
        self.capture_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.allowed:
            self.flush_text()
            attr_text = ""
            if tag == "a":
                href = ""
                for key, value in attrs:
                    if key == "href" and value:
                        href = escape(value, quote=True)
                        break
                attr_text = f' href="{href}"' if href else ""
            self.out.append(f"<{tag}{attr_text}>")
            self.stack.append(tag)
            self.capture_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.stack and tag == self.stack[-1]:
            self.flush_text()
            self.out.append(f"</{tag}>")
            self.stack.pop()
            self.capture_depth = max(0, self.capture_depth - 1)

    def handle_data(self, data: str) -> None:
        text = clean_text(data)
        if not text:
            return
        if self.capture_depth > 0:
            self.text_buffer.append(text)

    def close(self) -> None:
        self.flush_text()
        super().close()

    def flush_text(self) -> None:
        if not self.text_buffer:
            return
        text = " ".join(self.text_buffer)
        if len(text) >= 20 or any(tag in self.block_like for tag in self.stack):
            self.out.append(escape(text))
        self.text_buffer.clear()

    def to_html(self) -> str:
        html = "".join(self.out).strip()
        return html or "<p>No readable content extracted.</p>"


DEFAULT_CSS = """
body { font-family: serif; line-height: 1.5; margin: 5%; }
h1, h2, h3 { line-height: 1.2; }
pre { white-space: pre-wrap; }
img { max-width: 100%; height: auto; }
a { text-decoration: underline; color: inherit; }
"""


IMG_TAG_RE = re.compile(
    r'(<img\b[^>]*\bsrc=["\'])(?P<src>[^"\']+)(["\'][^>]*>)',
    flags=re.IGNORECASE,
)

IFRAME_RE = re.compile(
    r"<iframe\b(?P<attrs>[^>]*)>(?:.*?)</iframe>",
    flags=re.IGNORECASE | re.DOTALL,
)


def strip_img_web_attrs(img_tag: str) -> str:
    cleaned = img_tag
    for attr in ("srcset", "sizes", "loading", "decoding", "fetchpriority"):
        cleaned = re.sub(
            rf'\s{attr}=["\'][^"\']*["\']',
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
    return cleaned
