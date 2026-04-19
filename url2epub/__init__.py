"""Utilities for converting web pages into EPUB files."""

from .core import (
    Article,
    build_epub,
    default_output_name,
    extract_article,
    extract_url,
    fetch_html,
)

__all__ = [
    "Article",
    "build_epub",
    "default_output_name",
    "extract_article",
    "extract_url",
    "fetch_html",
]
