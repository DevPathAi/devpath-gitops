import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "release"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "tested_cloudflare_api", SCRIPTS / "cloudflare_pages.py"
)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FakeResponse:
    def __init__(self, body, *, status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            **(headers or {}),
        }
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.body if size < 0 else self.body[:size]


class CloudflareApiBoundaryTest(unittest.TestCase):
    def call(self, body=b'{"success":true,"result":{}}', **response_kwargs):
        response = FakeResponse(body, **response_kwargs)
        with mock.patch.object(
            module._NO_REDIRECT_OPENER, "open", return_value=response
        ):
            value = module._api("test-token", "GET", "/accounts/a/pages/projects/p")
        return value, response

    def test_reads_at_most_one_byte_past_the_response_bound(self):
        value, response = self.call()
        self.assertEqual(value, {"success": True, "result": {}})
        self.assertEqual(response.read_sizes, [module.MAX_API_RESPONSE_BYTES + 1])

    def test_rejects_non_json_encoded_redirect_or_oversized_responses(self):
        valid = json.dumps({"success": True, "result": {}}).encode()
        cases = (
            ("status", valid, {"status": 302}, "status"),
            ("content type", valid, {"headers": {"Content-Type": "text/html"}}, "content type"),
            ("encoding", valid, {"headers": {"Content-Encoding": "gzip"}}, "encoding"),
            (
                "declared size",
                valid,
                {"headers": {"Content-Length": str(module.MAX_API_RESPONSE_BYTES + 1)}},
                "size",
            ),
            ("actual size", b" " * (module.MAX_API_RESPONSE_BYTES + 1), {}, "size"),
            ("invalid utf8", b"\xff", {}, "UTF-8"),
            ("duplicate keys", b'{"success":true,"success":true}', {}, "duplicate"),
        )
        for label, body, kwargs, error in cases:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, error):
                self.call(body, **kwargs)

    def test_rejects_length_mismatch_and_non_success_envelopes(self):
        with self.assertRaisesRegex(ValueError, "length"):
            self.call(
                b'{"success":true}',
                headers={"Content-Length": "1"},
            )
        with self.assertRaisesRegex(ValueError, "rejected"):
            self.call(b'{"success":false,"errors":[]}')


if __name__ == "__main__":
    unittest.main()
