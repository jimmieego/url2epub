import io
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from unittest.mock import patch

from url2epub.cli import doctor_check, main


class CliTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
