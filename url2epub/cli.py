from __future__ import annotations

import argparse
import shlex
from pathlib import Path
import subprocess
import sys

from .core import (
    build_epub,
    default_output_name,
    defuddle_command,
    extract_url,
    pandoc_command,
    wechat_tool_command,
)


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


def probe_command(command: list[str]) -> tuple[bool, str | None]:
    try:
        completed = subprocess.run(
            [*command, "--version"],
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
    except Exception:
        return False, None

    output = (completed.stdout or completed.stderr).strip()
    if not output:
        return True, None
    return True, output.splitlines()[0]


def doctor_check(label: str, command: list[str] | None, required: bool = True) -> bool:
    if not command:
        state = "missing"
        suffix = "required" if required else "optional"
        print(f"[{state}] {label} ({suffix})")
        return not required

    healthy, version = probe_command(command)
    rendered = shlex.join(command)
    if not healthy:
        state = "broken"
        suffix = "required" if required else "optional"
        print(f"[{state}] {label}: {rendered} ({suffix})")
        return not required
    if version:
        print(f"[ok] {label}: {rendered} ({version})")
    else:
        print(f"[ok] {label}: {rendered}")
    return True


def run_doctor() -> int:
    print("url2epub environment check")
    ok = True
    ok &= doctor_check("Pandoc", pandoc_command(), required=True)
    ok &= doctor_check("Defuddle", defuddle_command(), required=True)
    doctor_check("WeChat extractor", wechat_tool_command(), required=False)
    print("")
    if ok:
        print("Core dependencies are available.")
        print("WeChat support is optional and only needed for mp.weixin.qq.com URLs.")
        return 0

    print("One or more required dependencies are missing.")
    print("Install Pandoc and Defuddle before running URL conversions.")
    return 1


def run_convert(args: argparse.Namespace) -> int:
    if not args.urls:
        print("error: at least one URL is required", file=sys.stderr)
        return 2

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


def add_convert_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("urls", nargs="*", help="One or more HTTP(S) URLs.")
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


def build_root_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="url2epub",
        description="Fetch one or more URLs and package them into an EPUB.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("convert", "doctor"),
        help="Optional subcommand. Omit it to use the default convert behavior.",
    )
    return parser


def build_convert_parser(prog: str = "url2epub") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Fetch one or more URLs and package them into an EPUB.",
    )
    add_convert_arguments(parser)
    return parser


def build_doctor_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="url2epub doctor",
        description="Check whether Pandoc and extraction helpers are installed.",
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "doctor":
        build_doctor_parser().parse_args(argv[1:])
        return run_doctor()

    if argv and argv[0] == "convert":
        args = build_convert_parser("url2epub convert").parse_args(argv[1:])
        return run_convert(args)

    if argv and argv[0] in {"-h", "--help"}:
        build_root_parser().print_help()
        return 0

    args = build_convert_parser().parse_args(argv)
    return run_convert(args)


if __name__ == "__main__":
    raise SystemExit(main())
