"""In-test Classic card emulating the ACR1552 pseudo-APDUs (not the shared FakeTransport)."""

from nfc_cli.nfc.classic import sector_of, trailer_block

OK = b"\x90\x00"
FAIL = b"\x63\x00"


class FakeClassic:
    def __init__(self, blocks: int = 64, keys: dict | None = None, uid: bytes = bytes(4)):
        self.blocks = [bytes(16) for _ in range(blocks)]
        self.uid = uid
        sectors = sector_of(blocks - 1) + 1
        for s in range(sectors):
            self.blocks[trailer_block(s)] = bytes.fromhex("FFFFFFFFFFFF" "FF078069" "FFFFFFFFFFFF")
        self.keys = keys or {}  # sector -> {"A": bytes, "B": bytes}
        self.loaded = None
        self.auth_sector = None
        self.needs_cycle = False
        self.log: list[bytes] = []
        self.cycles = 0
        self.selected = True

    def _key(self, s, kt):
        if s in self.keys and kt in self.keys[s]:
            return self.keys[s][kt]
        t = self.blocks[trailer_block(s)]
        return t[:6] if kt == "A" else t[10:]

    def power_on(self):
        self.selected = True
        self.needs_cycle = False
        self.auth_sector = None
        self.cycles += 1
        return b"\x3b\x8f"

    def power_off(self):
        self.selected = False
        self.auth_sector = None

    def present(self):
        return True

    def escape(self, cmd):
        return b""

    def close(self):
        pass

    def transmit(self, apdu):
        self.log.append(bytes(apdu))
        a = bytes(apdu)
        if a[:2] == b"\xff\xca":
            return self.uid, OK
        if a[:2] == b"\xff\x82":
            self.loaded = a[5:11]
            return b"", OK
        if a[:2] == b"\xff\x86":
            blk, kt = a[7], "A" if a[8] == 0x60 else "B"
            if self.needs_cycle or not self.selected:
                return b"", FAIL
            if self.loaded == self._key(sector_of(blk), kt):
                self.auth_sector = sector_of(blk)
                return b"", OK
            self.needs_cycle = True
            self.auth_sector = None
            return b"", FAIL
        if a[:2] == b"\xff\xb0":
            if self.auth_sector != sector_of(a[3]):
                return b"", b"\x69\x82"
            return self.blocks[a[3]], OK
        if a[:2] == b"\xff\xd6":
            if self.auth_sector != sector_of(a[3]):
                return b"", b"\x69\x82"
            self.blocks[a[3]] = a[5:21]
            return b"", OK
        return b"", b"\x6d\x00"

    def writes(self):
        return [x for x in self.log if x[:2] == b"\xff\xd6"]
