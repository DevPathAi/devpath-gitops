from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
from urllib.error import HTTPError


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
        # _probe_api retries propagation-shaped failures, so skip the real backoff sleeps here.
        with mock.patch.object(
            module._NO_REDIRECT_OPENER, "open", return_value=response, side_effect=side_effect
        ) as opened, mock.patch.object(module.time, "sleep", lambda _seconds: None):
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
        def http_error(status, reason):
            error = HTTPError(
                f"{self.ORIGIN}/api/invite-rounds", status, reason, Message(), io.BytesIO(b"")
            )
            self.addCleanup(error.close)
            return error

        for failure in (
            http_error(404, "Not Found"),
            http_error(308, "Permanent Redirect"),
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


class LandingProbePropagationTest(unittest.TestCase):
    """2026-10-01: the landing gate probed the public dist marker 1.17s after wrangler reported
    "Deployment complete!", got a 404, and failed release ms-20260930-s3-web-redesign-r3 — while the
    deployment was in fact correct and answered moments later. Propagation-shaped failures must be
    retried, and a 404 must not read as a connection failure."""

    ORIGIN = "https://leva.example.test"
    RELEASE = "ms-20260930-s3-web-redesign-r3"
    CANDIDATE = "e" * 64
    DIST = "d" * 64

    def http_error(self, status):
        # HTTPError inherits addinfourl -> tempfile._TemporaryFileWrapper, whose __del__ warns
        # unless the response was closed. Register the close so the suite output stays pristine.
        error = HTTPError(
            "https://leva.example.test/x", status, "boom", Message(), io.BytesIO(b"")
        )
        self.addCleanup(error.close)
        return error

    def marker(self, release_id=None, candidate=None, dist=None, extra=None):
        return FakeResponse(
            json.dumps(
                {
                    "candidate_spec_sha256": candidate or self.CANDIDATE,
                    "dist_sha256": dist or self.DIST,
                    "release_id": release_id or self.RELEASE,
                    **(extra or {}),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )

    def stale_marker(self):
        # The marker path is keyed by the dist hash alone, so the previous release of an unchanged
        # Home dist left a well-formed marker at the very same path (2026-10-06).
        return self.marker(release_id="ms-20261002-ai-provider-fallback-gpu7b", candidate="a" * 64)

    def drive(self, *outcomes):
        """Give _probe_marker one outcome per attempt; return (opener, recorded sleeps, error|None)."""
        slept = []
        error = None
        with mock.patch.object(
            module._NO_REDIRECT_OPENER, "open", side_effect=list(outcomes)
        ) as opened, mock.patch.object(module.time, "sleep", slept.append):
            try:
                module._probe_marker(self.ORIGIN, self.RELEASE, self.CANDIDATE, self.DIST)
            except ValueError as exc:
                error = exc
        return opened, slept, error

    def test_retries_until_the_new_deployment_propagates(self):
        opened, slept, error = self.drive(
            self.http_error(404), self.http_error(404), self.marker()
        )
        self.assertIsNone(error)
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(slept, list(module.PROBE_ATTEMPT_DELAYS[:2]))

    def test_retries_a_connection_failure(self):
        opened, _, error = self.drive(OSError("connection reset"), self.marker())
        self.assertIsNone(error)
        self.assertEqual(opened.call_count, 2)

    def test_names_a_404_separately_from_a_connection_failure(self):
        attempts = len(module.PROBE_ATTEMPT_DELAYS) + 1

        _, _, not_served = self.drive(*[self.http_error(404)] * attempts)
        self.assertIsNotNone(not_served)
        self.assertIn("public dist marker probe failed", str(not_served))
        self.assertIn("404", str(not_served))

        _, _, unreachable = self.drive(*[OSError("connection reset")] * attempts)
        self.assertIsNotNone(unreachable)
        self.assertIn("public dist marker probe failed", str(unreachable))
        self.assertIn("connect", str(unreachable))
        self.assertNotIn("404", str(unreachable))

    def test_stops_at_a_redirect_that_will_not_fix_itself(self):
        # A 308 means the path is wired wrong, not that the edge is still catching up.
        opened, slept, error = self.drive(self.http_error(308))
        self.assertIsNotNone(error)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(slept, [])
        self.assertIn("308", str(error))

    def test_retries_a_stale_marker_left_by_another_release_of_the_same_dist(self):
        # 2026-10-06: ms-20261003-ai-fallback-retry-budget shipped the same Home dist as the release
        # before it. 1.35s after "Deployment complete!" an edge still answered 200 with the previous
        # release's marker, and the gate failed while production was already correct.
        opened, slept, error = self.drive(
            self.stale_marker(), self.stale_marker(), self.marker()
        )
        self.assertIsNone(error)
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(slept, list(module.PROBE_ATTEMPT_DELAYS[:2]))

    def test_gives_up_when_another_release_of_the_same_dist_keeps_answering(self):
        attempts = len(module.PROBE_ATTEMPT_DELAYS) + 1
        opened, _, error = self.drive(*[self.stale_marker() for _ in range(attempts)])
        self.assertIsNotNone(error)
        self.assertEqual(opened.call_count, attempts)
        self.assertIn("public dist marker probe failed", str(error))
        self.assertIn("another release", str(error))

    def test_stops_at_a_marker_that_binds_another_dist(self):
        # Another dist under this dist's own path cannot be propagation: fail on the first attempt.
        opened, slept, error = self.drive(self.marker(dist="0" * 64))
        self.assertIsNotNone(error)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(slept, [])
        self.assertIn("exact release artifact", str(error))

    def test_stops_at_a_marker_with_unexpected_fields(self):
        # Only a well-formed marker of the same dist reads as a stale edge.
        opened, slept, error = self.drive(
            self.marker(release_id="ms-20261002-ai-provider-fallback-gpu7b", extra={"note": "x"})
        )
        self.assertIsNotNone(error)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(slept, [])
        self.assertIn("exact release artifact", str(error))

    def test_page_and_api_probes_retry_on_the_same_policy(self):
        for label, probe, ok in (
            (
                "page",
                module._probe,
                FakeResponse(
                    b"<!doctype html><title>home</title>",
                    headers={"Content-Type": "text/html"},
                ),
            ),
            ("api", module._probe_api, FakeResponse(b'{"rounds":[]}')),
        ):
            with self.subTest(probe=label):
                with mock.patch.object(
                    module._NO_REDIRECT_OPENER,
                    "open",
                    side_effect=[self.http_error(404), ok],
                ) as opened, mock.patch.object(module.time, "sleep", lambda _s: None):
                    probe(self.ORIGIN)
                self.assertEqual(opened.call_count, 2)

    def test_the_retry_budget_is_bounded(self):
        # A probe must not hold a release — or a rollback — open indefinitely.
        self.assertGreaterEqual(sum(module.PROBE_ATTEMPT_DELAYS), 10.0)
        self.assertLessEqual(sum(module.PROBE_ATTEMPT_DELAYS), 30.0)


if __name__ == "__main__":
    unittest.main()
