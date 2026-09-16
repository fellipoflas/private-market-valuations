from pcd.transform.extract_valuation import (
    detect_round_stage,
    extract,
    looks_like_funding,
    parse_money,
)

B = 1_000_000_000
M = 1_000_000


class TestParseMoney:
    def test_billions(self):
        assert parse_money("3.7", "billion") == 3.7 * B
        assert parse_money("3.7", "B") == 3.7 * B
        assert parse_money("3.7", "bn") == 3.7 * B

    def test_millions(self):
        assert parse_money("600", "million") == 600 * M
        assert parse_money("600", "M") == 600 * M

    def test_strips_thousands_separators(self):
        assert parse_money("1,200", "million") == 1200 * M

    def test_bare_number_is_rejected(self):
        # "$500" with no scale word is almost never a funding figure
        assert parse_money("500", None) is None

    def test_garbage_is_rejected(self):
        assert parse_money("abc", "billion") is None


class TestExtract:
    def test_the_canonical_sentence(self):
        result = extract("Mach Industries raised $600 million at a $3.7 billion valuation.")
        assert result.amount_raised_usd == 600 * M
        assert result.post_money_usd == 3.7 * B

    def test_abbreviated_units(self):
        result = extract("The startup raised $600M at a $3.7B valuation.")
        assert result.amount_raised_usd == 600 * M
        assert result.post_money_usd == 3.7 * B

    def test_valued_at_phrasing(self):
        result = extract("Anduril is now valued at $30.5 billion after the round.")
        assert result.post_money_usd == 30.5 * B

    def test_values_the_company_at_phrasing(self):
        result = extract("The deal values the company at $14 billion.")
        assert result.post_money_usd == 14 * B

    def test_valuation_to_phrasing(self):
        # real headline from our first GDELT/NewsData test run - the "to" form
        # was missed by the original pattern
        result = extract(
            "Mach Industries doubles valuation to $3.7B in three months with fresh $600M round"
        )
        assert result.post_money_usd == 3.7 * B
        assert result.amount_raised_usd == 600 * M

    def test_takes_current_valuation_not_historical_comparison(self):
        # real GDELT sentence. Two valuations are stated: the new one and the
        # prior one it's being compared against. We must take the new one.
        result = extract(
            "In August, Databricks raised $5 billion at a $190 billion valuation, led by "
            "Coatue, up 41.8% from the company's last valuation of $134 billion, "
            "priced in February."
        )
        assert result.post_money_usd == 190 * B
        assert result.amount_raised_usd == 5 * B

    def test_raised_questions_is_not_funding(self):
        # real GDELT sentence - "raised" as an ordinary verb
        result = extract(
            "The IPO has raised questions about Dangote retaining significant ownership "
            "of the $20 billion refinery."
        )
        assert result.amount_raised_usd is None

    def test_raised_price_target_is_not_funding(self):
        result = extract("Guggenheim upgraded the stock to Buy and raised its price target to $95.")
        assert result.is_empty

    def test_government_contract_is_not_funding(self):
        # real headline - a $192M Army contract is revenue, not a funding round.
        # Getting this wrong would put fake rounds in the dataset.
        result = extract(
            "Palantir and Anduril awarded a $192 million US Army contract to build TITAN"
        )
        assert result.is_empty

    def test_hedged_numbers(self):
        result = extract("ElevenLabs raised roughly $180 million in new funding.")
        assert result.amount_raised_usd == 180 * M

    # --- the adversarial cases: these must NOT produce numbers ---

    def test_bare_company_size_is_not_a_raise(self):
        # "a $3.7 billion company" might mean revenue, market cap, anything
        result = extract("Investors are betting on a $3.7 billion company in the defense sector.")
        assert result.amount_raised_usd is None

    def test_unrelated_sentence_yields_nothing(self):
        result = extract("The company opened a new office in Costa Mesa.")
        assert result.is_empty

    def test_no_scale_word_yields_nothing(self):
        result = extract("The round was priced at $500 per share.")
        assert result.is_empty

    def test_raise_larger_than_valuation_is_rejected(self):
        # a company cannot raise more than it is worth afterwards - if we parsed
        # that, we mismatched the two figures, so we should keep neither
        result = extract("It raised $9 billion at a $2 billion valuation.")
        assert result.amount_raised_usd is None
        assert result.post_money_usd is None

    # --- flags ---

    def test_pre_money_is_flagged(self):
        result = extract("The company raised $100 million at a $1 billion pre-money valuation.")
        assert result.is_pre_money is True

    def test_post_money_is_not_flagged_as_pre(self):
        result = extract("The company raised $100 million at a $1 billion post-money valuation.")
        assert result.is_pre_money is False

    def test_dollar_us_prefix_form(self):
        # real GDELT sentence; "$US183 billion" is common in AU/CA outlets
        result = extract(
            "Anthropic, for instance, raised equity in September last year "
            "at a valuation of $US183 billion."
        )
        assert result.post_money_usd == 183 * B

    def test_hyphenated_scale_word(self):
        # real GDELT sentence: "raised EUR3-billion"
        result = extract("Mistral raised $3-billion in a round announced this month.")
        assert result.amount_raised_usd == 3 * B

    def test_non_usd_currency_is_detected(self):
        # we must not silently treat euros as dollars
        result = extract("Mistral AI raised €600 million at a €5.8 billion valuation.")
        assert result.currency == "EUR"


class TestDetectRoundStage:
    def test_series_letter(self):
        assert detect_round_stage("closed its Series C round") == "Series C"

    def test_seed(self):
        assert detect_round_stage("announced a seed round of $5 million") == "Seed"

    def test_pre_seed_beats_seed(self):
        assert detect_round_stage("raised a pre-seed round") == "Pre-Seed"

    def test_tender_offer(self):
        assert detect_round_stage("completed an employee tender offer") == "Tender Offer"

    def test_secondary(self):
        assert detect_round_stage("via a secondary sale of shares") == "Secondary"

    def test_none_when_absent(self):
        assert detect_round_stage("the company hired a new CFO") is None


class TestLooksLikeFunding:
    def test_requires_both_keyword_and_money(self):
        assert looks_like_funding("raised $600 million") is True
        assert looks_like_funding("raised a lot of money") is False   # no figure
        assert looks_like_funding("the $600 million building") is False  # no funding word

    def test_empty_string(self):
        assert looks_like_funding("") is False
