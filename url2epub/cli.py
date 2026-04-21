from __future__ import annotations

import argparse
import shlex
from pathlib import Path
import threading
import time
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


class ProgressReporter:
    def __init__(self, stream: object | None = None) -> None:
        self.stream = sys.stderr if stream is None else stream
        self.enabled = bool(
            hasattr(self.stream, "isatty") and self.stream.isatty() and hasattr(self.stream, "write")
        )
        self._frames = ["|", "/", "-", "\\"]
        self._message = ""
        self._frame_index = 0
        self._active = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def info(self, message: str) -> None:
        if self.enabled:
            self._clear_line()
        print(message, file=self.stream)

    def start(self, message: str) -> None:
        if not self.enabled:
            self.info(message)
            return

        self.stop()
        self._message = message
        self._frame_index = 0
        self._active = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def update(self, message: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._message = message

    def stop(self, final_message: str | None = None) -> None:
        thread = self._thread
        if thread is not None:
            self._stop_event.set()
            thread.join()
            self._thread = None

        was_active = self._active
        self._active = False
        if self.enabled and was_active:
            self._clear_line()

        if final_message:
            print(final_message, file=self.stream)

    def _spin(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                frame = self._frames[self._frame_index % len(self._frames)]
                message = self._message
                self._frame_index += 1
            self.stream.write(f"\r\033[2K{frame} {message}")
            self.stream.flush()
            time.sleep(0.1)

    def _clear_line(self) -> None:
        self.stream.write("\r\033[2K")
        self.stream.flush()


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{seconds:.1f}s"

    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:.1f}s"

    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}h {minutes}m"


def render_progress_bar(completed: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return "[--------------------]"

    filled = round((completed / total) * width)
    filled = max(0, min(width, filled))
    return f"[{'#' * filled}{'-' * (width - filled)}]"


def format_batch_progress(completed: int, total: int, elapsed_seconds: float) -> str:
    return f"{completed}/{total} complete {render_progress_bar(completed, total)} ({format_duration(elapsed_seconds)})"


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


def doctor_check(
    label: str,
    command: list[str] | None,
    required: bool = True,
    probe: bool = True,
) -> bool:
    if not command:
        state = "missing"
        suffix = "required" if required else "optional"
        print(f"[{state}] {label} ({suffix})")
        return not required

    rendered = shlex.join(command)
    if not probe:
        print(f"[ok] {label}: {rendered}")
        return True

    healthy, version = probe_command(command)
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
    doctor_check("WeChat extractor", wechat_tool_command(), required=False, probe=False)
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

    reporter = ProgressReporter()
    articles = []
    total = len(args.urls)
    batch_started_at = time.perf_counter()
    for index, url in enumerate(args.urls, start=1):
        prefix = f"[{index}/{total}]"
        reporter.start(f"{prefix} Extracting {url}")
        started_at = time.perf_counter()
        try:
            article = extract_url(
                url,
                timeout=args.timeout,
                allow_fallback=args.allow_fallback,
            )
            articles.append(article)
            elapsed = format_duration(time.perf_counter() - started_at)
            reporter.stop(f'{prefix} Detected title: "{article.title}" ({elapsed})')
            batch_elapsed = time.perf_counter() - batch_started_at
            reporter.info(format_batch_progress(index, total, batch_elapsed))
        except Exception as exc:
            elapsed = format_duration(time.perf_counter() - started_at)
            reporter.stop()
            print(f"error: failed to process {url} after {elapsed}: {exc}", file=sys.stderr)
            return 1

    output = choose_output_path(
        args.output,
        default_output_name(articles, explicit_title=args.title),
    )
    book_title = args.title or (articles[0].title if len(articles) == 1 else None)
    if book_title:
        reporter.info(f'Book title: "{book_title}"')
    else:
        reporter.info("Book title: using Pandoc/default multi-article title behavior")
    reporter.info(f"Output: {output}")
    reporter.start("[build] Generating EPUB with Pandoc")
    build_started_at = time.perf_counter()

    path = build_epub(
        articles,
        output_path=output,
        book_title=args.title,
        language=args.language,
    )
    build_elapsed = format_duration(time.perf_counter() - build_started_at)
    reporter.stop(f"[Done] Wrote {path} ({build_elapsed})")
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
