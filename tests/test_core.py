import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import subprocess

from url2epub.core import (
    DefuddleError,
    WechatToolError,
    build_epub,
    default_output_name,
    extract_article,
    extract_url,
    is_wechat_url,
    localize_article_images,
    replace_unsupported_embeds,
    slugify,
    Article,
    render_article_markdown,
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

    def test_extract_article_requires_defuddle_by_default(self) -> None:
        with patch("url2epub.core.run_defuddle", side_effect=DefuddleError("missing")):
            with self.assertRaises(DefuddleError):
                extract_article("<html></html>", "https://example.com/story")

    def test_is_wechat_url_detects_mp_domain(self) -> None:
        self.assertTrue(is_wechat_url("https://mp.weixin.qq.com/s/example"))
        self.assertFalse(is_wechat_url("https://example.com/story"))

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


if __name__ == "__main__":
    unittest.main()
