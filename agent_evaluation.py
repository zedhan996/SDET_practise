"""运行版本化Agent行为评测，区分任务通过、工具执行成功和端到端耗时。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_evaluation_fixtures import (
    FIXTURE_PROFILES,
    FIXTURE_VERSION,
    INJECTED_PLANS,
    build_evaluation_environment,
)
from agent_mvp import EvaluationCase, evaluate_case


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CASES_PATH = PROJECT_ROOT / "eval_cases" / "agent_rag_v1.json"
DEFAULT_REPORT_DIRECTORY = PROJECT_ROOT / "reports" / "agent"
CATEGORIES = {
    "normal": "正常查询",
    "boundary": "边界与参数",
    "retrieval_failure": "检索失败",
    "tool_error": "工具异常",
    "prompt_injection": "恶意输出边界",
    "permission": "权限拒绝",
    "rejection": "安全拒答",
}
SCOPE_NOTES = [
    "商品数据、检索候选与重排分数使用固定替身；不读写dev.db或data/chroma。",
    "实际执行参数解析、Registry白名单/权限/Schema、重排排序和拒答门禁；不调用答案生成模型。",
    "offline使用规则Planner；ollama模式也只有Planner是真实模型，不是完整RAG端到端评测。",
    "三个恶意/错误输出case始终使用injected_output，不能证明模型能识别提示注入或恶意文档。",
    "TIMEOUT场景注入错误结果，不证明真实超时等待或线程取消。",
    "知识query允许改写；当前只校验工具Schema和非空输入，语义是否保持需人工复核。",
    "端到端耗时仅包含Planner→解析→Registry，不含依赖初始化、报告生成或最终答案生成。",
    "人工复核初始均为pending；自动断言通过不等于已完成人工复核。",
]


@dataclass(frozen=True)
class VersionedCase:
    """在已有EvaluationCase外增加分类、依赖配置和人工复核标记。"""

    case: EvaluationCase
    category: str
    description: str
    fixture: str
    expected_handler_calls: int
    manual_review: bool


@dataclass(frozen=True)
class EvaluationSuite:
    """保存用例版本和文件指纹，帮助确认两次报告是否使用了同一批输入。"""

    suite_id: str
    suite_version: str
    fixture_version: str
    source_path: str
    source_sha256: str
    cases: tuple[VersionedCase, ...]


def _object(value, required: set[str], optional: set[str], label: str) -> dict:
    """拒绝缺失字段与拼错的未知字段，防止用例悄悄失去断言。"""
    if not isinstance(value, dict):
        raise ValueError(f"{label}必须是对象")
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing or unknown:
        raise ValueError(f"{label}字段错误：缺少{sorted(missing)}，未知{sorted(unknown)}")
    return value


def _text(value, label: str, identifier: bool = False) -> str:
    """普通文字要求非空；写入文件名和trace的标识符另限制字符。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}必须是非空字符串")
    if identifier and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
        raise ValueError(f"{label}只能使用不超过80位的字母、数字、点、横线和下划线")
    return value


def _strings(value, label: str) -> list[str]:
    """权限和内容关键字均使用字符串列表，不接受单个字符串冒充列表。"""
    if not isinstance(value, list):
        raise ValueError(f"{label}必须是字符串列表")
    for item in value:
        _text(item, label)
    if len(set(value)) != len(value):
        raise ValueError(f"{label}不能重复")
    return value


def _reject_constant(value: str):
    """JSON中不接受NaN或Infinity，否则数值断言与统计会失真。"""
    raise ValueError(f"不支持的JSON数值：{value}")


def load_suite(path: str | Path = DEFAULT_CASES_PATH) -> EvaluationSuite:
    """加载并校验版本化用例；expected只交给断言，不传入工具依赖。"""
    source = Path(path).resolve()
    raw = source.read_bytes()
    document = json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_constant)
    _object(
        document,
        {"schema_version", "suite_id", "suite_version", "fixture_version", "cases"},
        set(),
        "评测集",
    )
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("只支持schema_version=1")
    suite_id = _text(document["suite_id"], "suite_id", identifier=True)
    version = _text(document["suite_version"], "suite_version", identifier=True)
    if document["fixture_version"] != FIXTURE_VERSION:
        raise ValueError(f"fixture_version必须为{FIXTURE_VERSION}")
    if not isinstance(document["cases"], list) or not document["cases"]:
        raise ValueError("cases必须是非空列表")

    cases = []
    seen_ids = set()
    for entry in document["cases"]:
        _object(
            entry,
            {"case_id", "category", "description", "fixture", "manual_review",
             "user_text", "permissions", "expected"},
            set(),
            "case",
        )
        case_id = _text(entry["case_id"], "case_id", identifier=True)
        if case_id in seen_ids:
            raise ValueError(f"case_id重复：{case_id}")
        seen_ids.add(case_id)
        if _text(entry["category"], "category") not in CATEGORIES:
            raise ValueError(f"未知category：{entry['category']}")
        if _text(entry["fixture"], "fixture") not in FIXTURE_PROFILES:
            raise ValueError(f"未知fixture：{entry['fixture']}")
        if type(entry["manual_review"]) is not bool:
            raise ValueError("manual_review必须是布尔值")

        expected = _object(
            entry["expected"],
            {"tool", "arguments", "ok", "error_type", "handler_calls"},
            {"data", "content_terms"},
            f"{case_id}.expected",
        )
        if type(expected["ok"]) is not bool:
            raise ValueError("expected.ok必须是布尔值")
        if expected["ok"]:
            if expected["error_type"] is not None:
                raise ValueError("expected.ok为true时，error_type必须为null")
        else:
            _text(expected["error_type"], "expected.error_type")
        for field_name in ("arguments", "data"):
            value = expected.get(field_name)
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"expected.{field_name}必须是对象或null")
        if type(expected["handler_calls"]) is not int or expected["handler_calls"] not in {0, 1}:
            raise ValueError("单步Agent的handler_calls必须为整数0或1")
        cases.append(VersionedCase(
            case=EvaluationCase(
                case_id=case_id,
                user_text=_text(entry["user_text"], "user_text"),
                permissions=frozenset(_strings(entry["permissions"], "permissions")),
                expected_tool=_text(expected["tool"], "expected.tool"),
                expected_arguments=expected["arguments"],
                expected_ok=expected["ok"],
                expected_error_type=expected["error_type"],
                expected_data=expected.get("data"),
                expected_content_terms=tuple(
                    _strings(expected.get("content_terms", []), "content_terms")
                ),
            ),
            category=entry["category"],
            description=_text(entry["description"], "description"),
            fixture=entry["fixture"],
            expected_handler_calls=expected["handler_calls"],
            manual_review=entry["manual_review"],
        ))
    return EvaluationSuite(
        suite_id=suite_id,
        suite_version=version,
        fixture_version=FIXTURE_VERSION,
        source_path=str(source),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        cases=tuple(cases),
    )


def summarize_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """预期拒绝也能使任务通过，但工具调用成功率只按实际ok值计算。"""
    evaluations = [row["actual"] for row in rows]
    total = len(evaluations)
    passed = sum(item["passed"] is True for item in evaluations)
    attempts = [item for item in evaluations if item["tool_call_attempted"] is True]
    successes = sum(item["result"]["ok"] is True for item in attempts)
    latencies = sorted(
        item["end_to_end_ms"] for item in evaluations
        if item["end_to_end_ms"] is not None
    )
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "task_pass_rate": passed / total if total else None,
        "tool_call_attempts": len(attempts),
        "tool_call_successes": successes,
        "tool_call_success_rate": successes / len(attempts) if attempts else None,
        "unknown_call_outcomes": sum(item["tool_call_attempted"] is None for item in evaluations),
        "latency": {
            "unit": "ms",
            "measured_cases": len(latencies),
            "mean_ms": sum(latencies) / len(latencies) if latencies else None,
            # 使用最近秩法；小样本P95仅作观察，不作为性能达标结论。
            "p95_ms": latencies[math.ceil(0.95 * len(latencies)) - 1] if latencies else None,
        },
    }


def run_suite(suite: EvaluationSuite, planner_mode: str = "offline") -> dict[str, Any]:
    """按case隔离执行，保存实际输出，且为每轮生成互不重复的trace。"""
    if planner_mode not in {"offline", "ollama"}:
        raise ValueError("planner只能是offline或ollama")
    if planner_mode == "ollama" and os.getenv("RUN_OLLAMA_INTEGRATION") != "1":
        raise ValueError("真实Planner须显式设置RUN_OLLAMA_INTEGRATION=1")
    run_id = uuid.uuid4().hex
    rows = []
    for entry in suite.cases:
        case = entry.case
        trace_id = f"eval-{run_id}-{case.case_id}"
        planner_kind = "injected_output" if entry.fixture in INJECTED_PLANS else (
            "ollama" if planner_mode == "ollama" else "offline_rules"
        )
        environment = None
        planner_model = None
        try:
            environment = build_evaluation_environment(entry.fixture, planner_mode)
            planner_kind = environment.planner_kind
            planner_model = getattr(environment.agent.planner, "model", None)
            actual = asdict(evaluate_case(case, environment.agent, trace_id=trace_id))
            actual["checks"]["handler_calls"] = (
                len(environment.handler_calls) == entry.expected_handler_calls
            )
            if case.expected_tool == "search_knowledge" and case.expected_ok:
                data = actual["result"]["data"]
                actual["checks"]["knowledge_trace_id"] = (
                    isinstance(data, dict) and data.get("trace_id") == trace_id
                )
            actual["passed"] = all(actual["checks"].values())
        except Exception as exc:
            # Harness自身异常不能伪装成预期工具错误，也不猜测是否已经进入Registry。
            actual = {
                "case_id": case.case_id, "passed": False, "checks": {"execution": False},
                "actual_tool": None, "actual_arguments": None,
                "tool_call_attempted": None, "end_to_end_ms": None,
                "result": {
                    "ok": False, "data": None, "error_type": "EVALUATION_ERROR",
                    "message": f"{type(exc).__name__}: {exc}", "trace": None,
                },
            }
        rows.append({
            "case_id": case.case_id,
            "category": entry.category,
            "description": entry.description,
            "fixture": entry.fixture,
            "planner_kind": planner_kind,
            "planner_model": planner_model,
            "trace_id": trace_id,
            "input": {"user_text": case.user_text, "permissions": sorted(case.permissions)},
            "expected": {
                "tool": case.expected_tool, "arguments": case.expected_arguments,
                "ok": case.expected_ok, "error_type": case.expected_error_type,
                "data": case.expected_data, "content_terms": list(case.expected_content_terms),
                "handler_calls": entry.expected_handler_calls,
            },
            "actual": actual,
            "handler_calls": list(environment.handler_calls) if environment else [],
            "failed_checks": [key for key, value in actual["checks"].items() if not value],
            "manual_review": {
                "status": "pending" if entry.manual_review else "not_selected",
                "notes": "",
            },
        })
    return {
        "report_schema_version": 1,
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "suite": {
            "id": suite.suite_id, "version": suite.suite_version,
            "fixture_version": suite.fixture_version,
            "source_path": suite.source_path, "source_sha256": suite.source_sha256,
        },
        "requested_planner": planner_mode,
        "scope_notes": list(SCOPE_NOTES),
        "summary": summarize_results(rows),
        "by_planner": {
            kind: summarize_results([row for row in rows if row["planner_kind"] == kind])
            for kind in sorted({row["planner_kind"] for row in rows})
        },
        "manual_review_pending": sum(row["manual_review"]["status"] == "pending" for row in rows),
        "results": rows,
    }


def _rate(value: float | None) -> str:
    """分母为零时显示不适用，不伪造百分比。"""
    return "不适用" if value is None else f"{value:.2%}"


def _number(value: float | None) -> str:
    """没有测到的耗时与真实零耗时分别展示。"""
    return "未测得" if value is None else f"{value:.3f}"


def _cell(value) -> str:
    """避免用户文字或模型输出破坏Markdown表格。"""
    return str(value).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def render_markdown(report: dict[str, Any]) -> str:
    """生成简明总览与人工复核清单；完整输入/预期/实际值保存在JSON中。"""
    summary = report["summary"]
    lines = [
        "# Agent/RAG行为评测报告", "",
        f"用例版本：{report['suite']['id']} / {report['suite']['version']}；"
        f"依赖版本：{report['suite']['fixture_version']}", "",
        f"运行ID：{report['run_id']}；生成时间（UTC）：{report['created_at_utc']}", "",
        f"用例文件SHA256：{report['suite']['source_sha256']}", "",
        "## 测试范围", "",
        *[f"- {note}" for note in report["scope_notes"]], "",
        "## 汇总", "",
        f"- 任务通过：{summary['passed']}/{summary['total']}（{_rate(summary['task_pass_rate'])}）",
        f"- 工具调用成功：{summary['tool_call_successes']}/{summary['tool_call_attempts']}"
        f"（{_rate(summary['tool_call_success_rate'])}）",
        f"- 平均端到端耗时：{_number(summary['latency']['mean_ms'])} ms；"
        f"P95：{_number(summary['latency']['p95_ms'])} ms",
        f"- 无法确认调用阶段的Harness异常：{summary['unknown_call_outcomes']}；"
        f"待人工复核：{report['manual_review_pending']}", "",
        "工具调用分母只统计已进入Registry的请求，包含其拒绝的坏参数、越权和未知工具。"
        "解析阶段失败不进入分母。安全拒答的ok=True算工具成功；"
        "预期权限拒绝的ok=False不算工具成功，但可以算任务通过。", "",
        "| Planner类型 | 用例数 | 任务通过率 | 工具成功率 | 平均耗时ms | P95 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for kind, metrics in report["by_planner"].items():
        lines.append(
            f"| {kind} | {metrics['total']} | {_rate(metrics['task_pass_rate'])} | "
            f"{_rate(metrics['tool_call_success_rate'])} | "
            f"{_number(metrics['latency']['mean_ms'])} | "
            f"{_number(metrics['latency']['p95_ms'])} |"
        )
    lines.extend([
        "", "## 逐条结果", "",
        "| case_id | 类别 | 预期工具 → 实际工具 | 判定 | 实际错误码 | 耗时ms | 失败检查 |",
        "| --- | --- | --- | --- | --- | ---: | --- |",
    ])
    for row in report["results"]:
        actual = row["actual"]
        values = [
            row["case_id"], CATEGORIES[row["category"]],
            f"{row['expected']['tool']} → {actual['actual_tool']}",
            "通过" if actual["passed"] else "失败",
            actual["result"]["error_type"] or "无",
            _number(actual["end_to_end_ms"]),
            ", ".join(row["failed_checks"]) or "无",
        ]
        lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    lines.extend([
        "", "## 人工复核（尚未完成）", "",
        "对照同目录results.json，检查输入语义、实际参数、数据/来源、拒答或错误原因，"
        "再填写结论。特别注意：知识query即使非空，也可能改写失真。", "",
    ])
    for row in report["results"]:
        if row["manual_review"]["status"] == "pending":
            lines.append(f"- [ ] {row['case_id']}：{row['description']} 复核结论：待填写。")
    lines.extend(["", "本报告不代表真实检索模型质量、最终答案正确率或生产性能，也没有替你完成人工复核。", ""])
    return "\n".join(lines)


def write_reports(
    report: dict[str, Any],
    output_directory: str | Path = DEFAULT_REPORT_DIRECTORY,
) -> tuple[Path, Path]:
    """每轮单独建目录，拒绝覆盖已存在的同一轮报告。"""
    folder_name = f"{report['suite']['id']}-{report['suite']['version']}-{report['run_id']}"
    directory = Path(output_directory).resolve() / folder_name
    directory.mkdir(parents=True, exist_ok=False)
    json_path = directory / "results.json"
    markdown_path = directory / "report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def main(argv: list[str] | None = None) -> int:
    """默认完全离线；失败用例退出1，配置或报告写入错误退出2。"""
    parser = argparse.ArgumentParser(description="运行Agent/RAG版本化行为评测")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--planner", choices=("offline", "ollama"), default="offline")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    args = parser.parse_args(argv)
    try:
        report = run_suite(load_suite(args.cases), args.planner)
        json_path, markdown_path = write_reports(report, args.output_dir)
    except (OSError, ValueError) as exc:
        print(f"评测未完成：{exc}", file=sys.stderr)
        return 2
    for row in report["results"]:
        print(
            f"{'PASS' if row['actual']['passed'] else 'FAIL'} | {row['case_id']} | "
            f"{row['actual']['actual_tool']} | {','.join(row['failed_checks']) or '-'}"
        )
    summary = report["summary"]
    print(f"任务通过率：{summary['passed']}/{summary['total']} = {_rate(summary['task_pass_rate'])}")
    print(
        f"工具调用成功率：{summary['tool_call_successes']}/{summary['tool_call_attempts']}"
        f" = {_rate(summary['tool_call_success_rate'])}"
    )
    print(
        f"平均端到端耗时：{_number(summary['latency']['mean_ms'])} ms；"
        f"P95：{_number(summary['latency']['p95_ms'])} ms"
    )
    print(f"待人工复核：{report['manual_review_pending']} 条；本次是固定依赖行为评测，不是完整RAG语义质量评测。")
    print(f"JSON：{json_path}\nMarkdown：{markdown_path}")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
