from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .core import build_epub, default_output_name, extract_url


def progress(message: str) -> None:
    print(message, file=sys.stderr)


def choose_output_path(requested_output: str | None, inferred_output: str) -> str:
    if requested_output:
        return requested_output

    candidate = Path(inferred_output)
    if not candidate.exists():
        return str(candidate)

    stem = candidate.stem
    suffix = candidate.suffix or ".epub"
    index = 2
    while True:
        numbered = candidate.with_name(f"{stem}-{index}{suffix}")
        if not numbered.exists():
            return str(numbered)
        index += 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="url2epub",
        description="Fetch one or more URLs and package them into an EPUB.",
    )
    parser.add_argument("urls", nargs="+", help="One or more HTTP(S) URLs.")
    parser.add_argument("-o", "--output", help="Output EPUB filename.")
    parser.add_argument("--title", help="Override the EPUB book title.")
    parser.add_argument(
        "--language",
        default="en",
        help="Language code stored in the EPUB metadata. Default: en",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Per-request timeout in seconds. Default: 20",
    )
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="Use the built-in extractor only if Defuddle is unavailable or fails.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    articles = []
    total = len(args.urls)
    for index, url in enumerate(args.urls, start=1):
        progress(f"[{index}/{total}] Extracting {url}")
        try:
            article = extract_url(
                url,
                timeout=args.timeout,
                allow_fallback=args.allow_fallback,
            )
            articles.append(article)
            progress(f'[{index}/{total}] Detected title: "{article.title}"')
        except Exception as exc:
            print(f"error: failed to process {url}: {exc}", file=sys.stderr)
            return 1

    output = choose_output_path(
        args.output,
        default_output_name(articles, explicit_title=args.title),
    )
    book_title = args.title or (articles[0].title if len(articles) == 1 else None)
    if book_title:
        progress(f'Book title: "{book_title}"')
    else:
        progress("Book title: using Pandoc/default multi-article title behavior")
    progress(f"Output: {output}")
    progress(f"[build] Generating EPUB with Pandoc")

    path = build_epub(
        articles,
        output_path=output,
        book_title=args.title,
        language=args.language,
    )
    progress(f"[done] Wrote {path}")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
