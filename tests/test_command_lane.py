"""
The command lane survives the tools that use it.

An operator tool is a short-lived process: it writes one request and exits.
When its writer goes, the middleware delivers an invalid sample to whoever is
reading -- data absent, key blob present. That is ordinary, and it killed the
model service, because `ModelCommand` was unkeyed and the binding cannot
deserialize a key that does not exist:

    struct.error: unpack_from requires a buffer of at least 8 bytes ...

The service was holding the whole world at the time. A tool exiting is not
allowed to take that down, so this pins both halves of the repair: the type
has a key the middleware can name, and the read loop treats the command lane
as untrusted input.
"""

import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bus as bus  # noqa: E402
from spatialdds.dds.types.builtin import Time  # noqa: E402
from spatialdds.dds.types.spatial.owm import ModelCommand  # noqa: E402
from spatialdds.dds.types.spatial.core import Aabb3, PoseSE3  # noqa: E402

DOMAIN = 52


def _participant(domain_id: int):
    """A participant on the venue scene, or a skip if there is no usable DDS."""
    return bus.participant(domain_id)


def _command(command_id: str) -> ModelCommand:
    now = time.time()
    return ModelCommand(
        command_id=command_id, verb="retire", subject_id="ent:duck:east",
        reason="winter", requester_id="tool:test", has_pose=False,
        pose=PoseSE3(t=[0.0, 0.0, 0.0], q=[0.0, 0.0, 0.0, 1.0]),
        has_extent=False,
        extent=Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0]),
        stamp=Time(sec=int(now), nanosec=0))


class CommandLane(unittest.TestCase):
    def test_the_type_is_keyed_so_an_invalid_sample_can_be_decoded(self):
        """
        The root cause, asserted at the type.

        A reader cannot filter what it cannot decode: the exception was raised
        inside `take()` while deserializing the key, before any Python of ours
        saw the sample. So the guard has to be here.
        """
        # The 1.8 provisional module, because that is what this test's
        # `ModelCommand` is. It read `idl/demo/oarc_model.idl` until
        # 2026-10-18 -- the guard kept passing after the imports above moved
        # to `spatial.owm`, which made it a check on a file the subject no
        # longer came from. The property holds in both; the aim did not.
        # Since the SDK adoption `ModelCommand` is the SDK's, generated from
        # the IDL it vendors, so that is the file read here (it was this
        # repository's idl/v1.8 copy until the adoption retired it).
        from spatialdds.server import pin
        source = (Path(pin.IDL_DIR) / "provisional" / "owm.idl").read_text()
        block = source[source.index("struct ModelCommand"):]
        self.assertIn("@key string command_id", block,
                      "ModelCommand must be keyed or an exiting writer kills the reader")

    def test_a_reader_outlives_writers_that_come_and_go(self):
        """
        The scenario that crashed it: several short-lived tools in a row.

        Each writer is created, writes, and is dropped -- the shape of every
        run of move_duck.py or retire_entity.py. The reader must still be
        reading afterwards.
        """
        participant = _participant(DOMAIN)
        reader = bus.reader(participant, "model_command")

        seen = []
        for i in range(4):
            writer_participant = _participant(DOMAIN)
            writer = bus.writer(writer_participant, "model_command")
            writer.wait_matched(timeout=3)
            writer.write(_command(f"cmd-{i}"))
            time.sleep(0.3)
            # The tool exits: writer and its participant go out of scope.
            del writer, writer_participant
            for _ in range(10):
                # The take that used to raise.
                seen.extend(c.command_id for c in bus.samples(reader))
                time.sleep(0.05)

        self.assertGreaterEqual(len(set(seen)), 3,
                                f"reader should have survived and read; got {seen}")
        print(f"\n  command lane: {len(set(seen))} commands read across "
              f"4 writers that came and went")


class DepthOneIsPerCommand(unittest.TestCase):
    """
    §3.3.2 spells `model_command` KEEP_LAST(1), and the venue's MODEL_COMMAND
    profile is exactly that since the SDK adoption (it said KEEP_LAST(16)).

    Depth is per instance, and the lane is keyed on `command_id`, with every
    command minting its own. The reader here reads as the model service does:
    the lane's own profile, so a depth-1 cache, taken only after the burst has
    landed (the service takes every 0.1 s). Keyed, each command is its own
    instance holding its own one sample, so nothing is lost. The control is
    the same burst with one shared id, one instance at depth 1, which keeps
    only the last. That is what shows this test can tell the two apart.

    (A first draft read with history="KEEP_ALL" and taking as it went. Over
    loopback the writer delivers each sample at once, so the control lost
    nothing either, and the test measured nothing. Ledgered.)
    """

    BURST = 50

    def _burst(self, ids):
        reader_p = _participant(DOMAIN + 3)
        writer_p = _participant(DOMAIN + 3)
        reader = bus.reader(reader_p, "model_command")
        writer = bus.writer(writer_p, "model_command")
        self.assertTrue(writer.wait_matched(timeout=5))
        for cid in ids:
            writer.write(_command(cid))
        # A clean stop waits for every acknowledgement, so after it the burst
        # has landed in the reader's cache, untaken -- as it would between two
        # of the service's 0.1 s takes.
        writer_p.close()
        time.sleep(0.2)
        seen = [c.command_id for c in bus.samples(reader)]
        reader_p.close()
        return seen

    def test_a_burst_of_keyed_commands_loses_none_at_depth_one(self):
        ids = [f"cmd:burst:{i}" for i in range(self.BURST)]
        seen = self._burst(ids)
        self.assertEqual(sorted(set(seen)), sorted(ids),
                         f"{len(set(seen))}/{len(ids)} delivered")
        print(f"\n  command lane: {len(set(seen))}/{len(ids)} keyed commands "
              f"in a burst at KEEP_LAST(1)")

    def test_the_control_one_shared_id_does_lose_at_depth_one(self):
        seen = self._burst(["cmd:burst:shared"] * self.BURST)
        self.assertLess(len(seen), self.BURST,
                        "one instance at depth 1 should keep only the last of "
                        "a burst; if it keeps them all, this test cannot tell "
                        "keyed from unkeyed and measures nothing")
        print(f"\n  command lane control: {len(seen)}/{self.BURST} samples of "
              f"one shared id survived the same burst")


if __name__ == "__main__":
    unittest.main()
