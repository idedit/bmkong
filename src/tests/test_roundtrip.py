"""Protobuf patch tests, plus round-trip tests against a real save. Set BMKONG_TEST_SAVE (a copy).

Works on a temporary copy; the original save is never written.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BMKONG_HOME", tempfile.mkdtemp(prefix="bmkong-test-"))  # backups never reach the real app dir
from bmkong import protobuf as pb  # noqa: E402
from bmkong.container import MD5_FIELD, Archive, xor  # noqa: E402
from bmkong.save import DEATHS, TIME_FLOAT, TIME_INT, WILL, SaveError, SaveFile  # noqa: E402

SAVE = os.environ.get("BMKONG_TEST_SAVE")


def msg(*fields):
    return b"".join(fields)


class ProtobufTest(unittest.TestCase):
    def setUp(self):
        # {1: {5: 7, 2: [{1: 10, 2: 3}, {1: 20, 2: 4}], 9: "x"}, 3: 1.5f}
        entry = lambda k, v: pb.encode(2, pb.LEN, msg(pb.encode(1, pb.VARINT, k), pb.encode(2, pb.VARINT, v)))
        inner = msg(pb.encode(5, pb.VARINT, 7), entry(10, 3), entry(20, 4), pb.encode(9, pb.LEN, b"x"))
        self.buf = msg(pb.encode(1, pb.LEN, inner), pb.encode(3, pb.I32, b"\x00\x00\xc0\x3f"))

    def test_select_repeated_by_child_value(self):
        self.assertEqual(pb.get_int(self.buf, (1, (2, {1: 20}), 2)), 4)
        self.assertEqual(pb.get_int(self.buf, (1, (2, 0), 2)), 3)
        self.assertIsNone(pb.get(self.buf, (1, (2, {1: 30}))))

    def test_set_keeps_other_bytes(self):
        new = pb.set_int(self.buf, (1, (2, {1: 20}), 2), 300)  # value grows from 1 to 2 bytes
        self.assertEqual(pb.get_int(new, (1, (2, {1: 20}), 2)), 300)
        self.assertEqual(len(new), len(self.buf) + 1)
        back = pb.set_int(new, (1, (2, {1: 20}), 2), 4)
        self.assertEqual(back, self.buf)

    def test_float_and_missing_scalar(self):
        self.assertEqual(pb.get_float(self.buf, (3,)), 1.5)
        new = pb.set_int(self.buf, (1, 6), 9)  # absent proto3 scalar: appended to its message
        self.assertEqual(pb.get_int(new, (1, 6)), 9)
        self.assertEqual(pb.get_int(self.buf, (1, 6)), 0)

    def test_append_entry(self):
        new = pb.append(self.buf, (1,), 2, msg(pb.encode(1, pb.VARINT, 30), pb.encode(2, pb.VARINT, 1)))
        self.assertEqual(pb.get_int(new, (1, (2, {1: 30}), 2)), 1)

    def test_xor_is_symmetric(self):
        data = bytes(range(37))
        self.assertEqual(xor(xor(data)), data)


@unittest.skipUnless(SAVE, "needs BMKONG_TEST_SAVE")
class SaveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = Path(self.dir) / "ArchiveSaveFile.1.sav"
        shutil.copy(SAVE, self.path)
        self.save = SaveFile(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def reopen(self, data):
        self.path.write_bytes(data)
        return SaveFile(self.path)

    def test_game_checksum_matches(self):
        self.assertTrue(self.save.archive.md5_ok)

    def test_repair_bad_checksum(self):
        broken = self.save.source.replace(self.save.archive.head(MD5_FIELD).encode(), b"0" * 32, 1)
        self.assertFalse(Archive(broken).md5_ok)
        bad = self.reopen(broken)
        self.assertFalse(bad.summary()["checksum_ok"])
        fixed = self.reopen(bad.build({}))  # no edits: only the checksum is rewritten
        self.assertTrue(fixed.archive.md5_ok)
        self.assertEqual(fixed.data, self.save.data)
        self.assertEqual(fixed.source, self.save.source)

    def test_reencode_is_identical(self):
        self.assertEqual(self.save.archive.encode(self.save.data), self.save.source)

    def test_deaths_total_is_chapter_sum(self):
        v = self.save.values()
        self.assertEqual(v["deaths"], sum(v["chapters"].values()))

    def test_edit_everything(self):
        old = self.save.values()
        ch = sorted(old["chapters"])
        again = self.reopen(self.save.build({"will": 1234567, "chapters": {str(ch[0]): 0, str(ch[1]): 999},
                                             "play_time": 3600 * 500}))
        new = again.values()
        self.assertEqual(new["will"], 1234567)
        self.assertEqual(new["chapters"][ch[0]], 0)
        self.assertEqual(new["chapters"][ch[1]], 999)
        self.assertEqual(new["deaths"], sum(new["chapters"].values()))
        self.assertAlmostEqual(new["play_time"], 3600 * 500, delta=0.5)
        # every time counter moved by the same amount
        delta = 3600 * 500 - old["play_time"]
        for path in TIME_FLOAT:
            self.assertAlmostEqual(pb.get_float(again.data, path), pb.get_float(self.save.data, path) + delta, delta=1)
        for path in TIME_INT:
            self.assertAlmostEqual(pb.get_int(again.data, path), pb.get_int(self.save.data, path) + delta, delta=1)
        # the header keeps every field except the recomputed MD5, which the game accepts
        self.assertTrue(again.archive.md5_ok)
        old_h = [f for f in pb.parse(self.save.archive.header_raw) if f.number != MD5_FIELD]
        new_h = [f for f in pb.parse(again.archive.header_raw) if f.number != MD5_FIELD]
        self.assertEqual([(f.number, f.value) for f in old_h], [(f.number, f.value) for f in new_h])

    def test_untouched_sections_survive(self):
        again = self.reopen(self.save.build({"will": 5}))
        # walk down to the Will entry: at each level every sibling field is byte-identical
        a, b = self.save.data, again.data
        for step in WILL[:-2]:
            fa, fb = pb.parse(a), pb.parse(b)
            self.assertEqual([f.number for f in fa], [f.number for f in fb])
            changed = [x.number for x, y in zip(fa, fb) if x.value != y.value]
            self.assertEqual(changed, [step])
            a, b = pb.get(a, (step,)).value, pb.get(b, (step,)).value

    def test_write_backs_up_and_replaces(self):
        result = self.save.write({"chapters": {"10": 1}})
        self.assertTrue(Path(result["backup"]).is_file())
        self.assertEqual(SaveFile(self.path).values()["chapters"][10], 1)
        with self.assertRaises(SaveError):  # the file changed since this SaveFile read it
            self.save.write({"will": 1})

    def test_rejects_bad_values(self):
        for changes in ({"will": -1}, {"will": 1.5}, {"will": 2**31}, {"chapters": {"10": -3}},
                        {"chapters": {"77": 1}}, {"play_time": -1}, {}):
            with self.assertRaises(SaveError, msg=changes):
                self.save.build(changes)

    def test_deaths_entry_is_added_when_missing(self):
        # drop chapter 60 and check it can be re-added
        data = self.save.data
        f = pb.get(data, DEATHS + ((2, {1: 60}),))
        if f is None:
            self.skipTest("save has no chapter 60 entry")
        parent = pb.get(data, DEATHS).value
        self.save.data = _replace_deaths(data, parent.replace(_raw(parent, f), b"", 1))
        self.assertNotIn(60, self.save.chapters())
        again = self.reopen(self.save.build({"chapters": {"60": 5}}))
        self.assertEqual(again.values()["chapters"][60], 5)


def _raw(parent: bytes, field: pb.Field) -> bytes:
    """Encoded bytes of `field` as it appears in its parent (looked up by value)."""
    for f in pb.parse(parent):
        if f.number == field.number and f.value == field.value:
            return parent[f.start:f.end]
    raise AssertionError("field not in parent")


def _replace_deaths(data: bytes, new_value: bytes) -> bytes:
    def rebuild(buf, path):
        step, rest = path[0], path[1:]
        f = [x for x in pb.parse(buf) if x.number == step][-1]
        inner = new_value if not rest else rebuild(f.value, rest)
        return buf[:f.start] + pb.encode(f.number, pb.LEN, inner) + buf[f.end:]
    return rebuild(data, DEATHS)


if __name__ == "__main__":
    unittest.main()
