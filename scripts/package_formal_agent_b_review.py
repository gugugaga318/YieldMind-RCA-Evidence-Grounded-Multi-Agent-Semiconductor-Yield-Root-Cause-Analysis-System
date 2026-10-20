# ruff: noqa: E501
"""Package the Agent-B-only Ground Truth review packet for formal blind V1 R2."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / ".blind_evaluation" / "formal_v1_candidate_r2"
PACKET = CANDIDATE / "agent_b_packet"
ARCHIVE = CANDIDATE / "agent_b_packet.zip"
DATASET_ID = "synthetic-semiconductor-formal-blind-v1-candidate-r2"
CASE_IDS = tuple(f"FORMAL_{index:03d}" for index in range(1, 25))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _task() -> str:
    return """你是独立的半导体 RCA 专业 Ground Truth 审核 Agent（Agent B）。

前置状态：Agent A 已仅根据 public/ 完成难度、泄漏和一致性审核，24/24 PASS，blocking_issues 为空。本轮不是再次判断题目难不难，而是审核 hidden/ground_truth.json 中的答案是否符合半导体工艺、是否被公开原始数据支持、是否具有正确的结论等级和影响范围。

你可以查看 public/、hidden/、agent_a_review/ 和 manifests/。不得运行或修改 RCA 系统，不得修改任何数据，不得把 Ground Truth 提供给 RCA 系统或其开发 Agent，也不要为了适应当前系统能力而降低审核标准。

请逐案例执行以下审核：
1. 因果机理：原因是否能合理产生题面现象；上游到下游传播是否符合工艺顺序和半导体常识。
2. 证据支持：hidden 根因不能只是作者声明。必须列出 FDC、MES/process history、Defect、Metrology、WAT、前驱量测、控制和排他证据。
3. 根因粒度：公开数据若只能证明某 Lane/设备/Recipe/参数异常，不得升级成未被仪器记录支持的具体部件失效。逐题给出可评分的根因措辞。
4. 时间线：原因、暴露和前驱必须早于检测；关键 FDC measured_at 必须在对应 process_history 窗口内。
5. supported（FORMAL_001–014）：必须同时具有现象、Source 当前证据、同设备/Chamber或Recipe暴露相关性、异常 peer、正常/恢复控制和排除同强度候选的证据。不足时应降级。
6. inconclusive（FORMAL_015–020）：必须真的存在无法消除的同强度候选；核查缺失的 split/复测/前驱证据是否确实能区分候选。若公开数据已唯一确认 Lane，则状态错误。
7. insufficient_evidence（FORMAL_021–024）：关键遥测或谱系必须真的缺失且不可替代；不得仅因存在一个可见 OOC 就把它升级为根因。
8. 影响范围：逐个验证 expected_impact_lots。范围必须满足因果暴露、设备/Chamber或Recipe Scope、时间窗口和兼容结果的交集；困难负例、仅有症状的 Lot、暴露正常控制不得误列入。
9. 困难负例：先枚举每题所有 symptom_hard_negative_lots，再逐 Lot 核对严重量测/缺陷、设备链、Recipe和 FDC。不要只抽查一个 Lot，也不要把严重困难负例误读成正常控制。
10. 替代原因：若存在证据强度相同且工艺合理的替代根因，写入 alternative_causes，并判断是否破坏唯一可评分性。
11. 工艺合理性：核查参数名、单位、幅度、空间分布、片数和 Lot/Wafer 关系。Synthetic 可以理想化，但不能违反基本工艺逻辑。
12. 独立性与措辞：核对 prior_development_and_pilot_mechanisms.md，检查是否只是既有机制同义改写；Ground Truth 措辞不得超过 proof_granularity。

特别注意：
- observation Module 不等于 causal Module。
- observed_abnormal_peer_lots 不自动等于 confirmed impact Lots。
- 对 inconclusive/insufficient 案例，expected_impact_lots 应保持空；除非你能证明状态本身应改成 supported。
- 任何原始 CSV 事实与 Ground Truth 冲突，都必须逐行引用并判 REVISE/REJECT，不能用概括性推测覆盖。

请填写 agent_b_review_template.json，并返回：
- agent_b_review.json
- agent_b_report.md

每题 decision 只能是 PASS、REVISE 或 REJECT。24题必须全部 PASS、batch_required_changes 为空，才可 recommend_freeze=true。任何一题非 PASS，都不要建议冻结，也不要建议运行 RCA 正式盲测。
"""


def _template() -> dict[str, Any]:
    return {
        "schema_version": "4.0",
        "dataset_id": DATASET_ID,
        "reviewer_role": "semiconductor_ground_truth",
        "reviews": [
            {
                "case_id": case_id,
                "decision": "PENDING",
                "mechanism_valid": None,
                "mechanism_granularity_valid": None,
                "timeline_valid": None,
                "public_data_supports_truth": None,
                "hard_negative_classification_valid": None,
                "controls_valid": None,
                "alternative_causes": [],
                "uniquely_scorable": None,
                "impact_scope_valid": None,
                "correct_status": "PENDING",
                "recommended_root_cause_wording": "",
                "recommended_impact_lots": [],
                "required_changes": [],
                "reason": "",
            }
            for case_id in CASE_IDS
        ],
        "batch_decision": "PENDING",
        "recommend_freeze": None,
        "batch_required_changes": [],
        "summary": "",
    }


def _zip(source: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = str(path.relative_to(source)).replace("\\", "/")
            info = zipfile.ZipInfo(relative, date_time=(2026, 8, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def package(agent_a_review: Path, agent_a_report: Path) -> None:
    review = json.loads(agent_a_review.read_text(encoding="utf-8"))
    if review.get("dataset_id") != DATASET_ID:
        raise ValueError("Agent A dataset_id does not match formal R2")
    if review.get("batch_decision") != "PASS":
        raise ValueError("Agent A batch did not pass")
    if review.get("blocking_issues"):
        raise ValueError("Agent A still has blocking issues")
    if review.get("recommend_agent_b") is not True:
        raise ValueError("Agent A did not recommend Agent B")
    reviews = review.get("reviews", [])
    if {row.get("case_id") for row in reviews} != set(CASE_IDS):
        raise ValueError("Agent A case set is incomplete")
    if any(row.get("decision") != "PASS" for row in reviews):
        raise ValueError("Not every Agent A case passed")

    if PACKET.exists():
        shutil.rmtree(PACKET)
    if ARCHIVE.exists():
        ARCHIVE.unlink()

    shutil.copytree(CANDIDATE / "public", PACKET / "public")
    shutil.copytree(CANDIDATE / "hidden", PACKET / "hidden")
    (PACKET / "agent_a_review").mkdir(parents=True, exist_ok=True)
    (PACKET / "manifests").mkdir(parents=True, exist_ok=True)
    shutil.copy2(agent_a_review, PACKET / "agent_a_review" / "agent_a_review.json")
    shutil.copy2(agent_a_report, PACKET / "agent_a_review" / "agent_a_report.md")
    shutil.copy2(CANDIDATE / "candidate_manifest.json", PACKET / "manifests" / "candidate_manifest.json")
    shutil.copy2(CANDIDATE / "automated_quality_gate.json", PACKET / "manifests" / "automated_quality_gate.json")
    (PACKET / "TASK_AGENT_B.txt").write_text(_task(), encoding="utf-8-sig")
    _write_json(PACKET / "agent_b_review_template.json", _template())
    _write_json(
        PACKET / "manifests" / "agent_a_approval.json",
        {
            "dataset_id": DATASET_ID,
            "agent_a_packet_sha256": _sha(CANDIDATE / "agent_a_packet.zip"),
            "agent_a_review_sha256": _sha(agent_a_review),
            "agent_a_report_sha256": _sha(agent_a_report),
            "batch_decision": review["batch_decision"],
            "blocking_issues": review["blocking_issues"],
            "recommend_agent_b": review["recommend_agent_b"],
        },
    )
    _zip(PACKET, ARCHIVE)
    _write_json(
        CANDIDATE / "agent_b_packet_hashes.json",
        {
            "agent_b_packet.zip": _sha(ARCHIVE),
            "agent_a_review.json": _sha(agent_a_review),
            "agent_a_report.md": _sha(agent_a_report),
        },
    )


if __name__ == "__main__":
    package(
        Path(r"C:\Users\ybt\.trae-cn\agent_a_packet\agent_a_review.json"),
        Path(r"C:\Users\ybt\.trae-cn\agent_a_packet\agent_a_report.md"),
    )
