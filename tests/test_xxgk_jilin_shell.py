"""Jilin vc_xxgkarea xxgk shell extraction."""

from tax_platform.crawler.xxgk_list import extract_xxgk_shell, find_xxgk_load_call


def test_jilin_vc_xxgkarea_shell_from_url_and_amp_iframe() -> None:
    html = (
        '<iframe src="/module/xxgk/tree.jsp?standardXxgk=1&amp;area=11220100MB1507524C'
        '&amp;divid=div4"></iframe>'
        "<script>"
        "loadDynamic('/module/xxgk/search.jsp?standardXxgk=1&infotypeId='+a+"
        "'&vc_title='+encodeURI(b)+'&vc_number='+encodeURI(c)+"
        "'&area=11220100MB1507524C', 'div4', '0','0','950','1','','');"
        "</script>"
    )
    url = (
        "https://jilin.chinatax.gov.cn/col/col13008/index.html"
        "?vc_xxgkarea=11220100MB1507524C&number="
    )
    shell = extract_xxgk_shell(html, url)
    assert shell is not None
    assert shell["area"] == "11220100MB1507524C"
    assert shell["divid"] == "div4"
    assert "tree.jsp" in shell["tree_url"]
    assert "&amp;" not in shell["tree_url"]


def test_jilin_load_dynamic_standard_xxgk_concat() -> None:
    html = (
        "loadDynamic('/module/xxgk/search.jsp?standardXxgk=1&infotypeId='+a+"
        "'&vc_title='+encodeURI(b)+'&vc_number='+encodeURI(c)+"
        "'&area=11220100MB1507524C', 'div4', '0','0','950','1','','');"
    )
    call = find_xxgk_load_call(html)
    assert call is not None
    path, divid, cid, webid = call
    assert "area=11220100MB1507524C" in path
    assert divid == "div4"
    assert webid == "1"


def test_zhejiang_style_load_dynamic_still_parses() -> None:
    html = (
        "loadDynamic('/module/xxgk/search.jsp?infotypeId=Z2401'+a+"
        "'&vc_title='+encodeURI(b)+'&vc_number='+encodeURI(c)+"
        "'&area=AREA123', 'divID', '0','1000','UID','WEBID', '', '');"
    )
    call = find_xxgk_load_call(html)
    assert call is not None
    path, divid, cid, webid = call
    assert path.startswith("/module/xxgk/search.jsp?infotypeId=Z2401")
    assert "area=AREA123" in path
    assert divid == "divID"
    assert webid == "WEBID"
