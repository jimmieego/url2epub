# url2epub

`url2epub` is a small Python CLI that fetches one or more web pages, extracts readable article content, and packages the result as an EPUB.

## Install

```bash
pipx install .
npm install -g defuddle
```

`pandoc` must also be installed and available on your `PATH`.

If you prefer not to install Defuddle globally, set `URL2EPUB_DEFUDDLE_CMD` to the command you want `url2epub` to run.

```bash
export URL2EPUB_DEFUDDLE_CMD="npx --yes defuddle"
```

WeChat article support is detected automatically for `mp.weixin.qq.com` URLs and uses `wechat-article-to-markdown` when available.

```bash
pipx install wechat-article-to-markdown
```

Or point `url2epub` at a custom WeChat extractor command:

```bash
export URL2EPUB_WECHAT_CMD="wechat-article-to-markdown"
```

## Usage

Convert a single URL:

```bash
url2epub "https://example.com/article"
```

The CLI shows progress in the terminal and prints the detected article title as it processes each URL. If you do not pass `--title`, the default output filename is inferred from the extracted title.

If the inferred EPUB filename already exists, `url2epub` now automatically picks a numbered filename like `article-title-2.epub` to avoid accidental reuse during repeated imports into reader apps such as Apple Books.

Allow the built-in extractor only when Defuddle fails:

```bash
url2epub --allow-fallback "https://example.com/article"
```

Convert multiple URLs into one book:

```bash
url2epub \
  --title "Weekend Reading" \
  --output weekend-reading.epub \
  "https://example.com/1" \
  "https://example.com/2"
```

## Notes

- Defuddle is the default extraction engine and is required unless you pass `--allow-fallback`.
- WeChat article URLs are detected automatically and use `wechat-article-to-markdown` when installed.
- EPUB generation is handled by Pandoc.
- Images are downloaded locally and rewritten before the EPUB is built so Pandoc can embed them.
- The fallback extractor is intentionally conservative and aimed at article-style pages.
