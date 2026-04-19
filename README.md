# url2epub

`url2epub` is a small Python CLI that fetches one or more web pages, extracts readable article content, and packages the result as an EPUB.

## Requirements

`url2epub` depends on a few external tools:

- `pandoc` to generate the final EPUB file
- `defuddle` for the default article extraction path
- `wechat-article-to-markdown` for WeChat articles on `mp.weixin.qq.com` when you want that support

Pandoc must be installed and available on your `PATH`, because `url2epub` shells out to it during EPUB generation.

Common Pandoc installation options:

```bash
# macOS (Homebrew)
brew install pandoc

# Debian / Ubuntu
sudo apt-get install pandoc

# Windows (winget)
winget install --id JohnMacFarlane.Pandoc -e
```

After installing Pandoc, verify that your shell can find it:

```bash
pandoc --version
url2epub doctor
```

If `url2epub doctor` says Pandoc is missing even after installation, the most common cause is that Pandoc was installed somewhere that is not on your shell's `PATH`. Restarting your terminal usually fixes that; otherwise, add the Pandoc install location to `PATH` and try again.

## Install

Install the Python CLI and the default extractor:

```bash
pipx install .
npm install -g defuddle
```

If you prefer not to install Defuddle globally, set `URL2EPUB_DEFUDDLE_CMD` to the command you want `url2epub` to run.

```bash
export URL2EPUB_DEFUDDLE_CMD="npx --yes defuddle"
```

## Optional WeChat Support

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

The explicit subcommand form also works:

```bash
url2epub convert "https://example.com/article"
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

Check whether the required tools are installed:

```bash
url2epub doctor
```

The `doctor` command checks:
- `pandoc`
- `defuddle`
- `wechat-article-to-markdown` if present

It exits non-zero when the required core tools are missing.

## Development

Run the test suite locally:

```bash
python -m unittest discover -s tests -v
```

Refresh the installed `pipx` CLI after local code changes:

```bash
pipx install --force .
```

GitHub Actions runs the same basic checks on pushes and pull requests:
- install the package
- install Defuddle with npm
- install Pandoc
- run `url2epub doctor`
- run the unit tests

## Notes

- Defuddle is the default extraction engine and is required unless you pass `--allow-fallback`.
- WeChat article URLs are detected automatically and use `wechat-article-to-markdown` when installed.
- EPUB generation is handled by Pandoc.
- Images are downloaded locally and rewritten before the EPUB is built so Pandoc can embed them.
- The fallback extractor is intentionally conservative and aimed at article-style pages.
