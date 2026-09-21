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


class LandingApiSmokeTest(unittest.TestCase):
    ORIGIN = "https://leva.example.test"

    def probe(self, response=None, side_effect=None):
        with mock.patch.object(
            module._NO_REDIRECT_OPENER, "open", return_value=response, side_effect=side_effect
        ) as opened:
            module._probe_api(f"{self.ORIGIN}/")
        return opened

    def test_requests_the_side_effect_free_functions_route_without_redirects(self):
        response = FakeResponse(b'{"rounds":[]}')
        opened = self.probe(response)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, f"{self.ORIGIN}/api/invite-rounds")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(opened.call_args.kwargs["timeout"], 10)
        self.assertEqual(response.read_sizes, [module.MAX_API_SMOKE_BYTES + 1])

    def test_rejects_a_deployment_that_lost_its_functions(self):
        # 2026-09-21: a dist-only deploy dropped functions/ and /api/* served the static 404 page.
        for response in (
            FakeResponse(b"<!doctype html><title>404</title>", status=404),
            FakeResponse(b"", status=308),
            FakeResponse(b"<!doctype html><title>home</title>"),
            FakeResponse(b"\xff\xfe"),
            FakeResponse(b"[1" + b" " * module.MAX_API_SMOKE_BYTES + b"]"),
        ):
            with self.assertRaisesRegex(ValueError, "Landing API smoke"):
                self.probe(response)

    def test_wraps_the_errors_the_no_redirect_opener_really_raises(self):
        # The opener raises HTTPError (an OSError) for 3xx/4xx instead of returning a response.
        from urllib.error import HTTPError

        for failure in (
            HTTPError(f"{self.ORIGIN}/api/invite-rounds", 404, "Not Found", None, None),
            HTTPError(f"{self.ORIGIN}/api/invite-rounds", 308, "Permanent Redirect", None, None),
            OSError("connection reset"),
        ):
            with self.assertRaisesRegex(ValueError, "Landing API smoke failed"):
                self.probe(side_effect=failure)

    def test_production_verification_runs_the_api_smoke_after_the_page_probe(self):
        source = (SCRIPTS / "cloudflare_pages.py").read_text(encoding="utf-8")
        branch = source[source.index('if action == "verify-new-production":'):]
        branch = branch[: branch.index("return")]
        self.assertLess(
            branch.index("_probe(landing_origin)"), branch.index("_probe_api(landing_origin)")
        )


if __name__ == "__main__":
    unittest.main()
