from pcd.settings import load_companies
from pcd.transform.match_company import find_mentions, match_companies, mentions, primary_company

COMPANIES = load_companies()


def company(cid):
    return next(c for c in COMPANIES if c.id == cid)


class TestMentions:
    def test_matches_display_name(self):
        assert mentions("Anduril won a contract", company("anduril")) is True

    def test_matches_alias(self):
        assert mentions("Eleven Labs launched a model", company("elevenlabs")) is True

    def test_is_case_insensitive(self):
        assert mentions("DATABRICKS raised money", company("databricks")) is True

    def test_respects_word_boundaries(self):
        # "Ramp" must not match inside "rampant" - it's one of our worst
        # false-positive names
        assert mentions("rampant speculation in the market", company("ramp")) is False

    def test_exclude_terms_veto_a_match(self):
        # Anduril is also Aragorn's sword
        text = "Anduril, the sword of Aragorn, was reforged from Narsil"
        assert mentions(text, company("anduril")) is False

    def test_exclude_terms_do_not_veto_legitimate_news(self):
        text = "Anduril raised $2.5 billion led by Founders Fund"
        assert mentions(text, company("anduril")) is True

    def test_empty_text(self):
        assert mentions("", company("anduril")) is False


class TestPrimaryCompany:
    def test_subject_is_earliest_mentioned(self):
        # the real GDELT sentence that exposed this bug: OpenAI appears only as
        # a descriptor, but the $65B belongs to Anthropic
        text = (
            "Anthropic, the company founded by former OpenAI researchers, "
            "raised $65 billion at a $965 billion valuation."
        )
        assert primary_company(text, COMPANIES) == "anthropic"
        # OpenAI is still genuinely mentioned - it just isn't the subject
        assert set(match_companies(text, COMPANIES)) == {"anthropic", "openai"}

    def test_single_company(self):
        assert primary_company("Databricks raised $5 billion", COMPANIES) == "databricks"

    def test_none_when_no_match(self):
        assert primary_company("The weather in Ohio was mild", COMPANIES) is None


class TestFindMentions:
    def test_returns_positions_in_order(self):
        text = "Anthropic and Databricks both raised capital"
        found = find_mentions(text, COMPANIES)
        assert [cid for cid, _ in found] == ["anthropic", "databricks"]
        assert found[0][1] < found[1][1]
