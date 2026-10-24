"""LZH (-lh5-) 形式を解凍する最小限の実装 (追加ライブラリ不要).

公式の競走成績データ (1ファイル1エントリの .lzh) を読むためだけのもの.
"""

from __future__ import annotations

import struct

DICBIT = 13
NC = 256 + 256 - 2  # 510
NT = 19
NP = DICBIT + 1     # 14
TBIT, CBIT, PBIT = 5, 9, 4


class _Bits:
    def __init__(self, data: bytes):
        self.d = data + b"\x00\x00\x00\x00"
        self.pos = 0

    def peek(self, n: int) -> int:
        if n == 0:
            return 0
        b = self.pos >> 3
        v = (self.d[b] << 16) | (self.d[b + 1] << 8) | self.d[b + 2]
        v = (v << (self.pos & 7)) & 0xFFFFFF
        return v >> (24 - n)

    def skip(self, n: int) -> None:
        self.pos += n

    def get(self, n: int) -> int:
        v = self.peek(n)
        self.pos += n
        return v


def _table(lengths: list[int]):
    """正準ハフマン符号の早見表を作る. 戻り値: (表, 表のビット数)."""
    maxlen = max(lengths) if lengths else 0
    if maxlen == 0:
        return None, 0
    count = [0] * (maxlen + 1)
    for L in lengths:
        if L:
            count[L] += 1
    code, start = 0, [0] * (maxlen + 2)
    for L in range(1, maxlen + 1):
        start[L] = code
        code = (code + count[L]) << 1
    tab = [None] * (1 << maxlen)
    nxt = start[:]
    for sym, L in enumerate(lengths):
        if not L:
            continue
        c = nxt[L]
        nxt[L] += 1
        base = c << (maxlen - L)
        entry = (sym, L)
        for i in range(base, base + (1 << (maxlen - L))):
            tab[i] = entry
    return tab, maxlen


class _Decoder:
    def __init__(self, data: bytes):
        self.bits = _Bits(data)
        self.blocksize = 0

    def _decode(self, tab, nbits, single):
        if tab is None:
            return single
        sym, L = tab[self.bits.peek(nbits)]
        self.bits.skip(L)
        return sym

    def _read_pt_len(self, nn: int, nbit: int, special: int):
        b = self.bits
        n = b.get(nbit)
        if n == 0:
            c = b.get(nbit)
            return [0] * nn, c
        lens = [0] * nn
        i = 0
        while i < n:
            c = b.peek(3)
            if c == 7:
                p = b.pos + 3
                while (b.d[p >> 3] >> (7 - (p & 7))) & 1:
                    c += 1
                    p += 1
                b.skip(c - 3)  # 先頭3ビット + 続く1の数 + 終端の0
            else:
                b.skip(3)
            lens[i] = c
            i += 1
            if i == special:
                c = b.get(2)
                i += c
        return lens, None

    def _read_c_len(self):
        b = self.bits
        n = b.get(CBIT)
        if n == 0:
            c = b.get(CBIT)
            return [0] * NC, c
        lens = [0] * NC
        i = 0
        while i < n:
            c = self._decode(self.pt_tab, self.pt_bits, self.pt_single)
            if c <= 2:
                if c == 0:
                    c = 1
                elif c == 1:
                    c = b.get(4) + 3
                else:
                    c = b.get(CBIT) + 20
                i += c
            else:
                lens[i] = c - 2
                i += 1
        return lens, None

    def _new_block(self):
        b = self.bits
        self.blocksize = b.get(16)
        lens, single = self._read_pt_len(NT, TBIT, 3)
        self.pt_tab, self.pt_bits = _table(lens) if single is None else (None, 0)
        self.pt_single = single
        lens, single = self._read_c_len()
        self.c_tab, self.c_bits = _table(lens) if single is None else (None, 0)
        self.c_single = single
        lens, single = self._read_pt_len(NP, PBIT, -1)
        self.p_tab, self.p_bits = _table(lens) if single is None else (None, 0)
        self.p_single = single

    def run(self, size: int) -> bytes:
        out = bytearray()
        b = self.bits
        while len(out) < size:
            if self.blocksize == 0:
                self._new_block()
            self.blocksize -= 1
            c = self._decode(self.c_tab, self.c_bits, self.c_single)
            if c < 256:
                out.append(c)
                continue
            length = c - 256 + 3
            p = self._decode(self.p_tab, self.p_bits, self.p_single)
            if p != 0:
                p = (1 << (p - 1)) + b.get(p - 1)
            start = len(out) - p - 1
            for k in range(length):
                out.append(out[start + k])
        return bytes(out[:size])


def extract_first(archive: bytes) -> bytes:
    """アーカイブ内の最初のファイルを解凍して返す (lh0 / lh5 のみ対応)."""
    level = archive[20]
    if level in (0, 1):
        hsize = archive[0]
        method = archive[2:7]
        comp, orig = struct.unpack_from("<II", archive, 7)
        pos = hsize + 2
        if level == 1:  # 拡張ヘッダーは圧縮サイズに含まれる
            ext = struct.unpack_from("<H", archive, pos - 2)[0]
            while ext:
                nxt = struct.unpack_from("<H", archive, pos + ext - 2)[0]
                pos += ext
                comp -= ext
                ext = nxt
    elif level == 2:
        hsize = struct.unpack_from("<H", archive, 0)[0]
        method = archive[2:7]
        comp, orig = struct.unpack_from("<II", archive, 7)
        pos = hsize
    else:
        raise ValueError("未対応のLZHヘッダーです")
    data = archive[pos:pos + comp]
    if method == b"-lh0-":
        return data[:orig]
    if method != b"-lh5-":
        raise ValueError(f"未対応の圧縮形式です: {method!r}")
    return _Decoder(data).run(orig)
