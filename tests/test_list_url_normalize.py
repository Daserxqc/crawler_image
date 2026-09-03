from tax_platform.config.list_heads_buckets import filter_scan_codes, is_deferred_list_head
from tax_platform.config.list_url_normalize import normalize_list_url


def test_normalize_strips_right_html_trailing_slash() -> None:
    raw = "http://hebei.chinatax.gov.cn/hbswxxgk/sjz/zdml/1370/1512/1413/right.html/"
    assert normalize_list_url(raw).endswith("right.html")
    assert not normalize_list_url(raw).endswith("/")


def test_normalize_jilin_https_to_http() -> None:
    raw = "https://jilin.chinatax.gov.cn/col/col13008/index.html?vc_xxgkarea=x"
    out = normalize_list_url(raw)
    assert out.startswith("http://")
    assert "jilin.chinatax.gov.cn" in out


def test_normalize_shtml_no_trailing_slash() -> None:
    raw = "https://shenzhen.chinatax.gov.cn/sztax/zdgkml/zsjs/rsjy/gkmlrsrm/zfxxgk_zdgk_list.shtml/"
    assert normalize_list_url(raw).endswith(".shtml")
    assert not normalize_list_url(raw).endswith("/")


def test_is_html_file_path() -> None:
    from tax_platform.config.list_url_normalize import is_html_file_path

    assert is_html_file_path("foo.shtml")
    assert is_html_file_path("foo.html/")
    assert not is_html_file_path("foo/rsrm/")


def test_deferred_hubei_and_jilin() -> None:
    assert is_deferred_list_head("hubei_hbsw_wuhan")
    assert is_deferred_list_head("jilin")
    assert not is_deferred_list_head("jilin_col822")
    kept, skipped = filter_scan_codes(["jilin", "jilin_col822", "hubei_hbsw_wuhan", "hebei_sjzsw"])
    assert kept == ["jilin_col822", "hebei_sjzsw"]
    assert skipped == ["jilin", "hubei_hbsw_wuhan"]
