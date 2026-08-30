#!/usr/bin/env python3
"""Pure-python keccak256 + event topic0, vendored so this repo is standalone.

Event topics are DERIVED from signatures rather than hardcoded: a copy-pasted
topic hash is unverifiable, and one wrong nibble silently returns zero logs,
which reads identically to "this event never fired".
"""

# keccak256 (pure python) for topic0
def keccak256(data: bytes) -> bytes:
    def rol(x, n): return ((x << n) | (x >> (64 - n))) & ((1 << 64) - 1)
    RC = [0x0000000000000001,0x0000000000008082,0x800000000000808A,0x8000000080008000,
          0x000000000000808B,0x0000000080000001,0x8000000080008081,0x8000000000008009,
          0x000000000000008A,0x0000000000000088,0x0000000080008009,0x000000008000000A,
          0x000000008000808B,0x800000000000008B,0x8000000000008089,0x8000000000008003,
          0x8000000000008002,0x8000000000000080,0x000000000000800A,0x800000008000000A,
          0x8000000080008081,0x8000000000008080,0x0000000080000001,0x8000000080008008]
    r = [[0,36,3,41,18],[1,44,10,45,2],[62,6,43,15,61],[28,55,25,21,56],[27,20,39,8,14]]
    rate = 136
    data = bytearray(data); data.append(0x01)
    while len(data) % rate != 0: data.append(0x00)
    data[-1] |= 0x80
    lanes = [[0]*5 for _ in range(5)]
    for off in range(0, len(data), rate):
        block = data[off:off+rate]
        for i in range(rate//8): lanes[i%5][i//5] ^= int.from_bytes(block[i*8:(i+1)*8], 'little')
        for _ in range(24):
            C = [lanes[x][0]^lanes[x][1]^lanes[x][2]^lanes[x][3]^lanes[x][4] for x in range(5)]
            D = [C[(x-1)%5] ^ rol(C[(x+1)%5],1) for x in range(5)]
            for x in range(5):
                for y in range(5): lanes[x][y] ^= D[x]
            B = [[0]*5 for _ in range(5)]
            for x in range(5):
                for y in range(5): B[y][(2*x+3*y)%5] = rol(lanes[x][y], r[x][y])
            for x in range(5):
                for y in range(5): lanes[x][y] = B[x][y] ^ ((~B[(x+1)%5][y]) & B[(x+2)%5][y])
            lanes[0][0] ^= RC[_]
    out = b''
    for i in range(4): out += lanes[i%5][i//5].to_bytes(8, 'little')
    return out

def topic0(sig): return "0x" + keccak256(sig.encode()).hex()
