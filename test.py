"""Tests for the AI Market Compare app.

Run:  python test.py        (or: make test)

Uses only the standard library. Wikipedia calls are mocked, so the tests run
offline and give the same result every time.
"""
import math
import unittest
from unittest import mock

import requests

import app as m


def fake_wiki_response(title="Nvidia", extract="Nvidia is a technology company.", pageid=1):
    """A stand-in for requests.get() that returns a Wikipedia-style payload."""
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "query": {"pages": {str(pageid): {"pageid": pageid, "title": title, "extract": extract}}}
    }
    return response


class FormattingTests(unittest.TestCase):
    def test_money_scales(self):
        self.assertEqual(m.money(5.57e12), "$5.57T")
        self.assertEqual(m.money(41.1e9), "$41.1B")
        self.assertEqual(m.money(350e6), "$350.0M")
        self.assertEqual(m.money(5e3), "$5.00K")
        self.assertEqual(m.money(500), "$500")

    def test_price_text(self):
        self.assertEqual(m.price_text(203.456), "$203.46")
        self.assertEqual(m.price_text(0.156749), "$0.1567")

    def test_shares_text(self):
        self.assertEqual(m.shares_text(24.15e9), "24.15B")
        self.assertEqual(m.shares_text(12.5e6), "12.50M")
        self.assertEqual(m.shares_text(800), "800")

    def test_tier_boundaries(self):
        self.assertEqual(m.tier(200e9), "mega cap")
        self.assertEqual(m.tier(199.9e9), "large cap")
        self.assertEqual(m.tier(10e9), "large cap")
        self.assertEqual(m.tier(1e9), "mid cap")
        self.assertEqual(m.tier(100e6), "small cap")
        self.assertEqual(m.tier(99.9e6), "micro cap")

    def test_ratio_text(self):
        self.assertEqual(m.ratio_text(135.4), "135×")
        self.assertEqual(m.ratio_text(2.46), "2.5×")


class DataTests(unittest.TestCase):
    def test_csv_loaded(self):
        self.assertEqual(m.COUNT, 77)
        self.assertEqual(len(m.COMPANIES), m.COUNT)

    def test_symbols_are_unique(self):
        self.assertEqual(len(m.BY_SYMBOL), m.COUNT)

    def test_total_matches_sum_of_rows(self):
        self.assertAlmostEqual(m.TOTAL_CAP, sum(c["market_cap"] for c in m.COMPANIES))

    def test_default_symbols_exist(self):
        self.assertIn(m.DEFAULT_A, m.BY_SYMBOL)
        self.assertIn(m.DEFAULT_B, m.BY_SYMBOL)

    def test_resolve_is_forgiving(self):
        self.assertEqual(m.resolve("nvda")["symbol"], "NVDA")
        self.assertEqual(m.resolve("  NVDA ")["symbol"], "NVDA")
        self.assertIsNone(m.resolve("NOPE"))
        self.assertIsNone(m.resolve(""))
        self.assertIsNone(m.resolve(None))


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.nvda = m.BY_SYMBOL["NVDA"]
        self.crwv = m.BY_SYMBOL["CRWV"]

    def test_biggest_company_gets_full_radius(self):
        biggest = max(m.COMPANIES, key=lambda c: c["market_cap"])
        self.assertAlmostEqual(m.profile(biggest)["radius"], m.R_MAX)

    def test_radius_scales_with_square_root_of_market_cap(self):
        # Area (not radius) should be proportional to market cap.
        pn, pc = m.profile(self.nvda), m.profile(self.crwv)
        expected = math.sqrt(pc["market_cap"] / pn["market_cap"])
        self.assertAlmostEqual(pc["radius"] / pn["radius"], expected, places=6)

    def test_tiny_companies_keep_a_minimum_radius(self):
        smallest = min(m.COMPANIES, key=lambda c: c["market_cap"])
        self.assertEqual(m.profile(smallest)["radius"], 2.5)

    def test_shares_of_total_add_up_to_100(self):
        total = sum(m.profile(c)["share_pct"] for c in m.COMPANIES)
        self.assertAlmostEqual(total, 100.0, places=6)

    def test_headline_is_the_same_whichever_order_is_chosen(self):
        first = m.compare(self.nvda, self.crwv)["insights"][0]
        second = m.compare(self.crwv, self.nvda)["insights"][0]
        self.assertEqual(first, second)
        self.assertTrue(first.startswith(self.nvda["name"]))

    def test_insights_cover_ratio_share_rank_tier_and_country(self):
        result = m.compare(self.nvda, self.crwv)
        self.assertEqual(len(result["insights"]), 5)
        self.assertIn("combined", result["insights"][1])
        self.assertIn("ranks", result["insights"][2])

    def test_same_tier_and_same_country_wording(self):
        a, b = m.BY_SYMBOL["NVDA"], m.BY_SYMBOL["AAPL"]
        insights = m.compare(a, b)["insights"]
        self.assertIn("Both are mega cap companies.", insights)
        self.assertIn(f"Both are based in {a['country']}.", insights)

    def test_rank_gap_wording_is_grammatical(self):
        one_apart = m.compare(m.BY_SYMBOL["NVDA"], m.BY_SYMBOL["AAPL"])["insights"][2]
        self.assertIn("1 place apart", one_apart)
        many_apart = m.compare(self.nvda, self.crwv)["insights"][2]
        self.assertIn("places apart", many_apart)

    def test_different_tier_wording(self):
        insights = m.compare(self.nvda, self.crwv)["insights"]
        self.assertTrue(any("different size tiers" in line for line in insights))

    def test_google_link_contains_both_names(self):
        url = m.compare(self.nvda, self.crwv)["google_url"]
        self.assertTrue(url.startswith("https://www.google.com/search?q="))
        self.assertIn("NVIDIA", url)
        self.assertIn("CoreWeave", url)


class WikipediaLookupTests(unittest.TestCase):
    def setUp(self):
        m._blurb_cache.clear()  # pylint: disable=protected-access

    def test_success_returns_text_title_and_url(self):
        with mock.patch.object(m.requests, "get", return_value=fake_wiki_response()):
            blurb = m.fetch_blurb("NVIDIA")
        self.assertEqual(blurb["title"], "Nvidia")
        self.assertEqual(blurb["text"], "Nvidia is a technology company.")
        self.assertIn("curid=1", blurb["url"])

    def test_brackets_are_stripped_from_the_search_term(self):
        with mock.patch.object(m.requests, "get", return_value=fake_wiki_response()) as get:
            m.fetch_blurb("Alphabet (Google)")
        self.assertEqual(get.call_args.kwargs["params"]["gsrsearch"], "Alphabet company")

    def test_success_is_cached(self):
        with mock.patch.object(m.requests, "get", return_value=fake_wiki_response()) as get:
            m.fetch_blurb("NVIDIA")
            m.fetch_blurb("NVIDIA")
        self.assertEqual(get.call_count, 1)

    def test_network_error_returns_none_and_is_not_cached(self):
        with mock.patch.object(m.requests, "get", side_effect=requests.RequestException("down")):
            self.assertIsNone(m.fetch_blurb("NVIDIA"))
        with mock.patch.object(m.requests, "get", return_value=fake_wiki_response()):
            self.assertIsNotNone(m.fetch_blurb("NVIDIA"))  # retried, not stuck on the failure

    def test_empty_result_returns_none(self):
        empty = mock.Mock()
        empty.raise_for_status.return_value = None
        empty.json.return_value = {"batchcomplete": ""}
        with mock.patch.object(m.requests, "get", return_value=empty):
            self.assertIsNone(m.fetch_blurb("Nothing Here"))

    def test_malformed_json_returns_none(self):
        bad = mock.Mock()
        bad.raise_for_status.return_value = None
        bad.json.side_effect = ValueError("not json")
        with mock.patch.object(m.requests, "get", return_value=bad):
            self.assertIsNone(m.fetch_blurb("NVIDIA"))


class RouteTests(unittest.TestCase):
    def setUp(self):
        m._blurb_cache.clear()  # pylint: disable=protected-access
        self.client = m.app.test_client()

    def get_page(self, url, wiki=None):
        """Fetch a page with Wikipedia mocked (success by default)."""
        patcher = (
            mock.patch.object(m.requests, "get", return_value=wiki or fake_wiki_response())
        )
        with patcher:
            return self.client.get(url)

    def test_home_page_loads_with_default_comparison(self):
        response = self.get_page("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("NVIDIA is about", html)
        self.assertIn("What the numbers say", html)

    def test_dropdowns_list_every_company(self):
        html = self.get_page("/").get_data(as_text=True)
        self.assertEqual(html.count('value="NVDA"'), 2)  # once per dropdown

    def test_chosen_companies_are_preselected(self):
        html = self.get_page("/?a=AAPL&b=MSFT").get_data(as_text=True)
        self.assertIn('<option value="AAPL" selected>', html)
        self.assertIn('<option value="MSFT" selected>', html)

    def test_description_and_source_link_are_shown(self):
        html = self.get_page("/?a=NVDA&b=AAPL").get_data(as_text=True)
        self.assertIn("Nvidia is a technology company.", html)
        self.assertIn("From Wikipedia", html)

    def test_page_still_works_when_wikipedia_is_down(self):
        with mock.patch.object(m.requests, "get", side_effect=requests.RequestException("down")):
            response = self.client.get("/?a=NVDA&b=AAPL")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("load a summary right now", html)
        self.assertIn("google.com/search", html)

    def test_same_company_shows_error(self):
        html = self.get_page("/?a=AAPL&b=AAPL").get_data(as_text=True)
        self.assertIn("Pick two different companies.", html)

    def test_unknown_symbol_shows_error(self):
        html = self.get_page("/?a=NOPE&b=AAPL").get_data(as_text=True)
        self.assertIn("Pick two companies from the lists.", html)

    def test_data_date_note_is_always_shown(self):
        for url in ("/", "/?a=AAPL&b=AAPL", "/?a=NOPE&b=AAPL"):
            with self.subTest(url=url):
                html = self.get_page(url).get_data(as_text=True)
                self.assertIn("as at 1 October 2026", html)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = m.app.test_client()

    def test_compare_returns_json_analysis(self):
        response = self.client.get("/api/compare?a=NVDA&b=AAPL")
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual({"a", "b", "insights", "google_url"}, set(data))
        self.assertEqual(data["a"]["symbol"], "NVDA")
        self.assertEqual(data["b"]["symbol"], "AAPL")

    def test_api_does_not_call_wikipedia(self):
        with mock.patch.object(m.requests, "get") as get:
            self.client.get("/api/compare?a=NVDA&b=AAPL")
        get.assert_not_called()

    def test_missing_same_or_unknown_symbols_return_400(self):
        for url in (
            "/api/compare",
            "/api/compare?a=NVDA",
            "/api/compare?a=NVDA&b=NVDA",
            "/api/compare?a=NVDA&b=NOPE",
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())


if __name__ == "__main__":
    unittest.main(verbosity=2)
