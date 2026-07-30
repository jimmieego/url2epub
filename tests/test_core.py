import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import subprocess

from url2epub.core import (
    DefuddleError,
    WechatToolError,
    build_epub,
    build_html_book,
    build_pdf,
    count_hacker_news_comments,
    default_output_name,
    extract_article,
    extract_hacker_news_item_from_url,
    extract_url,
    hacker_news_item_id,
    is_hacker_news_item_url,
    is_wechat_url,
    localize_article_images,
    replace_unsupported_embeds,
    render_hacker_news_article,
    render_hacker_news_comments,
    render_article_section_html,
    slugify,
    Article,
    render_article_markdown,
    TYPST_STYLE,
)


class CoreTests(unittest.TestCase):
    def test_slugify_normalizes_title(self) -> None:
        self.assertEqual(
            slugify("Open Source URL to EPUB!"),
            "open-source-url-to-epub",
        )

    def test_default_output_name_uses_article_title(self) -> None:
        with patch("url2epub.core.run_defuddle", side_effect=DefuddleError("missing")):
            article = extract_article(
                """
                <html>
                  <head><title>Example Story</title></head>
                  <body><article><p>This is a long enough article paragraph to keep.</p></article></body>
                </html>
                """,
                "https://example.com/story",
                allow_fallback=True,
            )
        self.assertEqual(default_output_name([article]), "example-story.epub")

    def test_default_output_name_accepts_pdf_extension(self) -> None:
        article = Article(
            title="Printable Story",
            source_url="https://example.com/story",
            content_html="<p>Example content.</p>",
        )

        self.assertEqual(default_output_name([article], extension=".pdf"), "printable-story.pdf")

    def test_extract_article_requires_defuddle_by_default(self) -> None:
        with patch("url2epub.core.run_defuddle", side_effect=DefuddleError("missing")):
            with self.assertRaises(DefuddleError):
                extract_article("<html></html>", "https://example.com/story")

    def test_is_wechat_url_detects_mp_domain(self) -> None:
        self.assertTrue(is_wechat_url("https://mp.weixin.qq.com/s/example"))
        self.assertFalse(is_wechat_url("https://example.com/story"))

    def test_is_hacker_news_item_url_detects_item_pages(self) -> None:
        self.assertTrue(is_hacker_news_item_url("https://news.ycombinator.com/item?id=123"))
        self.assertFalse(is_hacker_news_item_url("https://news.ycombinator.com/news"))

    def test_hacker_news_item_id_reads_query_id(self) -> None:
        self.assertEqual(hacker_news_item_id("https://news.ycombinator.com/item?id=123"), 123)
        self.assertIsNone(hacker_news_item_id("https://news.ycombinator.com/item"))

    def test_extract_article_uses_fallback_content(self) -> None:
        with patch("url2epub.core.run_defuddle", side_effect=DefuddleError("missing")):
            article = extract_article(
                """
                <html>
                  <head>
                    <meta property="og:title" content="Fallback Example" />
                    <meta name="author" content="Ada Lovelace" />
                  </head>
                  <body>
                    <main>
                      <p>Short.</p>
                      <p>This paragraph is definitely long enough to survive the fallback extraction path.</p>
                    </main>
                  </body>
                </html>
                """,
                "https://example.com/fallback",
                allow_fallback=True,
            )
        self.assertEqual(article.title, "Fallback Example")
        self.assertEqual(article.author, "Ada Lovelace")
        self.assertIn("definitely long enough", article.content_html)

    def test_extract_article_prefers_defuddle(self) -> None:
        with patch(
            "url2epub.core.run_defuddle",
            return_value={
                "title": "Defuddled Example",
                "author": "Grace Hopper",
                "content": "<p>Clean article body.</p>",
            },
        ):
            article = extract_article("<html></html>", "https://example.com/defuddled")
        self.assertEqual(article.title, "Defuddled Example")
        self.assertEqual(article.author, "Grace Hopper")
        self.assertIn("Clean article body", article.content_html)

    def test_extract_article_preserves_defuddle_html(self) -> None:
        rich_html = '<figure><img src="https://example.com/image.png"/><figcaption>Caption</figcaption></figure>'
        with patch(
            "url2epub.core.run_defuddle",
            return_value={
                "title": "Rich Example",
                "content": rich_html,
            },
        ):
            article = extract_article("<html></html>", "https://example.com/rich")
        self.assertEqual(article.content_html, rich_html)

    def test_localize_article_images_rewrites_sources(self) -> None:
        article = Article(
            title="Image Example",
            source_url="https://example.com/story",
            content_html='<p><img src="/image.jpg" alt="hero"/></p>',
        )
        with TemporaryDirectory() as tmpdir:
            with patch(
                "url2epub.core.fetch_binary",
                return_value=(b"jpeg-bytes", "image/jpeg"),
            ):
                localized = localize_article_images(article, Path(tmpdir))
        self.assertIn('src="assets/image-001.jpg"', localized.content_html)

    def test_localize_article_images_detects_mislabeled_webp_bytes(self) -> None:
        article = Article(
            title="Image Example",
            source_url="https://example.com/story",
            content_html='<p><img src="/image.jpeg" alt="hero"/></p>',
        )
        webp = b"RIFF\x24\x00\x00\x00WEBPVP8 "

        with TemporaryDirectory() as tmpdir:
            assets_dir = Path(tmpdir)
            with patch(
                "url2epub.core.fetch_binary",
                return_value=(webp, "image/jpeg"),
            ):
                localized = localize_article_images(article, assets_dir)

            saved_image = assets_dir / "image-001.webp"
            self.assertEqual(saved_image.read_bytes(), webp)

        self.assertIn('src="assets/image-001.webp"', localized.content_html)

    def test_build_html_book_uses_chapter_relative_image_paths(self) -> None:
        article = Article(
            title="Image Example",
            source_url="https://example.com/story",
            content_html='<p><img src="/image.jpg" alt="hero"/></p>',
        )
        with TemporaryDirectory() as tmpdir:
            with patch(
                "url2epub.core.fetch_binary",
                return_value=(b"jpeg-bytes", "image/jpeg"),
            ):
                book_path = build_html_book(
                    Path(tmpdir),
                    "Image Example",
                    [article],
                    "en",
                )
                book_html = book_path.read_text(encoding="utf-8")
        self.assertIn('src="chapter_001/assets/image-001.jpg"', book_html)

    def test_replace_unsupported_embeds_uses_iframe_title(self) -> None:
        html = (
            '<figure><iframe src="https://datawrapper.dwcdn.net/G2UHq/1/" '
            'title="Rail ridership chart"></iframe></figure>'
        )
        replaced = replace_unsupported_embeds(html)
        self.assertIn("Interactive content omitted from EPUB: Rail ridership chart", replaced)
        self.assertNotIn("<iframe", replaced)

    def test_replace_unsupported_embeds_falls_back_to_host(self) -> None:
        html = '<iframe src="https://example.com/embed/123"></iframe>'
        replaced = replace_unsupported_embeds(html)
        self.assertIn("Interactive content omitted from EPUB (example.com).", replaced)

    def test_render_article_section_removes_broken_footnote_backlinks(self) -> None:
        article = Article(
            title="Footnote Example",
            source_url="https://example.com/story",
            content_html=(
                '<p>Text<sup id="fnref:1"><a href="#fn:1">1</a></sup></p>'
                '<ol><li id="fn:1">Note '
                '<a class="footnote-backref" href="#fnref:1">return</a></li></ol>'
            ),
        )

        rendered = render_article_section_html(article)

        self.assertIn('href="#fn:1"', rendered)
        self.assertNotIn("footnote-backref", rendered)
        self.assertNotIn('href="#fnref:1"', rendered)

    def test_extract_url_routes_wechat_urls_to_wechat_tool(self) -> None:
        with patch(
            "url2epub.core.extract_wechat_article_from_url",
            return_value=Article(
                title="WeChat Example",
                source_url="https://mp.weixin.qq.com/s/example",
                markdown_content="content",
            ),
        ) as wechat_tool:
            article = extract_url("https://mp.weixin.qq.com/s/example")
        self.assertEqual(article.title, "WeChat Example")
        wechat_tool.assert_called_once()

    def test_extract_url_can_fallback_after_wechat_tool_failure(self) -> None:
        with patch(
            "url2epub.core.extract_wechat_article_from_url",
            side_effect=WechatToolError("missing"),
        ), patch(
            "url2epub.core.fetch_html",
            return_value="<html><head><title>Fallback</title></head><body><article><p>This is fallback content that is long enough.</p></article></body></html>",
        ), patch(
            "url2epub.core.run_defuddle",
            side_effect=DefuddleError("missing"),
        ):
            article = extract_url(
                "https://mp.weixin.qq.com/s/example",
                allow_fallback=True,
            )
        self.assertEqual(article.title, "Fallback")

    def test_extract_wechat_article_reads_reported_markdown_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            output_root = Path(tmpdir) / "site-packages" / "output" / "Example"
            output_root.mkdir(parents=True)
            markdown_path = output_root / "Example.md"
            markdown_path.write_text("# Example\n\nBody", encoding="utf-8")
            images_dir = output_root / "images"
            images_dir.mkdir()
            (images_dir / "img_001.png").write_bytes(b"png")

            executable = Path(tmpdir) / "wechat-article-to-markdown"
            executable.write_text(f"#!{Path(tmpdir) / 'venv' / 'bin' / 'python'}\n", encoding="utf-8")

            with patch(
                "url2epub.core.wechat_tool_command",
                return_value=["wechat-article-to-markdown"],
            ), patch(
                "url2epub.core.resolve_command_path",
                return_value=executable,
            ), patch(
                "url2epub.core.Path.glob",
                wraps=Path.glob,
            ), patch(
                "url2epub.core.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["wechat-article-to-markdown", "https://mp.weixin.qq.com/s/example"],
                    returncode=0,
                    stdout=f"✅ 已保存: {markdown_path}\n",
                    stderr="",
                ),
            ):
                article = extract_url("https://mp.weixin.qq.com/s/example")

        self.assertEqual(article.title, "Example")
        self.assertIn("Body", article.markdown_content or "")
        self.assertIsNotNone(article.asset_dir)
        self.assertTrue((article.asset_dir / "img_001.png").exists())

    def test_render_hacker_news_article_preserves_algolia_comment_tree(self) -> None:
        article = render_hacker_news_article(
            {
                "title": "Example HN Story",
                "url": "https://example.com/story",
                "author": "alice",
                "points": 42,
                "created_at": "2026-05-31T12:41:19.000Z",
                "children": [
                    {
                        "author": "bob",
                        "created_at": "2026-05-31T13:00:00.000Z",
                        "text": 'First comment with a <a href="https://example.com/link">link</a>.',
                        "children": [
                            {
                                "author": "carol",
                                "created_at": "2026-05-31T14:00:00.000Z",
                                "text": "Nested reply.",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
            "https://news.ycombinator.com/item?id=123",
        )

        self.assertEqual(article.title, "Example HN Story")
        self.assertIn("Story link", article.content_html or "")
        self.assertIn("<h2>Comments</h2>", article.content_html or "")
        self.assertIn("First comment", article.content_html or "")
        self.assertIn("Nested reply", article.content_html or "")
        self.assertIn('class="hn-comment hn-depth-0"', article.content_html or "")
        self.assertIn('class="hn-comment hn-depth-1"', article.content_html or "")
        self.assertIn('id="hn-comment-unknown"', article.content_html or "")
        self.assertNotIn("<table", article.content_html or "")
        self.assertNotIn("<td", article.content_html or "")

    def test_render_hacker_news_comments_uses_comment_ids_for_links(self) -> None:
        comments = [
            {
                "id": 456,
                "author": "bob",
                "text": "Comment body.",
                "children": [],
            }
        ]

        html = "".join(render_hacker_news_comments(comments))

        self.assertIn('id="hn-comment-456"', html)

    def test_count_hacker_news_comments_includes_nested_comments(self) -> None:
        comments = [
            {
                "text": "Top",
                "children": [
                    {"text": "Nested", "children": []},
                    {"deleted": True, "text": "Deleted", "children": []},
                ],
            }
        ]

        self.assertEqual(count_hacker_news_comments(comments), 2)

    def test_extract_hacker_news_item_from_url_uses_algolia_api(self) -> None:
        with patch(
            "url2epub.core.fetch_hacker_news_item",
            return_value={
                "title": "API Story",
                "url": "https://example.com/story",
                "children": [],
            },
        ) as fetch_hacker_news_item:
            article = extract_hacker_news_item_from_url(
                "https://news.ycombinator.com/item?id=123",
            )

        self.assertEqual(article.title, "API Story")
        fetch_hacker_news_item.assert_called_once_with(123, timeout=20)

    def test_build_epub_sets_fixed_author_metadata(self) -> None:
        article = Article(
            title="Example Story",
            source_url="https://example.com/story",
            author="Ada Lovelace",
            content_html="<p>Example content.</p>",
        )

        with TemporaryDirectory() as tmpdir, patch(
            "url2epub.core.pandoc_command",
            return_value=["pandoc"],
        ), patch("url2epub.core.subprocess.run") as run_mock:
            output = build_epub([article], Path(tmpdir) / "book.epub")

        self.assertEqual(output, Path(tmpdir) / "book.epub")
        command = run_mock.call_args.args[0]
        self.assertIn("author=URL2EPUB", command)
        self.assertNotIn("author=Ada Lovelace", command)

    def test_render_article_markdown_strips_wechat_generated_header_block(self) -> None:
        article = Article(
            title="Example Story",
            source_url="https://example.com/story",
            markdown_content=(
                "# Example Story\n\n"
                "> 公众号: Example\n"
                "> 发布时间: 2026-04-20 09:00\n"
                "> 原文链接: https://example.com/story\n\n"
                "---\n\n"
                "Body paragraph.\n"
            ),
        )

        rendered = render_article_markdown(article)

        self.assertEqual(rendered.count("# Example Story"), 1)
        self.assertIn("[Source](https://example.com/story)", rendered)
        self.assertIn("Body paragraph.", rendered)
        self.assertNotIn("> 公众号:", rendered)

    def test_build_pdf_uses_pdf_target_and_engine(self) -> None:
        article = Article(
            title="Printable Story",
            source_url="https://example.com/story",
            content_html="<p>Example content.</p>",
        )

        with TemporaryDirectory() as tmpdir, patch(
            "url2epub.core.pandoc_command",
            return_value=["pandoc"],
        ), patch("url2epub.core.subprocess.run") as run_mock:
            output = build_pdf(
                [article],
                Path(tmpdir) / "book.pdf",
                pdf_engine="weasyprint",
            )

        self.assertEqual(output, Path(tmpdir) / "book.pdf")
        command = run_mock.call_args.args[0]
        self.assertIn("--to=pdf", command)
        self.assertIn("--pdf-engine", command)
        self.assertIn("weasyprint", command)
        self.assertIn("author=URL2EPUB", command)
        self.assertNotIn("--include-before-body", command)

    def test_build_pdf_defaults_to_typst_with_type_style(self) -> None:
        article = Article(
            title="Printable Story",
            source_url="https://example.com/story",
            content_html="<p>Example content.</p>",
        )
        captured_style = {"content": ""}

        def capture_run(command: list[str], **_: object) -> None:
            style_path = Path(command[command.index("--include-before-body") + 1])
            captured_style["content"] = style_path.read_text(encoding="utf-8")

        with TemporaryDirectory() as tmpdir, patch(
            "url2epub.core.pandoc_command",
            return_value=["pandoc"],
        ), patch(
            "url2epub.core.typst_command",
            return_value=["/tools/typst"],
        ), patch("url2epub.core.subprocess.run", side_effect=capture_run) as run_mock:
            output = build_pdf([article], Path(tmpdir) / "book.pdf")

        self.assertEqual(output, Path(tmpdir) / "book.pdf")
        command = run_mock.call_args.args[0]
        self.assertIn("--to=typst", command)
        self.assertIn("--pdf-engine", command)
        self.assertIn("/tools/typst", command)
        self.assertIn("--include-before-body", command)
        self.assertIn('"Charter"', captured_style["content"])
        self.assertIn('"Inter"', captured_style["content"])
        self.assertIn('"JetBrains Mono"', captured_style["content"])
        self.assertEqual(captured_style["content"], TYPST_STYLE)


if __name__ == "__main__":
    unittest.main()
