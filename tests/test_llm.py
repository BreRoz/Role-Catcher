"""Run: python -m unittest discover tests   (uses a fake client; no network, no key)"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from role_catcher import llm, spend
from role_catcher.config import load_config

SCHEMA = {"type": "object", "required": ["score"], "additionalProperties": False,
          "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100}}}


class FakeResp:
    def __init__(self, content, annotations=None, cost=0.002):
        self.d = {"model": "anthropic/claude-sonnet-5.5",
                  "choices": [{"message": {"content": content, "annotations": annotations or []}}],
                  "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": cost,
                            "prompt_tokens_details": {"cached_tokens": 50}}}

    def model_dump(self):
        return self.d


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = mock.Mock()
        self.chat.completions.create = self._create

    def _create(self, **kw):
        self.calls.append(kw)
        return self.replies.pop(0)


class Tests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.tmp = Path(tempfile.mkdtemp()) / "spend.jsonl"
        p = mock.patch.object(spend, "LOG_PATH", self.tmp)
        p.start(); self.addCleanup(p.stop)
        p2 = mock.patch.object(spend.log_call, "__defaults__", (self.tmp,))
        p2.start(); self.addCleanup(p2.stop)

    def test_valid_json_and_request_shape(self):
        c = FakeClient([FakeResp('{"score": 82}')])
        r = llm.complete(c, self.cfg, "scoring", "profile", "job", SCHEMA)
        self.assertEqual(r.data, {"score": 82})
        kw = c.calls[0]
        self.assertEqual(kw["extra_body"]["models"], ["anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-5"])
        self.assertEqual(kw["extra_body"]["provider"]["data_collection"], "deny")
        self.assertEqual(kw["response_format"]["type"], "json_schema")
        self.assertIn("cache_control", kw["messages"][0]["content"][0])

    def test_retry_once_then_succeed(self):
        c = FakeClient([FakeResp('{"score": 500}'), FakeResp('{"score": 90}')])
        r = llm.complete(c, self.cfg, "scoring", "p", "j", SCHEMA)
        self.assertEqual(r.data["score"], 90)
        self.assertEqual(len(c.calls), 2)
        self.assertAlmostEqual(r.cost, 0.004)

    def test_fails_after_two_bad_replies(self):
        c = FakeClient([FakeResp("nope"), FakeResp("still nope")])
        with self.assertRaises(llm.LLMError):
            llm.complete(c, self.cfg, "scoring", "p", "j", SCHEMA)

    def test_citations_come_from_annotations(self):
        ann = [{"type": "url_citation", "url_citation": {"url": "https://a.com/x", "title": "A"}},
               {"type": "url_citation", "url_citation": {"url": "https://a.com/x", "title": "A"}}]
        c = FakeClient([FakeResp('{"score": 1}', ann)])
        r = llm.complete(c, self.cfg, "vetting", "p", "j", SCHEMA, web_search=True)
        self.assertEqual(r.citations, [{"url": "https://a.com/x", "title": "A"}])
        self.assertEqual(c.calls[0]["extra_body"]["plugins"][0]["id"], "web")

    def test_spend_log_and_total(self):
        c = FakeClient([FakeResp('{"score": 1}')])
        llm.complete(c, self.cfg, "scoring", "p", "j", SCHEMA)
        self.assertAlmostEqual(spend.spend_today("America/Chicago", path=self.tmp), 0.002)


if __name__ == "__main__":
    unittest.main()
