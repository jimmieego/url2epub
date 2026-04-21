import io
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from unittest.mock import patch

from url2epub.cli import (
    ProgressReporter,
    doctor_check,
    format_batch_progress,
    format_duration,
    main,
    render_progress_bar,
)
from url2epub.core import Article


class FakeStream(io.StringIO):
    def __init__(self, is_tty: bool) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


class CliTests(unittest.TestCase):
    def test_render_progress_bar_reflects_completion(self) -> None:
        self.assertEqual(render_progress_bar(0, 4, width=8), "[--------]")
        self.assertEqual(render_progress_bar(2, 4, width=8), "[####----]")
        self.assertEqual(render_progress_bar(4, 4, width=8), "[########]")

    def test_format_batch_progress_includes_count_bar_and_elapsed_time(self) -> None:
        self.assertEqual(
            format_batch_progress(3, 10, 12.3),
            "3/10 complete [######--------------] (12.3s)",
        )

    def test_format_duration_uses_seconds_for_short_steps(self) -> None:
        self.assertEqual(format_duration(0.42), "0.4s")
        self.assertEqual(format_duration(12.34), "12.3s")

    def test_format_duration_uses_minutes_for_longer_steps(self) -> None:
        self.assertEqual(format_duration(73.2), "1m 13.2s")

    def test_progress_reporter_falls_back_to_plain_lines_when_not_a_tty(self) -> None:
        stream = FakeStream(is_tty=False)
        reporter = ProgressReporter(stream=stream)

        reporter.start("[1/2] Extracting https://example.com/1")
        reporter.stop('[1/2] Detected title: "Example"')

        self.assertEqual(
            stream.getvalue(),
            '[1/2] Extracting https://example.com/1\n[1/2] Detected title: "Example"\n',
        )

    def test_main_routes_bare_urls_to_convert(self) -> None:
        with patch("url2epub.cli.run_convert", return_value=17) as run_convert:
            result = main(["https://example.com/article"])

        self.assertEqual(result, 17)
        run_convert.assert_called_once()
        self.assertEqual(
            run_convert.call_args.args[0],
            Namespace(
                urls=["https://example.com/article"],
                output=None,
                title=None,
                language="en",
                timeout=20,
                allow_fallback=False,
            ),
        )

    def test_main_routes_convert_subcommand_to_convert(self) -> None:
        with patch("url2epub.cli.run_convert", return_value=23) as run_convert:
            result = main(["convert", "https://example.com/article"])

        self.assertEqual(result, 23)
        run_convert.assert_called_once()
        self.assertEqual(
            run_convert.call_args.args[0],
            Namespace(
                urls=["https://example.com/article"],
                output=None,
                title=None,
                language="en",
                timeout=20,
                allow_fallback=False,
            ),
        )

    def test_main_routes_doctor_subcommand(self) -> None:
        with patch("url2epub.cli.run_doctor", return_value=5) as run_doctor:
            result = main(["doctor"])

        self.assertEqual(result, 5)
        run_doctor.assert_called_once_with()

    def test_main_reports_elapsed_time_for_extract_and_build_steps(self) -> None:
        stderr = io.StringIO()
        stdout = io.StringIO()
        article = Article(
            title="Example",
            source_url="https://example.com/article",
            content_html="<p>Example</p>",
        )

        perf_counter_values = iter([10.0, 10.0, 11.25, 20.0, 20.0, 22.5])

        with patch("url2epub.cli.extract_url", return_value=article), patch(
            "url2epub.cli.build_epub",
            return_value="example.epub",
        ), patch(
            "url2epub.cli.choose_output_path",
            return_value="example.epub",
        ), patch(
            "url2epub.cli.default_output_name",
            return_value="example.epub",
        ), patch(
            "url2epub.cli.sys.stderr",
            stderr,
        ), patch(
            "url2epub.cli.time.perf_counter",
            side_effect=lambda: next(perf_counter_values),
        ), patch(
            "sys.stdout",
            stdout,
        ):
            result = main(["https://example.com/article"])

        self.assertEqual(result, 0)
        self.assertIn('[1/1] Detected title: "Example" (1.2s)', stderr.getvalue())
        self.assertIn("1/1 complete [####################] (10.0s)", stderr.getvalue())
        self.assertIn("[Done] Wrote example.epub (2.5s)", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "example.epub\n")

    def test_doctor_check_reports_broken_required_command(self) -> None:
        output = io.StringIO()
        with patch("url2epub.cli.probe_command", return_value=(False, None)):
            with redirect_stdout(output):
                result = doctor_check("Defuddle", ["npx", "--yes", "defuddle"])

        self.assertFalse(result)
        self.assertIn("[broken] Defuddle: npx --yes defuddle (required)", output.getvalue())

    def test_doctor_check_reports_broken_optional_command(self) -> None:
        output = io.StringIO()
        with patch("url2epub.cli.probe_command", return_value=(False, None)):
            with redirect_stdout(output):
                result = doctor_check(
                    "WeChat extractor",
                    ["wechat-article-to-markdown"],
                    required=False,
                )

        self.assertTrue(result)
        self.assertIn(
            "[broken] WeChat extractor: wechat-article-to-markdown (optional)",
            output.getvalue(),
        )

    def test_doctor_check_can_skip_probe_for_presence_only_command(self) -> None:
        output = io.StringIO()
        with patch("url2epub.cli.probe_command") as probe_command:
            with redirect_stdout(output):
                result = doctor_check(
                    "WeChat extractor",
                    ["wechat-article-to-markdown"],
                    required=False,
                    probe=False,
                )

        self.assertTrue(result)
        probe_command.assert_not_called()
        self.assertIn("[ok] WeChat extractor: wechat-article-to-markdown", output.getvalue())


if __name__ == "__main__":
    unittest.main()
