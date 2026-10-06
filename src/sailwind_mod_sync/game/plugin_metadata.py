"""Read declared BepInEx identity without loading or executing input assemblies."""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import dnfile
from dnfile.mdtable import MemberRefRow, MethodDefRow, TypeDefRow, TypeRefRow

MAX_DLL_BYTES = 32 * 1024 * 1024
MAX_ATTRIBUTE_BYTES = 64 * 1024
MAX_METADATA_ROWS = 1_000_000


@dataclass(frozen=True)
class PluginMetadata:
    guid: str
    name: str
    version: str


@dataclass(frozen=True)
class MetadataResult:
    status: Literal["plugin", "managed-helper", "native", "unsupported"]
    plugins: tuple[PluginMetadata, ...] = ()


def _compressed_uint(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise ValueError("Truncated compressed integer")
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    count = 2 if first < 0xC0 else 4 if first < 0xE0 else 0
    if not count or offset + count > len(data):
        raise ValueError("Invalid compressed integer")
    value = first & (0x3F if count == 2 else 0x1F)
    for byte in data[offset + 1:offset + count]:
        value = (value << 8) | byte
    return value, offset + count


def _identity(blob: bytes) -> PluginMetadata:
    # ECMA-335 II.23.3: prolog, three SerStrings, zero named arguments.
    if len(blob) > MAX_ATTRIBUTE_BYTES or blob[:2] != b"\x01\x00":
        raise ValueError("Unsupported plugin attribute")
    offset = 2
    values: list[str] = []
    for _ in range(3):
        size, offset = _compressed_uint(blob, offset)
        end = offset + size
        if end > len(blob):
            raise ValueError("Truncated plugin attribute")
        values.append(blob[offset:end].decode("utf-8", errors="strict"))
        offset = end
    if blob[offset:] != b"\x00\x00":
        raise ValueError("Unsupported named plugin arguments")
    guid, name, version = values
    if not guid.strip() or not name.strip():
        raise ValueError("Empty plugin identity")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version):
        raise ValueError("Invalid BepInEx version")
    if any(int(part) > 2147483647 for part in version.split(".")):
        raise ValueError("Invalid BepInEx version")
    return PluginMetadata(guid, name, version)


def read_plugin_metadata(path: Path) -> MetadataResult:
    """Return declared type attributes, or a bounded classification for fallback.

    No external assembly resolution is needed for normal BepInPlugin attributes.
    A valid managed DLL without that attribute is support code, not a guessed mod.
    Multiple identities are returned intact for the caller to report ambiguity.
    """
    pe = None
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_DLL_BYTES + 1)
        if len(data) > MAX_DLL_BYTES:
            return MetadataResult("unsupported")
        pe = dnfile.dnPE(data=data, fast_load=True, clr_lazy_load=True)
        directories = pe.OPTIONAL_HEADER.DATA_DIRECTORY
        if len(directories) <= 14:
            return MetadataResult("unsupported")
        if directories[14].VirtualAddress == 0:
            return MetadataResult("native")
        pe.parse_data_directories(directories=[14])
        if pe.net is None or pe.net.mdtables is None:
            return MetadataResult("unsupported")
        tables = pe.net.mdtables
        if any(table.num_rows > MAX_METADATA_ROWS for table in tables.tables_list):
            return MetadataResult("unsupported")
        if tables.TypeDef is None:
            return MetadataResult("unsupported")
        attributes = tables.CustomAttribute
        if attributes is None:
            return MetadataResult("managed-helper")
        plugins: list[PluginMetadata] = []
        method_owners: dict[int, TypeDefRow] | None = None
        for attribute in attributes.rows:
            parent = attribute.Parent.row
            if parent is None:
                return MetadataResult("unsupported")
            if not isinstance(parent, TypeDefRow):
                continue
            constructor = attribute.Type.row
            if isinstance(constructor, MemberRefRow):
                declaring_type = constructor.Class.row
            elif isinstance(constructor, MethodDefRow):
                if method_owners is None:
                    method_owners = {id(method.row): owner for owner in tables.TypeDef.rows for method in owner.MethodList}
                declaring_type = method_owners.get(id(constructor))
            else:
                return MetadataResult("unsupported")
            if not isinstance(declaring_type, (TypeRefRow, TypeDefRow)):
                return MetadataResult("unsupported")
            if (str(declaring_type.TypeNamespace), str(declaring_type.TypeName)) != ("BepInEx", "BepInPlugin"):
                continue
            # HASTHIS, 3 parameters, void return, three System.String arguments.
            if str(constructor.Name) != ".ctor" or constructor.Signature.value != b"\x20\x03\x01\x0e\x0e\x0e":
                return MetadataResult("unsupported")
            plugins.append(_identity(attribute.Value.value))
        return MetadataResult("plugin", tuple(plugins)) if plugins else MetadataResult("managed-helper")
    except (OSError, ValueError, TypeError, AttributeError, IndexError, KeyError,
            OverflowError, struct.error, dnfile.PEFormatError, dnfile.errors.dnFormatError):
        return MetadataResult("unsupported")
    finally:
        if pe is not None:
            pe.close()
