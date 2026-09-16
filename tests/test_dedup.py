from datetime import datetime

from pcd.transform.dedup import Article, canonicalize_url, dedupe_articles, normalize_title


class TestCanonicalizeUrl:
    def test_scheme_is_ignored(self):
        assert canonicalize_url("http://example.com/a") == canonicalize_url("https://example.com/a")

    def test_www_is_stripped(self):
        assert canonicalize_url("https://www.example.com/a") == "example.com/a"

    def test_host_case_is_ignored(self):
        assert canonicalize_url("https://Example.COM/a") == "example.com/a"

    def test_trailing_slash_is_stripped(self):
        assert canonicalize_url("https://example.com/story/") == "example.com/story"

    def test_tracking_params_are_dropped(self):
        url = "https://example.com/story?utm_source=twitter&utm_campaign=x&fbclid=abc"
        assert canonicalize_url(url) == "example.com/story"

    def test_meaningful_params_are_kept(self):
        # some CMSs put the article id in the query string - dropping it would
        # merge genuinely different articles
        assert canonicalize_url("https://example.com/view?id=7") == "example.com/view?id=7"

    def test_param_order_does_not_matter(self):
        a = canonicalize_url("https://example.com/v?b=2&a=1")
        b = canonicalize_url("https://example.com/v?a=1&b=2")
        assert a == b

    def test_path_case_is_preserved(self):
        # some sites serve different content for different path casing
        assert canonicalize_url("https://example.com/Story") == "example.com/Story"

    def test_port_and_credentials_are_stripped(self):
        assert canonicalize_url("https://user:pw@example.com:443/a") == "example.com/a"


class TestNormalizeTitle:
    def test_lowercases_and_strips_punctuation(self):
        assert normalize_title("Anduril Raises $1.5B!") == "anduril raises 15b"

    def test_strips_outlet_suffix(self):
        assert normalize_title("Anduril raises funds - Reuters") == "anduril raises funds"
        assert normalize_title("Anduril raises funds | TechCrunch") == "anduril raises funds"

    def test_handles_none(self):
        assert normalize_title(None) == ""


class TestDedupeArticles:
    def test_collapses_tracking_param_variants(self):
        articles = [
            Article("https://a.com/story", "Big round", datetime(2026, 1, 1)),
            Article("https://www.a.com/story/?utm_source=x", "Big round", datetime(2026, 1, 1)),
        ]
        assert len(dedupe_articles(articles)) == 1

    def test_collapses_syndicated_wire_story(self):
        # same press release, different outlets, reworded headline
        articles = [
            Article("https://a.com/1", "Anduril raises $1.5 billion", datetime(2026, 1, 1)),
            Article("https://b.com/2", "Anduril Raises $1.5 Billion!", datetime(2026, 1, 2)),
            Article("https://c.com/3", "$1.5 billion raised by Anduril", datetime(2026, 1, 2)),
        ]
        assert len(dedupe_articles(articles)) == 1

    def test_keeps_earliest_published(self):
        articles = [
            Article("https://late.com/x", "Anduril raises money", datetime(2026, 1, 3)),
            Article("https://early.com/y", "Anduril raises money", datetime(2026, 1, 1)),
        ]
        kept = dedupe_articles(articles)
        assert len(kept) == 1
        assert kept[0].url == "https://early.com/y"

    def test_does_not_collapse_outside_syndication_window(self):
        # same headline months apart is probably a genuinely separate event
        articles = [
            Article("https://a.com/1", "Anduril raises money", datetime(2026, 1, 1)),
            Article("https://b.com/2", "Anduril raises money", datetime(2026, 6, 1)),
        ]
        assert len(dedupe_articles(articles)) == 2

    def test_does_not_collapse_different_stories(self):
        articles = [
            Article("https://a.com/1", "Anduril wins Army contract", datetime(2026, 1, 1)),
            Article("https://b.com/2", "ElevenLabs launches voice model", datetime(2026, 1, 1)),
        ]
        assert len(dedupe_articles(articles)) == 2

    def test_empty_input(self):
        assert dedupe_articles([]) == []
