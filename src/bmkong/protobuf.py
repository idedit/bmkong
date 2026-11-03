"""Schema-less protobuf wire format: parse with byte spans, and edit single values losslessly.

An edit splices only the target value and re-encodes the length prefixes of its ancestors; every other
byte of the message stays exactly as the game wrote it (field order, unknown fields, packed arrays).

A path is a sequence of steps. Each step is a field number, or (field number, selector) where the
selector picks one of the repeated occurrences: an int index, or {child field: value} to match the
occurrence whose child varint fields have those values (e.g. {1: 30} = the entry with id 30).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

VARINT, I64, LEN, I32 = 0, 1, 2, 5


class WireError(ValueError):
    pass


@dataclass
class Field:
    number: int
    wire: int
    start: int       # first byte of the tag
    value_start: int  # first byte of the value (after the length prefix for LEN)
    end: int
    value: int | bytes


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf) or shift > 63:
            raise WireError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if b < 0x80:
            return result, pos


def varint(n: int) -> bytes:
    if n < 0:
        n &= (1 << 64) - 1  # int32/int64 negatives are ten-byte two's complement
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def parse(buf: bytes) -> list[Field]:
    fields, pos = [], 0
    while pos < len(buf):
        start = pos
        key, pos = read_varint(buf, pos)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise WireError("field number 0")
        if wire == VARINT:
            value_start = pos
            value, pos = read_varint(buf, pos)
        elif wire == I64:
            value_start, pos = pos, pos + 8
            value = buf[value_start:pos]
        elif wire == I32:
            value_start, pos = pos, pos + 4
            value = buf[value_start:pos]
        elif wire == LEN:
            length, value_start = read_varint(buf, pos)
            pos = value_start + length
            value = buf[value_start:pos]
        else:
            raise WireError(f"wire type {wire}")
        if pos > len(buf):
            raise WireError("truncated field")
        fields.append(Field(number, wire, start, value_start, pos, value))
    return fields


def message(buf: bytes) -> list[Field] | None:
    """Fields of buf if it parses as a message, else None."""
    try:
        return parse(buf) if buf else None
    except WireError:
        return None


def ints(fields: list[Field]) -> dict[int, int]:
    """Last value of every varint field (proto3 scalar semantics)."""
    return {f.number: f.value for f in fields if f.wire == VARINT}


def _matches(f: Field, want: dict[int, int]) -> bool:
    if f.wire != LEN:
        return False
    sub = message(f.value)
    if sub is None:
        return False
    have = ints(sub)
    return all(have.get(k, 0) == v for k, v in want.items())


def _pick(fields: list[Field], step) -> Field | None:
    number, sel = (step, None) if isinstance(step, int) else step
    hits = [f for f in fields if f.number == number]
    if sel is None:
        return hits[-1] if hits else None
    if isinstance(sel, int):
        return hits[sel] if sel < len(hits) else None
    return next((f for f in hits if _matches(f, sel)), None)


def get(buf: bytes, path) -> Field | None:
    field = None
    for step in path:
        fields = parse(buf if field is None else field.value)
        field = _pick(fields, step)
        if field is None:
            return None
    return field


def get_int(buf: bytes, path) -> int | None:
    """Value of a varint field. A message that exists without the field holds proto3's default 0."""
    f = get(buf, path)
    if f is None:
        return 0 if len(path) > 1 and get(buf, path[:-1]) is not None else None
    return f.value if f.wire == VARINT else None


def get_float(buf: bytes, path) -> float | None:
    f = get(buf, path)
    if f is None:
        return 0.0 if len(path) > 1 and get(buf, path[:-1]) is not None else None
    return struct.unpack("<f", f.value)[0] if f.wire == I32 else None


def encode(number: int, wire: int, value) -> bytes:
    tag = varint(number << 3 | wire)
    if wire == VARINT:
        return tag + varint(value)
    if wire == LEN:
        return tag + varint(len(value)) + value
    return tag + value


def _set(buf: bytes, path, encoded: bytes, number_wire: tuple[int, int]) -> bytes:
    step, rest = path[0], path[1:]
    fields = parse(buf)
    f = _pick(fields, step)
    number = step if isinstance(step, int) else step[0]
    if not rest:
        if f is None:
            return buf + encoded  # absent scalar (proto3 default): append it
        if (f.number, f.wire) != number_wire:
            raise WireError(f"field {number} has wire type {f.wire}")
        return buf[:f.start] + encoded + buf[f.end:]
    if f is None:
        raise WireError(f"field {number} not found")
    if f.wire != LEN:
        raise WireError(f"field {number} is not a message")
    inner = _set(f.value, rest, encoded, number_wire)
    return buf[:f.start] + encode(f.number, LEN, inner) + buf[f.end:]


def set_int(buf: bytes, path, value: int) -> bytes:
    number = path[-1] if isinstance(path[-1], int) else path[-1][0]
    return _set(buf, path, encode(number, VARINT, value), (number, VARINT))


def set_float(buf: bytes, path, value: float) -> bytes:
    number = path[-1] if isinstance(path[-1], int) else path[-1][0]
    return _set(buf, path, encode(number, I32, struct.pack("<f", value)), (number, I32))


def set_bytes(buf: bytes, path, value: bytes) -> bytes:
    """Replace a LEN field (string/bytes) at path."""
    number = path[-1] if isinstance(path[-1], int) else path[-1][0]
    return _set(buf, path, encode(number, LEN, value), (number, LEN))


def append(buf: bytes, path, number: int, payload: bytes) -> bytes:
    """Append a LEN field `number` (e.g. a new repeated entry) to the message at path."""
    if not path:
        return buf + encode(number, LEN, payload)
    step, rest = path[0], path[1:]
    f = _pick(parse(buf), step)
    if f is None or f.wire != LEN:
        raise WireError("parent message not found")
    inner = append(f.value, rest, number, payload)
    return buf[:f.start] + encode(f.number, LEN, inner) + buf[f.end:]
