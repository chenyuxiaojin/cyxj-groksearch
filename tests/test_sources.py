from grok_search.sources import (
    split_answer_and_sources, merge_sources, extract_unique_urls,
)


def test_heading_sources():
    text = "答案正文。\n\n## Sources\n- [标题A](https://a.com)\n- [标题B](https://b.com)"
    answer, sources = split_answer_and_sources(text)
    assert answer == "答案正文。"
    assert {s["url"] for s in sources} == {"https://a.com", "https://b.com"}


def test_function_call_sources():
    text = '正文内容。\ncitation_card([{"url": "https://x.com", "title": "X"}])'
    answer, sources = split_answer_and_sources(text)
    assert answer == "正文内容。"
    assert sources[0]["url"] == "https://x.com"


def test_no_sources_returns_full_text():
    text = "纯答案，没有任何信源。"
    answer, sources = split_answer_and_sources(text)
    assert answer == text
    assert sources == []


def test_merge_dedup():
    merged = merge_sources(
        [{"url": "https://a.com", "title": "A"}],
        [{"url": "https://a.com"}, {"url": "https://b.com"}],
    )
    assert [s["url"] for s in merged] == ["https://a.com", "https://b.com"]


def test_extract_unique_urls_strips_trailing_punct():
    urls = extract_unique_urls("见 https://a.com。 和 https://b.com, 还有 https://a.com")
    assert urls == ["https://a.com", "https://b.com"]
