"""Same-generation evidence construction and honesty checks."""
from __future__ import annotations
from typing import Any, Optional
from .util import sha256_bytes
from .manifest import (
    GATE_ALLOWED,
    GATE_BLOCKED,
    STATUS_FAILED,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    STATUSES,
    ManifestLayer,
    ReleaseManifest,
)
from .model import (
    LayerGenerationCheck,
    LoadedLayer,
    SourceGenerationEvidence,
)
def build_source_generation_evidence(
    *,
    policy: str,
    manifest: Optional[ReleaseManifest],
    loaded: tuple[LoadedLayer, ...],
    manifest_read_consistent: Optional[bool],
    reread_by_layer: dict[str, bool],
    extra_issues: tuple[str, ...] = (),
) -> SourceGenerationEvidence:
    """Bind the parsed layer bytes to the manifest and decide the gate."""
    if manifest is None:
        checks = tuple(
            LayerGenerationCheck(
                layer_id=layer.config.layer_id,
                path=layer.config.path,
                source_format=layer.config.source_format,
                expected_sha256=None,
                actual_sha256=layer.input_file.sha256,
                status="not_applicable",
                reread_consistent=None,
                manifest_path=None,
            )
            for layer in loaded
        )
        issues = list(extra_issues)
        issues.append("未提供上游发布清单；来源代未证，不可作同代质量放行。")
        return SourceGenerationEvidence(
            policy=policy,
            status=STATUS_UNVERIFIED,
            quality_gate=GATE_BLOCKED,
            generation_id=None,
            manifest_path=None,
            manifest_sha256=None,
            manifest_digest=None,
            manifest_read_consistent=None,
            layer_checks=checks,
            issues=tuple(issues),
        )
    members = manifest.by_id()
    configured_ids = {layer.config.layer_id for layer in loaded}
    missing = tuple(sorted(configured_ids.difference(members)))
    extra = tuple(sorted(set(members).difference(configured_ids)))
    issues: list[str] = list(extra_issues)
    if manifest_read_consistent is False:
        issues.append("发布清单在本次审计期间发生变更；该清单不能作为不可分割的版本读取。")
    for layer_id in missing:
        issues.append(f"图层 {layer_id} 未出现在发布清单中。")
    for layer_id in extra:
        issues.append(f"发布清单成员 {layer_id} 不在配置图层集合中。")
    checks: list[LayerGenerationCheck] = []
    for layer in loaded:
        checks.append(_check_layer(layer, members.get(layer.config.layer_id), manifest, reread_by_layer, issues))
    verified = (
        manifest_read_consistent is True
        and not missing
        and not extra
        and not issues
        and all(item.status == STATUS_VERIFIED for item in checks)
    )
    status = STATUS_VERIFIED if verified else STATUS_FAILED
    return SourceGenerationEvidence(
        policy=policy,
        status=status,
        quality_gate=GATE_ALLOWED if verified else GATE_BLOCKED,
        generation_id=manifest.generation_id,
        manifest_path=manifest.manifest_path_text(),
        manifest_sha256=manifest.sha256,
        manifest_digest=manifest.digest,
        manifest_read_consistent=manifest_read_consistent,
        layer_checks=tuple(checks),
        missing_layer_ids=missing,
        extra_layer_ids=extra,
        issues=tuple(issues),
    )

def _check_layer(
    layer: LoadedLayer,
    member: Optional[ManifestLayer],
    manifest: ReleaseManifest,
    reread_by_layer: dict[str, bool],
    issues: list[str],
) -> LayerGenerationCheck:
    layer_id = layer.config.layer_id
    actual = layer.input_file.sha256
    reread = reread_by_layer.get(layer_id)
    if reread is False:
        issues.append(f"图层 {layer_id} 在读取后被替换，二次读取与解析所用字节不一致。")
    if member is None:
        return LayerGenerationCheck(
            layer_id=layer_id,
            path=layer.config.path,
            source_format=layer.config.source_format,
            expected_sha256=None,
            actual_sha256=actual,
            status="missing_from_manifest",
            reread_consistent=reread,
            manifest_path=None,
        )
    manifest_resolved = (manifest.base_dir / member.path).resolve()
    status = STATUS_VERIFIED
    if manifest_resolved != layer.config.resolved_path:
        status = "path_mismatch"
        issues.append(
            f"图层 {layer_id} 的配置路径与清单路径不一致（清单: {member.path}）。"
        )
    if member.source_format != layer.config.source_format:
        status = "format_mismatch" if status == STATUS_VERIFIED else status
        issues.append(
            f"图层 {layer_id} 的格式与清单不一致（清单: {member.source_format}）。"
        )
    if member.sha256 != actual:
        status = "digest_mismatch" if status == STATUS_VERIFIED else status
        issues.append(f"图层 {layer_id} 的原始字节摘要与清单不一致。")
    if reread is False and status == STATUS_VERIFIED:
        status = "reread_mismatch"
    return LayerGenerationCheck(
        layer_id=layer_id,
        path=layer.config.path,
        source_format=layer.config.source_format,
        expected_sha256=member.sha256,
        actual_sha256=actual,
        status=status,
        reread_consistent=reread,
        manifest_path=member.path,
    )

def source_generation_issues(payload: Any) -> list[str]:
    """Check internal honesty of a serialized source-generation evidence block."""
    if not isinstance(payload, dict):
        return ["source_generation 必须是 JSON 对象"]
    issues: list[str] = []
    status = payload.get("status")
    gate = payload.get("quality_gate")
    if status not in STATUSES:
        issues.append("source_generation.status 取值非法")
    if gate not in (GATE_ALLOWED, GATE_BLOCKED):
        issues.append("source_generation.quality_gate 取值非法")
    proven = payload.get("same_generation_proven") is True
    if proven != (status == STATUS_VERIFIED):
        issues.append("source_generation.same_generation_proven 与 status 不一致")
    if (gate == GATE_ALLOWED) != (status == STATUS_VERIFIED):
        issues.append("source_generation.quality_gate 只在同代已证时允许放行")
    checks = payload.get("layer_checks")
    if not isinstance(checks, list):
        issues.append("source_generation.layer_checks 必须是数组")
        checks = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            issues.append(f"source_generation.layer_checks[{index}] 必须是对象")
            continue
        if check.get("status") not in (
            STATUS_VERIFIED,
            "not_applicable",
            "missing_from_manifest",
            "path_mismatch",
            "format_mismatch",
            "digest_mismatch",
            "reread_mismatch",
        ):
            issues.append(f"source_generation.layer_checks[{index}].status 取值非法")
        if check.get("status") == STATUS_VERIFIED:
            if not check.get("expected_sha256") or check.get("expected_sha256") != check.get("actual_sha256"):
                issues.append(f"source_generation.layer_checks[{index}] 声称已证但摘要不一致")
            if check.get("reread_consistent") is not True:
                issues.append(f"source_generation.layer_checks[{index}] 声称已证但二次读取未确认")
    if status == STATUS_VERIFIED:
        if not payload.get("generation_id"):
            issues.append("同代已证缺少发布代 generation_id")
        if not payload.get("manifest_sha256") or not payload.get("manifest_digest"):
            issues.append("同代已证缺少清单摘要")
        if payload.get("manifest_read_consistent") is not True:
            issues.append("同代已证但清单读取一致性未确认")
        if payload.get("missing_layer_ids") or payload.get("extra_layer_ids"):
            issues.append("同代已证但清单成员集合与配置不一致")
        if payload.get("issues"):
            issues.append("同代已证但仍记录了核验问题")
        if not checks or any(
            isinstance(item, dict) and item.get("status") != STATUS_VERIFIED
            for item in checks
        ):
            issues.append("同代已证但存在未通过的图层核对")
    if status == STATUS_FAILED:
        has_negative = bool(
            payload.get("issues")
            or payload.get("missing_layer_ids")
            or payload.get("extra_layer_ids")
            or payload.get("manifest_read_consistent") is False
        )
        if not has_negative:
            issues.append("来源代核验失败却未记录任何失败证据")
        if checks and all(
            isinstance(item, dict) and item.get("status") == STATUS_VERIFIED
            for item in checks
        ) and not payload.get("missing_layer_ids") and not payload.get("extra_layer_ids") \
                and payload.get("manifest_read_consistent") is not False:
            issues.append("所有图层均已匹配却仍标记来源代核验失败")
    if status == STATUS_UNVERIFIED:
        if payload.get("generation_id") is not None or payload.get("manifest_sha256") is not None:
            issues.append("来源代未证却声明了发布代或清单摘要")
    return issues


def reread_layer_hashes(layers: tuple[LoadedLayer, ...]) -> dict[str, bool]:
    """Confirm each path still yields the exact bytes that were parsed."""
    result: dict[str, bool] = {}
    for layer in layers:
        try:
            current = layer.config.resolved_path.read_bytes()
        except OSError:
            result[layer.config.layer_id] = False
            continue
        result[layer.config.layer_id] = sha256_bytes(current) == layer.input_file.sha256
    return result

def manifest_was_replaced(path: Any, manifest: ReleaseManifest) -> bool:
    """Compare the manifest's parsed bytes with a post-load re-read."""
    try:
        raw = path.read_bytes()
    except OSError:
        return True
    return sha256_bytes(raw) != manifest.sha256
