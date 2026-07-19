from collector.normalize import parse_notice_elements


def test_parse_table_row():
    html = """
    <table><tbody><tr>
      <td>1</td><td>행복주택</td>
      <td><a href="/notice/123">고양시 행복주택 신혼부부 예비입주자 모집공고</a></td>
      <td>경기도 고양시</td><td>2026.07.18</td><td>2026.07.28</td><td>접수중</td>
    </tr></tbody></table>
    """
    items = parse_notice_elements(html, "LH", "https://example.com/list")
    assert len(items) == 1
    item = items[0]
    assert item.noticeType == "행복주택"
    assert "신혼부부" in item.targetGroups
    assert "고양시" in item.regions
    assert item.publishedAt == "2026-07-18"
    assert item.applyEnd == "2026-07-28"
    assert item.status == "접수중"
    assert item.officialUrl == "https://example.com/notice/123"


def test_excludes_non_resident_procurement():
    html = """
    <table><tbody><tr><td><a href="/x">행복주택 민간사업자 모집 공고</a></td><td>2026.07.18</td></tr></tbody></table>
    """
    assert parse_notice_elements(html, "GH", "https://example.com") == []
