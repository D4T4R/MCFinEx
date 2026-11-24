"""What a tag is allowed to be, and when a company gets asked again.

The regex prototype this replaces was wrong about one company in three, and the
failures were the readable kind: "Incorporated in 1999" rendered as a product.
These tests pin the floor under the model's reply -- the prompt asks for these
rules, and this is what happens when the reply does not follow them.
"""

from __future__ import annotations

import json

import pytest

from mcfinex.tagging import (
    MAX_TAGS, Tags, clean, digest, needs_tagging, request_for, tags_from_reply,
)


class TestClean:
    def test_it_keeps_a_plain_product(self):
        assert clean(["Casual garments", "Accessories"]) == (
            "Casual garments", "Accessories")

    @pytest.mark.parametrize("junk", [
        "Incorporated in 1999",      # the regex prototype's worst output
        "Leading end-to-end",        # self-image, not a product
        "One of India's largest",
        "Established in 2004",
        "Part of the Tata group",
        "Flagship company",
    ])
    def test_it_rejects_self_description(self, junk):
        assert clean([junk]) == ()

    def test_it_rejects_anything_with_a_digit(self):
        # Years are the common case, but a model that answers "5G equipment"
        # is also describing a standard rather than naming a product line.
        assert clean(["Founded 1987", "3D printing"]) == ()

    def test_it_rejects_a_tag_too_long_for_a_chip(self):
        assert clean(["A" * 40]) == ()

    def test_it_rejects_fragments(self):
        assert clean(["", "  ", "ab"]) == ()

    def test_it_caps_the_count(self):
        many = [f"Product {chr(c)}" for c in range(ord("a"), ord("z"))]
        # (these carry no digits, so only the cap should bite)
        assert len(clean(many)) == MAX_TAGS

    def test_duplicates_collapse_case_insensitively(self):
        assert clean(["Textiles", "textiles", "TEXTILES"]) == ("Textiles",)

    def test_non_strings_are_ignored_rather_than_crashing(self):
        # A model that answers with nested objects must not take the run down.
        assert clean(["Valves", None, 42, {"a": 1}, ["b"]]) == ("Valves",)

    def test_punctuation_and_whitespace_are_trimmed(self):
        assert clean(["  speciality   chemicals . "]) == ("Speciality chemicals",)


class TestReply:
    def test_it_reads_the_requested_shape(self):
        got = tags_from_reply('{"tags": ["Valves", "Pumps"]}', "makes valves and pumps")
        assert got.tags == ("Valves", "Pumps")

    def test_a_reply_that_is_not_json_yields_no_tags(self):
        """One unreadable company must not fail a batch of two thousand."""
        got = tags_from_reply("I'm sorry, I can't help with that.", "whatever")
        assert got.tags == ()

    def test_a_reply_of_the_wrong_shape_yields_no_tags(self):
        assert tags_from_reply('{"tags": "Valves"}', "x").tags == ()
        assert tags_from_reply('{"other": []}', "x").tags == ()

    def test_the_filter_still_applies_to_a_well_formed_reply(self):
        # The schema constrains the shape, not the content.
        got = tags_from_reply('{"tags": ["Incorporated in 1999"]}', "x")
        assert got.tags == ()


class TestStaleness:
    def test_a_company_never_tagged_is_offered(self):
        assert needs_tagging("makes valves", None) is True

    def test_a_tagged_company_is_not_offered_again(self):
        stored = Tags(("Valves",), digest("makes valves")).as_json()
        assert needs_tagging("makes valves", stored) is False

    def test_a_rewritten_description_makes_the_tags_stale(self):
        """The reason tags carry a digest at all.

        A re-scrape can replace the description; tags read from the old one are
        then a claim about text that no longer exists. Keyed to the source, that
        is detectable instead of quietly wrong.
        """
        stored = Tags(("Valves",), digest("makes valves")).as_json()
        assert needs_tagging("now makes pumps instead", stored) is True

    def test_whitespace_alone_does_not_make_tags_stale(self):
        # Re-scraping can reflow the text without changing a word; re-tagging
        # the universe over that would be pure spend.
        stored = Tags(("Valves",), digest("makes valves")).as_json()
        assert needs_tagging("makes   valves", stored) is False

    def test_an_honest_empty_answer_is_not_asked_again(self):
        """An empty result is an answer, not a gap.

        CG Power's description names no product. Re-offering it every night
        would spend the budget re-deciding a question already decided.
        """
        about = "provides solutions for the management of electrical energy"
        stored = Tags((), digest(about)).as_json()
        assert needs_tagging(about, stored) is False

    def test_a_company_with_no_description_is_never_offered(self):
        assert needs_tagging(None, None) is False
        assert needs_tagging("   ", None) is False

    def test_an_unreadable_payload_is_treated_as_untagged(self):
        assert needs_tagging("makes valves", "{not json") is True
        assert needs_tagging("makes valves", '{"tags": []}') is True  # no digest


class TestRequest:
    def test_the_ticker_travels_as_the_key(self):
        """Batch results arrive in arbitrary order.

        Matching by position would attach one company's products to another --
        invisible in the output and wrong in the worst way.
        """
        assert request_for("MUFTI", "sells garments")["custom_id"] == "MUFTI"

    def test_it_constrains_the_reply_to_the_schema(self):
        params = request_for("X", "sells valves")["params"]
        fmt = params["output_config"]["format"]
        assert fmt["type"] == "json_schema"
        assert fmt["schema"]["required"] == ["tags"]
        assert fmt["schema"]["additionalProperties"] is False

    def test_the_description_is_the_only_company_specific_input(self):
        # Nothing identifies the company to the model: given the name it would
        # tag from what it knows rather than from the text, which is the one
        # thing the prompt is built to prevent.
        body = json.dumps(request_for("RELIANCE", "sells petrochemicals"))
        assert "RELIANCE" not in json.dumps(request_for("RELIANCE", "sells petrochemicals")["params"])
        assert "petrochemicals" in body


class FakeBatches:
    """Enough of the Batches endpoint to drive `extract`."""

    def __init__(self, replies, statuses=("ended",)):
        self.replies = replies           # custom_id -> reply text or Exception
        self.statuses = list(statuses)
        self.submitted = None

    # -- client surface -------------------------------------------------
    def create(self, requests):
        self.submitted = requests
        return self

    def retrieve(self, _id):
        self.processing_status = self.statuses.pop(0) if self.statuses else "ended"
        return self

    def results(self, _id):
        # Deliberately reversed: results arrive in arbitrary order, and a
        # position-based match would be invisible in the output.
        for custom_id in reversed(list(self.replies)):
            yield self._result(custom_id, self.replies[custom_id])

    id = "batch_fake"

    @staticmethod
    def _result(custom_id, reply):
        import types
        if reply is None:
            inner = types.SimpleNamespace(type="errored")
        else:
            block = types.SimpleNamespace(type="text", text=reply)
            inner = types.SimpleNamespace(
                type="succeeded",
                message=types.SimpleNamespace(content=[block]))
        return types.SimpleNamespace(custom_id=custom_id, result=inner)


class FakeClient:
    def __init__(self, batches):
        self.messages = type("M", (), {"batches": batches})()


class TestExtract:
    def run(self, replies, pairs, **kw):
        from mcfinex.tagging import extract
        batches = FakeBatches(replies)
        return extract(FakeClient(batches), pairs, poll=0, **kw), batches

    def test_results_are_matched_by_custom_id_not_position(self):
        """The failure this guards is silent and severe.

        Batch results arrive in any order. Matching by position would file one
        company's products under another -- a plausible-looking tag on the
        wrong business, which nothing downstream could detect.
        """
        out, _ = self.run(
            {"AAA": '{"tags": ["Valves"]}', "ZZZ": '{"tags": ["Textiles"]}'},
            [("AAA", "makes valves"), ("ZZZ", "makes textiles")])
        assert out["AAA"].tags == ("Valves",)
        assert out["ZZZ"].tags == ("Textiles",)

    def test_each_result_is_keyed_to_its_own_description(self):
        out, _ = self.run({"AAA": '{"tags": ["Valves"]}'}, [("AAA", "makes valves")])
        assert out["AAA"].describes("makes valves")
        assert not out["AAA"].describes("makes textiles")

    def test_a_failed_company_is_absent_rather_than_stored_empty(self):
        """Absent means "ask again"; empty means "answered, no products".

        Storing a failure as an empty answer would retire the company from the
        backlog on the strength of an error.
        """
        out, _ = self.run(
            {"OK": '{"tags": ["Valves"]}', "BAD": None},
            [("OK", "makes valves"), ("BAD", "makes pumps")])
        assert "OK" in out and "BAD" not in out

    def test_it_waits_for_the_batch_to_end(self):
        from mcfinex.tagging import extract
        batches = FakeBatches({"A": '{"tags": ["Valves"]}'},
                              statuses=("in_progress", "in_progress", "ended"))
        out = extract(FakeClient(batches), [("A", "makes valves")], poll=0)
        assert out["A"].tags == ("Valves",)

    def test_it_gives_up_rather_than_polling_forever(self):
        from mcfinex.tagging import extract
        batches = FakeBatches({"A": "{}"}, statuses=["in_progress"] * 50)
        with pytest.raises(TimeoutError, match="29 days"):
            extract(FakeClient(batches), [("A", "x")], poll=0, timeout=-1)

    def test_an_empty_input_makes_no_request_at_all(self):
        out, batches = self.run({}, [])
        assert out == {} and batches.submitted is None
