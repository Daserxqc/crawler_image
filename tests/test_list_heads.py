"""Unit tests for appointment list-heads compare / collect logic (no network)."""

from __future__ import annotations

from datetime import date

from tax_platform.crawler.appointment_list import AppointmentListItem
from tax_platform.crawler.list_heads_job import collect_new_since_baseline, pick_top_heads
from tax_platform.store.list_heads import is_newer_than


def test_is_newer_than():
    assert is_newer_than("2026-09-01", "2026-08-31")
    assert not is_newer_than("2026-08-31", "2026-08-31")
    assert not is_newer_than("2026-08-30", "2026-08-31")
    assert not is_newer_than(None, "2026-08-31")
    assert is_newer_than("2026-09-01", None)


def test_pick_top_heads_prefers_dates():
    items = [
        AppointmentListItem("旧", "http://a/1", date(2024, 1, 1)),
        AppointmentListItem("无日期", "http://a/2", None),
        AppointmentListItem("新", "http://a/3", date(2026, 8, 1)),
    ]
    heads = pick_top_heads(items, limit=2)
    assert [h.source_url for h in heads] == ["http://a/3", "http://a/1"]
    assert heads[0].published_on == "2026-08-01"


def test_collect_walks_until_baseline_url():
    items = [
        AppointmentListItem("新3", "http://a/3", date(2026, 8, 3)),
        AppointmentListItem("新2", "http://a/2", date(2026, 8, 2)),
        AppointmentListItem("旧头", "http://a/1", date(2026, 8, 1)),
        AppointmentListItem("更旧", "http://a/0", date(2026, 7, 1)),
    ]
    got = collect_new_since_baseline(
        items,
        baseline_date="2026-08-01",
        baseline_url="http://a/1",
    )
    assert [x.source_url for x in got] == ["http://a/3", "http://a/2"]


def test_collect_skip_when_top_is_baseline():
    items = [
        AppointmentListItem("旧头", "http://a/1", date(2026, 8, 1)),
        AppointmentListItem("更旧", "http://a/0", date(2026, 7, 1)),
    ]
    assert (
        collect_new_since_baseline(
            items,
            baseline_date="2026-08-01",
            baseline_url="http://a/1",
        )
        == []
    )


def test_collect_same_day_new_above_baseline():
    items = [
        AppointmentListItem("同日新", "http://a/2", date(2026, 8, 1)),
        AppointmentListItem("旧头", "http://a/1", date(2026, 8, 1)),
    ]
    got = collect_new_since_baseline(
        items,
        baseline_date="2026-08-01",
        baseline_url="http://a/1",
    )
    assert [x.source_url for x in got] == ["http://a/2"]


def test_list_heads_updates_roundtrip(tmp_path):
    heads_db = tmp_path / "heads.db"
    from tax_platform.store.list_heads import (
        ListHead,
        connect_list_heads,
        list_waiting_updates,
        load_heads,
        mark_updates_ingested,
        replace_bureau_updates,
        replace_heads,
    )

    conn = connect_list_heads(heads_db, main_db_path=tmp_path / "missing.db")
    replace_heads(
        conn,
        "pdtax",
        [
            ListHead(1, "t1", "http://x/1", "2026-08-01"),
            ListHead(2, "t2", "http://x/2", "2026-07-01"),
        ],
        has_update=True,
    )
    replace_bureau_updates(
        conn,
        "pdtax",
        [
            {"title": "n1", "source_url": "http://x/9", "published_on": "2026-08-10"},
            {"title": "n2", "source_url": "http://x/8", "published_on": "2026-08-09"},
            {"title": "n3", "source_url": "http://x/7", "published_on": "2026-08-08"},
        ],
    )
    conn.commit()
    assert len(load_heads(conn, "pdtax")) == 2
    assert len(list_waiting_updates(conn)) == 3
    mark_updates_ingested(conn, ["pdtax"])
    conn.commit()
    assert list_waiting_updates(conn) == []
    n = conn.execute("SELECT COUNT(*) FROM appointment_list_updates").fetchone()[0]
    assert n == 3
    conn.close()


def test_scan_failure_mark_and_clear(tmp_path):
    from tax_platform.store.list_heads import (
        clear_list_scan_failure,
        connect_list_heads,
        list_open_scan_failures,
        record_list_scan_failure,
    )

    conn = connect_list_heads(tmp_path / "heads.db", main_db_path=tmp_path / "m.db")
    record_list_scan_failure(conn, "beijing", list_url="http://x", error="HTTP 412")
    conn.commit()
    open_rows = list_open_scan_failures(conn)
    assert len(open_rows) == 1
    assert open_rows[0]["bureau_code"] == "beijing"
    assert open_rows[0]["retry_count"] == 0

    record_list_scan_failure(conn, "beijing", list_url="http://x", error="HTTP 412 again")
    conn.commit()
    assert list_open_scan_failures(conn)[0]["retry_count"] == 1

    clear_list_scan_failure(conn, "beijing")
    conn.commit()
    assert list_open_scan_failures(conn) == []
    conn.close()


def test_merge_and_retry_replaces_failed(monkeypatch, tmp_path):
    from tax_platform.crawler.list_heads_job import (
        ListHeadScanResult,
        merge_scan_results,
        retry_failed_list_heads,
    )
    from tax_platform.store.list_heads import connect_list_heads, record_list_scan_failure

    first = [
        ListHeadScanResult(bureau="a", list_url="", list_count=1),
        ListHeadScanResult(bureau="b", list_url="", list_count=0, error="boom"),
    ]
    second = [
        ListHeadScanResult(bureau="b", list_url="", list_count=2, seeded=True),
    ]
    merged = merge_scan_results(first, second)
    assert [r.bureau for r in merged] == ["a", "b"]
    assert merged[1].error is None
    assert merged[1].seeded

    conn = connect_list_heads(tmp_path / "heads.db", main_db_path=tmp_path / "m.db")
    record_list_scan_failure(conn, "b", error="boom")
    conn.commit()

    calls = []

    def fake_scan(heads_conn, code, **kwargs):
        calls.append(code)
        return ListHeadScanResult(bureau=code, list_url="", list_count=1, seeded=True)

    monkeypatch.setattr(
        "tax_platform.crawler.list_heads_job.scan_bureau_list_heads",
        fake_scan,
    )
    out = retry_failed_list_heads(conn, first, delay=0)
    assert calls == ["b"]
    assert out[1].seeded
    conn.close()


def test_load_html_raises_when_strict(monkeypatch):
    from tax_platform.crawler import appointment_job as job

    monkeypatch.setattr(job, "_fetch_qxtax_zwgk_list_html", lambda *a, **k: None)

    def boom(*a, **k):
        raise RuntimeError("HTTP 412")

    monkeypatch.setattr(job, "fetch_html", boom)

    class Sess:
        pass

    try:
        job._load_appointment_list_html(Sess(), "http://example.test/list", raise_on_fetch_fail=True)
        assert False, "expected raise"
    except RuntimeError as exc:
        assert "list fetch failed" in str(exc)

    url, html = job._load_appointment_list_html(
        Sess(), "http://example.test/list", raise_on_fetch_fail=False
    )
    assert url.endswith("/list")
    assert html == ""
