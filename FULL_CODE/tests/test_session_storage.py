"""Session storage: memory and SQLite behave the same; chats survive a restart and move between processes."""

from __future__ import annotations

import multiprocessing as mp
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from api.state import State, session_from_dict, session_to_dict
from groundtruth.answering import Session
from groundtruth.builder import build_assistant
from groundtruth.catalogue import Spec
from groundtruth.config import get_settings
from groundtruth.storage import MemoryBackend, SqliteBackend

DATA = Path(__file__).resolve().parent.parent / "data"


def tmpdir(test: unittest.TestCase) -> str:
    """A temporary folder removed after the test, and after the stores in it have been closed."""
    folder = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, folder, ignore_errors=True)
    return folder


def sqlite(test: unittest.TestCase, path: Path | str, cap: int = 100, ttl: float = 60) -> SqliteBackend:
    """A SQLite store that is closed when the test ends."""
    store = SqliteBackend(path, cap, ttl)
    test.addCleanup(store.close)
    return store


def _hammer(args: tuple[str, int, int]) -> int:
    """Run in another process: write and read back sessions in a shared file; returns how many came back right."""
    path, worker, count = args
    store = SqliteBackend(path, max_sessions=10_000, ttl_seconds=60)
    try:
        return _round_trips(store, worker, count)
    finally:
        store.close()


def _round_trips(store: SqliteBackend, worker: int, count: int) -> int:
    """Save and read back `count` sessions; returns how many came back right."""
    good = 0
    for i in range(count):
        sid = f"w{worker}-{i}"
        store.save(sid, {"n": i, "worker": worker})
        good += store.load(sid) == {"n": i, "worker": worker}
    return good


class BackendBehaviourTest(unittest.TestCase):
    def backends(self, tmp: str, cap: int = 3, ttl: float = 60):
        return [MemoryBackend(cap, ttl), sqlite(self, Path(tmp) / "s.sqlite", cap, ttl)]

    def test_round_trip_replace_and_delete(self) -> None:
        tmp = tmpdir(self)
        for store in self.backends(tmp):
            self.assertIsNone(store.load("a"))
            store.save("a", {"x": [1, 2], "y": "é"})
            self.assertEqual(store.load("a"), {"x": [1, 2], "y": "é"})
            store.save("a", {"x": []})
            self.assertEqual((store.load("a"), len(store)), ({"x": []}, 1))
            store.delete("a")
            self.assertEqual((store.load("a"), len(store)), (None, 0))

    def test_the_least_recently_used_go_first(self) -> None:
        tmp = tmpdir(self)
        for store in self.backends(tmp, cap=3):
            for sid in "abc":
                store.save(sid, {"id": sid})
                time.sleep(0.01)
            store.load("a")  # reading is use, but saving is what SQLite orders by: touch it with a save
            store.save("a", {"id": "a"})
            store.save("d", {"id": "d"})
            if isinstance(store, SqliteBackend):
                store.trim()
            self.assertEqual({s for s in "abcd" if store.load(s)}, {"a", "c", "d"})

    def test_idle_sessions_expire(self) -> None:
        tmp = tmpdir(self)
        for store in self.backends(tmp, ttl=0.05):
            store.save("a", {"x": 1})
            time.sleep(0.1)
            self.assertIsNone(store.load("a"))
            self.assertEqual(len(store), 0)

    def test_a_session_survives_a_restart(self) -> None:
        tmp = tmpdir(self)
        first = sqlite(self, Path(tmp) / "s.sqlite")
        first.save("a", {"x": 1})
        first.close()
        self.assertEqual(sqlite(self, Path(tmp) / "s.sqlite").load("a"), {"x": 1})

    def test_many_processes_can_share_one_file(self) -> None:
        tmp = tmpdir(self)
        path = str(Path(tmp) / "s.sqlite")
        sqlite(self, path, 10_000)  # create the table once
        with mp.get_context("spawn").Pool(4) as pool:
            results = pool.map(_hammer, [(path, w, 150) for w in range(4)])
        self.assertEqual(results, [150] * 4)
        self.assertEqual(len(sqlite(self, path, 10_000)), 600)


class SessionDataTest(unittest.TestCase):
    def test_a_session_round_trips_through_json(self) -> None:
        spec = Spec(
            type_tokens=["shoe"],
            filters={"color": "black"},
            exclude={"material": ["mesh"]},
            price_max=2500.0,
            size="9",
            sort="price_asc",
            measures=[("w", 65.0)],
            alternatives={"color": ["grey"]},
        )
        session = Session(spec=spec, last_product="P0001", shown=["P0001", "P0002"])
        again = session_from_dict(session_to_dict(session))
        self.assertEqual((again.spec, again.last_product, again.shown), (spec, "P0001", ["P0001", "P0002"]))
        self.assertEqual(session_from_dict(None).spec, Spec())


class ChatAcrossProcessesTest(unittest.TestCase):
    """Two State objects on one SQLite file stand for two server processes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.assistant = build_assistant(DATA / "catalogue.jsonl", get_settings().paths.data_dir / "policy.txt")

    def test_a_follow_up_works_on_another_process_and_after_a_restart(self) -> None:
        tmp = tmpdir(self)
        file = Path(tmp) / "s.sqlite"
        one = State(self.assistant, sqlite(self, file, 100, 600))
        first = one.chat(None, "black running shoes")
        sid = first["session_id"]
        two = State(self.assistant, sqlite(self, file, 100, 600))  # another process
        follow = two.chat(sid, "under 2500")
        self.assertEqual(follow["session_id"], sid)
        self.assertEqual(follow["remembered"]["budget_max"], 2500.0)
        self.assertEqual(follow["remembered"]["filters"], first["remembered"]["filters"])
        self.assertEqual(follow["totals"]["queries"], 2)
        # the same chat on one process with memory gives the same answer
        memory = State(self.assistant, MemoryBackend(100, 600))
        memory.chat(None, "black running shoes")
        alone = memory.chat(memory.chat(None, "black running shoes")["session_id"], "under 2500")
        self.assertEqual(follow["action"], alone["action"])
        # restart: a new State and a new connection still know the chat
        three = State(self.assistant, sqlite(self, file, 100, 600))
        self.assertEqual(three.chat(sid, "size 9")["totals"]["queries"], 3)
        three.reset(sid)
        self.assertEqual(three.session(sid)[1]["said"].maxlen, 30)
        self.assertIsNone(three.chat(sid, "hi")["remembered"]["budget_max"])


if __name__ == "__main__":
    unittest.main()
