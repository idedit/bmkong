"""ArchiveSaveFile.N.sav / ShareArchiveSaveFile.sav envelope.

The file is a protobuf message:
    1: header  {1: 32-hex MD5, 4: save counter, 5: save time (unix), 6: first save time, 7, 8,
                9: uuid, 10, 11, 12: Steam ID, 13: uuid shared with ShareArchiveSaveFile.sav}
    2: payload = archive protobuf XORed with a fixed 8-byte key (no nonce, no compression)

The game (b1.BGW_GameArchiveMgr in the managed BtlSvr.Main assembly embedded in b1-Win64-Shipping.exe) checks
    MD5 = lowercase hex md5(encrypted payload + UTF-8 SALT)
and refuses to load the archive on a mismatch. The XOR key is the int64 4024806562674138235 in little endian.
ShareArchiveSaveFile.sav uses the same scheme with the MD5 in header field 2.
"""
from __future__ import annotations

import hashlib

from . import protobuf as pb

KEY = bytes.fromhex("7b5cda913efcda37")
SALT = b"lhx2tkh6lj1wj8jmrgs3k1xb2brusehx"
MD5_FIELD = 1  # header field holding the MD5 of an ArchiveSaveFile


class SaveError(ValueError):
    """User-readable failure; the save on disk is left untouched. Carries a Chinese and an English message."""

    def __init__(self, zh: str, en: str):
        super().__init__(zh)
        self.en = en

    def text(self, lang: str) -> str:
        return self.en if lang == "en" else str(self)


def xor(data: bytes) -> bytes:
    """Encrypts and decrypts (XOR is its own inverse)."""
    key = (KEY * (len(data) // 8 + 1))[:len(data)]
    return (int.from_bytes(data, "little") ^ int.from_bytes(key, "little")).to_bytes(len(data), "little")


def checksum(encrypted: bytes) -> bytes:
    return hashlib.md5(encrypted + SALT).hexdigest().encode("ascii")


class Archive:
    def __init__(self, raw: bytes):
        try:
            fields = pb.parse(raw)
        except pb.WireError:
            fields = []
        by = {f.number: f for f in fields}
        if [f.number for f in fields] != [1, 2] or by[1].wire != pb.LEN or by[2].wire != pb.LEN:
            raise SaveError("不是黑神话：悟空的存档文件（ArchiveSaveFile.N.sav）。",
                            "Not a Black Myth: Wukong save (ArchiveSaveFile.N.sav).")
        self.header_raw = by[1].value
        self.header = pb.parse(self.header_raw)
        self.md5_ok = self.head(MD5_FIELD) == checksum(by[2].value).decode()
        self.data = xor(by[2].value)
        if pb.message(self.data) is None:
            raise SaveError("存档内容无法解析，可能来自不支持的游戏版本。",
                            "The save content can't be parsed; it may come from an unsupported game version.")

    def head(self, number: int):
        values = [f.value for f in self.header if f.number == number]
        if not values:
            return None
        v = values[-1]
        return v.decode("utf-8", "replace") if isinstance(v, bytes) else v

    def encode(self, data: bytes) -> bytes:
        """The file with `data` as plaintext payload: header kept byte for byte except the recomputed MD5."""
        encrypted = xor(data)
        header = pb.set_bytes(self.header_raw, (MD5_FIELD,), checksum(encrypted))
        return pb.encode(1, pb.LEN, header) + pb.encode(2, pb.LEN, encrypted)
