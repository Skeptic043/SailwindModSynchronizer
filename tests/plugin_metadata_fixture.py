"""Authored minimal PE/CLI fixtures, never executable code or third-party DLLs."""
from __future__ import annotations

import struct


def _compressed(value: int) -> bytes:
    if value < 0x80:
        return bytes([value])
    if value < 0x4000:
        return bytes([0x80 | (value >> 8), value & 0xFF])
    return struct.pack(">I", value | 0xC0000000)


def _pad(data: bytes, size: int = 4) -> bytes:
    return data + bytes((-len(data)) % size)


def managed_dll(
    plugins: tuple[tuple[str, str, str], ...] = (("private.example.plugin", "Example", "1.2.3"),),
    *, nested: bool = False, bad_attribute: bytes | None = None,
    attribute_namespace: str = "BepInEx", local_constructor: bool = False,
) -> bytes:
    """Build only metadata needed to describe plugin and dependency attributes.

    Literals model what the C# compiler emits for attribute constant arguments.
    Nested TypeDefs prove discovery doesn't depend on top-level type traversal.
    """
    strings = bytearray(b"\x00")
    blobs = bytearray(b"\x00")

    def string(value: str) -> int:
        index = len(strings)
        strings.extend(value.encode("utf-8") + b"\x00")
        return index

    def blob(value: bytes) -> int:
        index = len(blobs)
        blobs.extend(_compressed(len(value)) + value)
        return index

    def identity(values: tuple[str, str, str]) -> bytes:
        return b"\x01\x00" + b"".join(_compressed(len(v.encode("utf-8"))) + v.encode("utf-8") for v in values) + b"\x00\x00"

    plugin_sig = blob(b"\x20\x03\x01\x0e\x0e\x0e")
    ctor_name = string(".ctor")
    namespace = string(attribute_namespace)
    refs = [struct.pack("<HHH", 0, string(name), namespace) for name in ("BepInPlugin", "BepInDependency", "BaseUnityPlugin")]
    definitions = [struct.pack("<IHHHHH", 0, string("<Module>"), 0, 0, 1, 1)]
    for number in range(max(1, len(plugins))):
        definitions.append(struct.pack("<IHHHHH", 2 if nested else 1, string(f"Plugin{number}"), string("Fixtures"), (3 << 2) | 1, 1, 1))
    if nested:
        definitions.append(struct.pack("<IHHHHH", 1, string("Container"), 0, 0, 1, 1))
    members = [
        struct.pack("<HHH", (1 << 3) | 1, ctor_name, plugin_sig),
        struct.pack("<HHH", (2 << 3) | 1, ctor_name, plugin_sig),
    ]
    attributes = []
    for number, values in enumerate(plugins):
        parent = ((number + 2) << 5) | 3  # HasCustomAttribute TypeDef tag.
        attributes.append(struct.pack("<HHH", parent, (1 << 3) | (2 if local_constructor else 3), blob(bad_attribute if bad_attribute is not None else identity(values))))
        attributes.append(struct.pack("<HHH", parent, (2 << 3) | 3, blob(identity(("com.dependency.fake", "Decoy 9.9.9", "9.9.9")))))
    rows = {
        0: [struct.pack("<HHHHH", 0, string("Fixture.dll"), 1, 0, 0)],
        1: refs, 2: definitions, 10: members,
    }
    if local_constructor:
        definitions.append(struct.pack("<IHHHHH", 1, string("BepInPlugin"), namespace, 0, 1, 1))
        # MethodDef belongs to the final TypeDef, earlier types have empty lists.
        rows[6] = [struct.pack("<IHHHHH", 0, 0, 0, ctor_name, plugin_sig, 1)]
        for number in range(len(definitions) - 1):
            entry = bytearray(definitions[number])
            struct.pack_into("<H", entry, 12, 1)
            definitions[number] = bytes(entry)
    if attributes:
        rows[12] = attributes
    if nested:
        rows[41] = [struct.pack("<HH", number + 2, len(definitions)) for number in range(max(1, len(plugins)))]
    mask = sum(1 << number for number in rows)
    tables = struct.pack("<IBBBBQQ", 0, 2, 0, 0, 1, mask, 0)
    tables += b"".join(struct.pack("<I", len(rows[number])) for number in sorted(rows))
    tables += b"".join(b"".join(rows[number]) for number in sorted(rows))
    streams = [(b"#~", tables), (b"#Strings", bytes(strings)), (b"#Blob", bytes(blobs)), (b"#GUID", bytes(16))]
    version = _pad(b"v4.0.30319\x00")
    root = struct.pack("<IHHII", 0x424A5342, 1, 1, 0, len(version)) + version + struct.pack("<HH", 0, len(streams))
    offset = len(root) + sum(8 + len(_pad(name + b"\x00")) for name, _ in streams)
    stream_headers = b""
    contents = b""
    for name, data in streams:
        stream_headers += struct.pack("<II", offset, len(data)) + _pad(name + b"\x00")
        contents += _pad(data)
        offset += len(_pad(data))
    metadata = root + stream_headers + contents
    cli = bytearray(72)
    struct.pack_into("<IHHIIII", cli, 0, 72, 2, 5, 0x2048, len(metadata), 1, 0)
    section = _pad(bytes(cli) + metadata, 512)
    headers = bytearray(512)
    headers[:2] = b"MZ"
    struct.pack_into("<I", headers, 60, 0x80)
    headers[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<HHIIIHH", headers, 0x84, 0x14C, 1, 0, 0, 0, 224, 0x2102)
    opt = 0x98
    struct.pack_into("<H", headers, opt, 0x10B)
    struct.pack_into("<III", headers, opt + 28, 0x400000, 0x2000, 512)
    struct.pack_into("<II", headers, opt + 56, 0x4000, 512)
    struct.pack_into("<I", headers, opt + 92, 16)
    struct.pack_into("<II", headers, opt + 96 + 14 * 8, 0x2000, 72)
    sec = opt + 224
    headers[sec:sec + 8] = b".text\x00\x00\x00"
    struct.pack_into("<IIII", headers, sec + 8, len(section), 0x2000, len(section), 512)
    struct.pack_into("<I", headers, sec + 36, 0x40000040)
    return bytes(headers) + section


def native_dll() -> bytes:
    data = bytearray(managed_dll(()))
    struct.pack_into("<II", data, 0x98 + 96 + 14 * 8, 0, 0)
    return bytes(data)
