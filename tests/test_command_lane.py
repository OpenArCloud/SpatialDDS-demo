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

from spatialdds_demo import typed_transport as tt  # noqa: E402
from spatialdds_demo.qos_profiles import MODEL_COMMAND  # noqa: E402
from spatialdds_demo.topics import TOPIC_MODEL_COMMAND_V1  # noqa: E402
from spatialdds_idl.builtin import Time  # noqa: E402
from spatialdds_idl.oarc_model import ModelCommand  # noqa: E402
from spatialdds_idl.spatial.core import PoseSE3  # noqa: E402

DOMAIN = 52


def _participant(domain_id: int):
    try:
        from cyclonedds.domain import DomainParticipant
        return DomainParticipant(domain_id)
    except Exception as exc:
        raise unittest.SkipTest(f"DDS-UNAVAILABLE: {exc}")


def _command(command_id: str) -> ModelCommand:
    now = time.time()
    return ModelCommand(
        command_id=command_id, verb="retire", entity_id="ent:duck:east",
        reason="winter", requester_id="tool:test", has_pose=False,
        pose=PoseSE3(t=[0.0, 0.0, 0.0], q=[0.0, 0.0, 0.0, 1.0]),
        stamp=Time(sec=int(now), nanosec=0))


class CommandLane(unittest.TestCase):
    def test_the_type_is_keyed_so_an_invalid_sample_can_be_decoded(self):
        """
        The root cause, asserted at the type.

        A reader cannot filter what it cannot decode: the exception was raised
        inside `take()` while deserializing the key, before any Python of ours
        saw the sample. So the guard has to be here.
        """
        source = (REPO / "idl" / "demo" / "oarc_model.idl").read_text()
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
        reader = tt.make_reader(
            participant, TOPIC_MODEL_COMMAND_V1, ModelCommand, MODEL_COMMAND.name)

        seen = []
        for i in range(4):
            writer_participant = _participant(DOMAIN)
            writer = tt.make_writer(
                writer_participant, TOPIC_MODEL_COMMAND_V1, ModelCommand,
                MODEL_COMMAND.name)
            deadline = time.time() + 3
            while (time.time() < deadline
                   and not writer.get_publication_matched_status().current_count):
                time.sleep(0.02)
            writer.write(_command(f"cmd-{i}"))
            time.sleep(0.3)
            # The tool exits: writer and its participant go out of scope.
            del writer, writer_participant
            for _ in range(10):
                # The take that used to raise.
                seen.extend(c.command_id for c in tt.take_samples(reader) or [])
                time.sleep(0.05)

        self.assertGreaterEqual(len(set(seen)), 3,
                                f"reader should have survived and read; got {seen}")
        print(f"\n  command lane: {len(set(seen))} commands read across "
              f"4 writers that came and went")


if __name__ == "__main__":
    unittest.main()
