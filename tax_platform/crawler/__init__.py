from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.http_client import (
    create_session,
    ensure_trailing_slash,
    extract_meta_refresh_url,
    fetch_html,
    resolve_list_child_url,
)

__all__ = [
    "create_session",
    "ensure_trailing_slash",
    "extract_appointment_events",
    "extract_meta_refresh_url",
    "fetch_html",
    "parse_appointment_detail",
    "parse_appointment_list",
    "resolve_list_child_url",
]
