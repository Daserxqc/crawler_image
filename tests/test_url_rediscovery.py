from tax_platform.config.url_rediscovery import collect_url_candidates, rediscover_bureau_url


def test_henan_collects_stored_candidates() -> None:
    cands = collect_url_candidates("henan", db_path="output/tax_hr.db")
    assert cands
    assert any("xxgk/rsgl/rsrm" in c.url for c in cands)


def test_shenzhen_collects_registry_and_ingest() -> None:
    cands = collect_url_candidates("guangdong_shenzhen", db_path="output/tax_hr.db")
    assert any("zfxxgk_zdgk_list.shtml" in c.url for c in cands)


def test_rediscover_henan_validates(monkeypatch) -> None:
    def fake_validate(url: str, *, timeout: int = 20):
        if "rsgl/rsrm" in url:
            return True, 15, url
        return False, 0, "404"

    monkeypatch.setattr(
        "tax_platform.config.url_rediscovery.validate_list_url",
        fake_validate,
    )
    r = rediscover_bureau_url("henan", db_path="output/tax_hr.db")
    assert r.validated_url
    assert "rsgl/rsrm" in r.validated_url
    assert not r.needs_user
