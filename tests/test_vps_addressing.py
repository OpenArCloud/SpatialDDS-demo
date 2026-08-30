"""
A VpsRequest names a service; both ends have to honour that name.

Two VPS services share one well-known topic, so "who answers" is decided by
addressing, not by the topic. Measured on AWS with a real OpenVPS localizer and
a co-located stand-in on the same bus: the stand-in answered a request
addressed to OpenVPS in 0.4 s, returning a confident pose from a different map,
and nothing in the response said so. Discovery had worked; the request went to
the wrong service anyway.

OpenVPS's own binding already gets this right — it ignores requests not
addressed to it and pins the map revision it answered with. These are the two
checks that make this repo behave the same way.
"""
import unittest
from unittest import mock

from spatialdds_demo.service_bus import VpsClient, VpsService, service_id_base


class _Service(VpsService):
    """A VpsService without a bus, for the addressing predicate alone."""

    def __init__(self, service_id=""):
        self._service_id = service_id_base(service_id)


class _Response:
    def __init__(self, query_id, service_id):
        self.query_id = query_id
        self.service_id = service_id


class _Client(VpsClient):
    """A VpsClient without a bus; `tt.take_samples` is patched per test."""

    def __init__(self):
        self._reader = object()


class ServiceIdBase(unittest.TestCase):
    def test_revision_suffix_is_stripped(self):
        self.assertEqual(service_id_base("svc:vps:oarc/x;v=map-1"), "svc:vps:oarc/x")

    def test_a_plain_id_is_unchanged(self):
        self.assertEqual(service_id_base("svc:vps:oarc/x"), "svc:vps:oarc/x")

    def test_empty_stays_empty(self):
        self.assertEqual(service_id_base(""), "")
        self.assertEqual(service_id_base(None), "")


class Addressing(unittest.TestCase):
    def setUp(self):
        self.service = _Service("svc:vps:demo/austin-downtown")

    def test_a_request_naming_this_service_is_answered(self):
        self.assertTrue(self.service.addressed_to_us("svc:vps:demo/austin-downtown"))

    def test_a_request_naming_another_service_is_left_alone(self):
        self.assertFalse(self.service.addressed_to_us("svc:vps:oarc/aws-test"))

    def test_an_unaddressed_request_is_answered(self):
        """A client that has discovered nothing names nobody; someone must reply."""
        self.assertTrue(self.service.addressed_to_us(""))

    def test_a_revision_suffix_still_routes_to_the_base_service(self):
        self.assertTrue(
            self.service.addressed_to_us("svc:vps:demo/austin-downtown;v=austin-map"))

    def test_an_unnamed_service_answers_everything(self):
        """Single-service deployments have nothing to confuse, so behaviour holds."""
        self.assertTrue(_Service().addressed_to_us("svc:vps:someone/else"))


class ReplyMatching(unittest.TestCase):
    def test_a_reply_from_another_service_is_not_accepted(self):
        wrong = _Response("q1", "svc:vps:demo/austin-downtown")
        with mock.patch("spatialdds_demo.service_bus.tt.take_samples",
                        return_value=[wrong]):
            self.assertIsNone(_Client().await_reply(
                "q1", timeout=0.2, expect_service_id="svc:vps:oarc/aws-test"))

    def test_the_addressed_services_reply_is_accepted(self):
        right = _Response("q1", "svc:vps:oarc/aws-test;v=map-9")
        with mock.patch("spatialdds_demo.service_bus.tt.take_samples",
                        return_value=[right]):
            self.assertIs(_Client().await_reply(
                "q1", timeout=0.2, expect_service_id="svc:vps:oarc/aws-test"), right)

    def test_without_an_expectation_any_responder_answers(self):
        any_one = _Response("q1", "svc:vps:whoever/it-is")
        with mock.patch("spatialdds_demo.service_bus.tt.take_samples",
                        return_value=[any_one]):
            self.assertIs(_Client().await_reply("q1", timeout=0.2), any_one)

    def test_a_reply_to_another_query_is_ignored(self):
        other = _Response("q2", "svc:vps:oarc/aws-test")
        with mock.patch("spatialdds_demo.service_bus.tt.take_samples",
                        return_value=[other]):
            self.assertIsNone(_Client().await_reply("q1", timeout=0.2))


if __name__ == "__main__":
    unittest.main()
