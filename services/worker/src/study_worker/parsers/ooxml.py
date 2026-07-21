"""Read-only OOXML ZIP/XML preflight with no relationship resolution."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from lxml import etree  # type: ignore[import-untyped]


class OOXMLSecurityError(RuntimeError):
    """A stable, content-free OOXML rejection."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class OOXMLSecurityPolicy:
    max_entries: int = 2_048
    max_uncompressed_bytes: int = 512 * 1024 * 1024
    max_entry_bytes: int = 128 * 1024 * 1024
    max_compression_ratio: float = 200

    def __post_init__(self) -> None:
        if (
            self.max_entries <= 0
            or self.max_uncompressed_bytes <= 0
            or self.max_entry_bytes <= 0
            or self.max_compression_ratio <= 0
        ):
            raise ValueError("OOXML security limits must be positive")


@dataclass(frozen=True, slots=True)
class ExternalRelationship:
    source_part: str
    relationship_type: str
    target_scheme: str


@dataclass(frozen=True, slots=True)
class OOXMLInspection:
    entry_count: int
    uncompressed_bytes: int
    external_relationships: tuple[ExternalRelationship, ...]
    has_ole: bool
    has_smartart: bool
    has_omml: bool


@dataclass(frozen=True, slots=True)
class OLEMetadata:
    name: str
    prog_id: str


@dataclass(frozen=True, slots=True)
class SlideFeatures:
    omml_texts: tuple[str, ...]
    smartart_count: int
    ole_objects: tuple[OLEMetadata, ...]
    external_relationship_count: int


def inspect_ooxml(path: Path, policy: OOXMLSecurityPolicy) -> OOXMLInspection:
    """Inspect package metadata without extracting files or opening external targets."""

    if path.is_symlink() or not path.is_file():
        raise OOXMLSecurityError("OOXML_INPUT_INVALID")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        raise OOXMLSecurityError("OOXML_CONTAINER_INVALID") from None

    with archive:
        infos = archive.infolist()
        if len(infos) > policy.max_entries:
            raise OOXMLSecurityError("OOXML_ENTRY_LIMIT_EXCEEDED")
        seen: set[str] = set()
        uncompressed_bytes = 0
        for info in infos:
            _validate_entry_name(info.filename)
            normalized = info.filename.casefold()
            if normalized in seen:
                raise OOXMLSecurityError("OOXML_DUPLICATE_ENTRY")
            seen.add(normalized)
            if info.flag_bits & 0x1:
                raise OOXMLSecurityError("OOXML_ENCRYPTED_ENTRY")
            if info.file_size > policy.max_entry_bytes:
                raise OOXMLSecurityError("OOXML_ENTRY_SIZE_EXCEEDED")
            uncompressed_bytes += info.file_size
            if uncompressed_bytes > policy.max_uncompressed_bytes:
                raise OOXMLSecurityError("OOXML_SIZE_LIMIT_EXCEEDED")
            if info.file_size:
                if info.compress_size == 0:
                    raise OOXMLSecurityError("OOXML_COMPRESSION_RATIO_EXCEEDED")
                if info.file_size / info.compress_size > policy.max_compression_ratio:
                    raise OOXMLSecurityError("OOXML_COMPRESSION_RATIO_EXCEEDED")

        required = {"[content_types].xml", "ppt/presentation.xml"}
        if not required <= seen:
            raise OOXMLSecurityError("OOXML_PRESENTATION_PART_MISSING")
        if any(_is_macro_part(info.filename) for info in infos):
            raise OOXMLSecurityError("MACRO_CONTENT_BLOCKED")

        relationships: list[ExternalRelationship] = []
        has_ole = any("/embeddings/" in info.filename.casefold() for info in infos)
        has_smartart = any("/diagrams/" in info.filename.casefold() for info in infos)
        has_omml = False
        for info in infos:
            lower_name = info.filename.casefold()
            if not lower_name.endswith((".xml", ".rels")):
                continue
            payload = _read_xml(archive, info)
            root = _parse_xml(payload)
            if lower_name.endswith(".rels"):
                for element in root.iter():
                    if _local_name(element.tag) != "Relationship":
                        continue
                    relationship_type = element.get("Type", "").rsplit("/", 1)[-1]
                    if relationship_type == "oleObject":
                        has_ole = True
                    if relationship_type.startswith("diagram"):
                        has_smartart = True
                    if element.get("TargetMode", "").casefold() != "external":
                        continue
                    scheme = urlsplit(element.get("Target", "")).scheme.casefold() or "unknown"
                    relationships.append(
                        ExternalRelationship(
                            source_part=info.filename,
                            relationship_type=relationship_type or "unknown",
                            target_scheme=scheme,
                        )
                    )
                continue
            for element in root.iter():
                local_name = _local_name(element.tag)
                namespace = _namespace(element.tag)
                if local_name == "oleObj":
                    has_ole = True
                if local_name in {"relIds", "dataModel"} and namespace.endswith("/diagram"):
                    has_smartart = True
                if local_name == "oMath" and namespace.endswith("/math"):
                    has_omml = True

        return OOXMLInspection(
            entry_count=len(infos),
            uncompressed_bytes=uncompressed_bytes,
            external_relationships=tuple(
                sorted(
                    relationships,
                    key=lambda item: (
                        item.source_part,
                        item.relationship_type,
                        item.target_scheme,
                    ),
                )
            ),
            has_ole=has_ole,
            has_smartart=has_smartart,
            has_omml=has_omml,
        )


def inspect_slide_features(
    path: Path,
    policy: OOXMLSecurityPolicy,
) -> dict[int, SlideFeatures]:
    """Return presentation feature metadata without resolving package relationships."""

    inspection = inspect_ooxml(path, policy)
    external_counts: dict[int, int] = {}
    for relationship in inspection.external_relationships:
        match = re.search(r"/slide(\d+)\.xml\.rels$", relationship.source_part)
        if match is not None:
            ordinal = int(match.group(1))
            external_counts[ordinal] = external_counts.get(ordinal, 0) + 1

    features: dict[int, SlideFeatures] = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            match = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", info.filename)
            if match is None:
                continue
            ordinal = int(match.group(1))
            root = _parse_xml(_read_xml(archive, info))
            omml_texts: list[str] = []
            smartart_count = 0
            ole_objects: list[OLEMetadata] = []
            for element in root.iter():
                local_name = _local_name(element.tag)
                namespace = _namespace(element.tag)
                if local_name == "oMath" and namespace.endswith("/math"):
                    text = "".join(
                        child.text or ""
                        for child in element.iter()
                        if _local_name(child.tag) == "t"
                    ).strip()
                    omml_texts.append(text)
                elif local_name == "graphicData" and element.get("uri", "").endswith("/diagram"):
                    smartart_count += 1
                elif local_name == "oleObj":
                    ole_objects.append(
                        OLEMetadata(
                            name=element.get("name", "unknown") or "unknown",
                            prog_id=element.get("progId", "unknown") or "unknown",
                        )
                    )
            features[ordinal] = SlideFeatures(
                omml_texts=tuple(omml_texts),
                smartart_count=smartart_count,
                ole_objects=tuple(ole_objects),
                external_relationship_count=external_counts.get(ordinal, 0),
            )
    return features


def _validate_entry_name(name: str) -> None:
    if not name or "\\" in name or "\x00" in name:
        raise OOXMLSecurityError("OOXML_PATH_INVALID")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise OOXMLSecurityError("OOXML_PATH_INVALID")


def _is_macro_part(name: str) -> bool:
    lower_name = name.casefold()
    return lower_name.endswith("vbaproject.bin") or "/macrosheets/" in lower_name


def _read_xml(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    try:
        return archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile):
        raise OOXMLSecurityError("OOXML_ENTRY_READ_FAILED") from None


def _parse_xml(payload: bytes) -> etree._Element:
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise OOXMLSecurityError("OOXML_XML_UNSAFE")
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        recover=False,
        huge_tree=False,
    )
    try:
        return etree.fromstring(payload, parser=parser)
    except etree.XMLSyntaxError:
        raise OOXMLSecurityError("OOXML_XML_INVALID") from None


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: object) -> str:
    if not isinstance(tag, str) or not tag.startswith("{"):
        return ""
    return tag[1:].split("}", 1)[0]
