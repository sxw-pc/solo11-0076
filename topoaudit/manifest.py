"""Atomic upstream release-manifest parsing and same-generation evidence.

A manifest is an upstream-declared, indivisible description of one release
generation: it names every configured layer, its relative path, format, and
the SHA-256 of the exact raw bytes the upstream published.  The loader reads
the manifest once, parses that same byte sequence, and binds every parsed
layer byte sequence to the manifest entry.  A combined ``input_digest`` alone
is content identity only; it is never treated as a same-generation proof.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .errors import ConfigError, InputError
from .model import SourceGenerationConfig
from .util import canonical_json, sha256_bytes, sha256_text
MANIFEST_SCHEMA_VERSION = "1.0"
POLICY_OBSERVE = "observe"
POLICY_REQUIRE = "require"
POLICIES = (POLICY_OBSERVE, POLICY_REQUIRE)
STATUS_VERIFIED = "verified"
STATUS_UNVERIFIED = "unverified"
STATUS_FAILED = "failed"
STATUSES = (STATUS_VERIFIED, STATUS_UNVERIFIED, STATUS_FAILED)
GATE_ALLOWED = "allowed"
GATE_BLOCKED = "blocked"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

@dataclass(frozen=True)
class ManifestLayer:
    layer_id: str
    path: str
    source_format: str
    sha256: str
    def as_public_dict(self) -> dict[str, str]:
        return {
            "id": self.layer_id,
            "path": self.path,
            "format": self.source_format,
            "sha256": self.sha256,
        }

@dataclass(frozen=True)
class ReleaseManifest:
    generation_id: str
    layers: tuple[ManifestLayer, ...]
    sha256: str
    digest: str
    base_dir: Path
    relative_path: str
    def by_id(self) -> dict[str, ManifestLayer]:
        return {item.layer_id: item for item in self.layers}
    def manifest_path_text(self) -> str:
        return self.relative_path
    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "generation_id": self.generation_id,
            "layers": [item.as_public_dict() for item in self.layers],
        }

def parse_source_generation(
    raw: Any, path: str, base_dir: Path
) -> SourceGenerationConfig:
    """Parse the optional top-level ``source_generation`` configuration."""
    if raw is None:
        return SourceGenerationConfig()
    if not isinstance(raw, dict):
        raise ConfigError("TOPO-CFG-010", "source_generation 必须是 JSON 对象", path)
    for key in raw:
        if key not in {"policy", "manifest"}:
            raise ConfigError(
                "TOPO-CFG-010", f"未知 source_generation 字段: {key}", f"{path}.{key}"
            )
    manifest = raw.get("manifest")
    policy = raw.get("policy")
    if policy is None:
        # Supplying a manifest is a request to prove same-generation input;
        # observation-only runs must opt in explicitly.
        policy = POLICY_REQUIRE if manifest is not None else POLICY_OBSERVE
    if not isinstance(policy, str) or policy not in POLICIES:
        raise ConfigError(
            "TOPO-CFG-010",
            "source_generation.policy 只能是 observe 或 require",
            f"{path}.policy",
        )
    if manifest is not None:
        if not isinstance(manifest, str) or not manifest.strip():
            raise ConfigError(
                "TOPO-CFG-010",
                "source_generation.manifest 必须是非空字符串",
                f"{path}.manifest",
            )
        resolved = (base_dir / manifest).resolve()
    else:
        resolved = None
    if policy == POLICY_REQUIRE and manifest is None:
        raise ConfigError(
            "TOPO-CFG-010", "policy=require 必须同时提供 manifest", f"{path}.manifest"
        )
    return SourceGenerationConfig(
        policy=policy, manifest_path=manifest, resolved_manifest_path=resolved
    )

def read_manifest(path: Path, locator: str) -> ReleaseManifest:
    """Read, hash, and validate a release manifest from one byte sequence."""
    if not path.is_file():
        raise InputError("TOPO-INP-009", "发布清单不存在或不是普通文件", locator)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError("TOPO-INP-010", f"无法读取发布清单: {exc}", locator) from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError("TOPO-INP-011", "发布清单不是有效 UTF-8 文本", locator) from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        where = f"line {exc.lineno}, column {exc.colno}"
        raise InputError("TOPO-INP-011", f"发布清单 JSON 解析失败 ({where})", locator) from exc
    layers = _parse_manifest(parsed, locator)
    manifest = ReleaseManifest(
        generation_id=parsed["generation_id"],
        layers=layers,
        sha256=sha256_bytes(raw),
        digest=sha256_text(
            canonical_json(
                {
                    "schema_version": MANIFEST_SCHEMA_VERSION,
                    "generation_id": parsed["generation_id"],
                    "layers": [
                        item.as_public_dict()
                        for item in sorted(layers, key=lambda value: value.layer_id)
                    ],
                }
            )
        ),
        base_dir=path.parent,
        relative_path=locator,
    )
    return manifest

def _parse_manifest(raw: Any, locator: str) -> tuple[ManifestLayer, ...]:
    if not isinstance(raw, dict):
        raise InputError("TOPO-INP-011", "发布清单根节点必须是 JSON 对象", locator)
    for key in raw:
        if key not in {"schema_version", "generation_id", "layers"}:
            raise InputError(
                "TOPO-INP-011", f"发布清单存在未知字段: {key}", f"{locator}.{key}"
            )
    if raw.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise InputError(
            "TOPO-INP-011", "发布清单 schema_version 不受支持", f"{locator}.schema_version"
        )
    generation_id = raw.get("generation_id")
    if not isinstance(generation_id, str) or not generation_id.strip():
        raise InputError(
            "TOPO-INP-011", "发布清单 generation_id 必须是非空字符串", f"{locator}.generation_id"
        )
    layers_raw = raw.get("layers")
    if not isinstance(layers_raw, list) or not layers_raw:
        raise InputError("TOPO-INP-011", "发布清单 layers 必须是非空数组", f"{locator}.layers")
    layers: list[ManifestLayer] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(layers_raw):
        where = f"{locator}.layers[{index}]"
        if not isinstance(item, dict):
            raise InputError("TOPO-INP-011", "发布清单图层成员必须是对象", where)
        for key in item:
            if key not in {"id", "path", "format", "sha256"}:
                raise InputError("TOPO-INP-011", f"未知清单图层字段: {key}", f"{where}.{key}")
        layer_id = _nonempty(item.get("id"), f"{where}.id")
        if layer_id in seen_ids:
            raise InputError("TOPO-INP-011", f"发布清单图层 id 重复: {layer_id}", f"{where}.id")
        seen_ids.add(layer_id)
        member_path = _nonempty(item.get("path"), f"{where}.path")
        if Path(member_path).is_absolute():
            raise InputError("TOPO-INP-011", "发布清单图层路径必须是相对路径", f"{where}.path")
        source_format = item.get("format")
        if source_format not in ("geojson", "csv"):
            raise InputError(
                "TOPO-INP-011", "发布清单 format 只能是 geojson 或 csv", f"{where}.format"
            )
        digest = item.get("sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise InputError(
                "TOPO-INP-011", "发布清单 sha256 必须是 64 位小写十六进制", f"{where}.sha256"
            )
        layers.append(
            ManifestLayer(
                layer_id=layer_id,
                path=member_path,
                source_format=source_format,
                sha256=digest,
            )
        )
    return tuple(layers)

def _nonempty(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError("TOPO-INP-011", "发布清单字段必须是非空字符串", path)
    return value
