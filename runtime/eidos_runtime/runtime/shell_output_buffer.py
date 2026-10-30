"""Bounded Shell evidence retaining a stable prefix and the latest suffix."""
from __future__ import annotations

import codecs
from dataclasses import dataclass


@dataclass(frozen=True)
class ShellOutputSlice:
    text: str
    next_offset: int


class ShellOutputBuffer:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.head = bytearray()
        self.tail = bytearray()
        self.total_bytes = 0
        self.finished = False

    @property
    def retained_bytes(self) -> int:
        return len(self.head) + len(self.tail)

    @property
    def omitted_bytes(self) -> int:
        return self.total_bytes - self.retained_bytes

    def append(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        head_size = min(len(chunk), self.limit // 2 - len(self.head))
        self.head.extend(chunk[:head_size])
        self.tail.extend(chunk[head_size:])
        excess = len(self.tail) - (self.limit - self.limit // 2)
        if excess > 0:
            del self.tail[:excess]

    def read(self, after: int = 0) -> ShellOutputSlice:
        pieces: list[tuple[int, bytes]] = [(0, bytes(self.head))]
        tail_start = self.total_bytes - len(self.tail)
        if tail_start == len(self.head):
            pieces = [(0, bytes(self.head + self.tail))]
        elif self.tail:
            pieces.append((tail_start, bytes(self.tail)))
        result: list[str] = []
        cursor = after
        for start, data in pieces:
            end = start + len(data)
            if end <= cursor:
                continue
            offset = max(cursor, start)
            data = data[offset - start:]
            if offset > cursor:
                # A retained suffix may begin in the middle of a UTF-8 sequence.
                while data and data[0] & 0xC0 == 0x80:
                    data = data[1:]
                    offset += 1
                result.append(f"\n[... {offset - cursor} output bytes omitted ...]\n")
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            text = decoder.decode(data, final=self.finished and end == self.total_bytes)
            pending = len(decoder.getstate()[0])
            result.append(text)
            cursor = end - pending
        return ShellOutputSlice("".join(result), cursor)
