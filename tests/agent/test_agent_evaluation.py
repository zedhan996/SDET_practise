"""版本化评测集与Harness测试；不请求Ollama，不加载真实检索模型。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from app.agent import evaluation as agent_evaluation
from app.agent.evaluation import (
    CATEGORIES,
    DEFAULT_CASES_PATH,
    load_suite,
    main,
    run_suite,
    summarize_results,
    write_reports,
)
from app.agent.evaluation_fixtures import build_evaluation_environment
from app.agent.mvp import (
    EvaluationCase,
    ToolCall,
    ToolCallingAgent,
    ToolExecutionResult,
    TraceEvent,
    _matches_expected_data,
    evaluate_case,
)


CASE_IDS = [entry.case.case_id for entry in load_suite().cases]


@pytest.fixture(scope="module")
def suite():
    """一次加载冻结的v1用例，供本文件各测试复用。"""
    return load_suite()


@pytest.fixture(scope="module")
def offline_report(suite):
    """批量评测只执行一次；所有依赖仍是固定测试替身。"""
    return run_suite(suite)


def find_row(report, case_id):
    """通过稳定的case_id定位结果，不依赖报告中的行号。"""
    return next(row for row in report["results"] if row["case_id"] == case_id)


def test_suite_contains_fifteen_versioned_cases_and_five_reviews(suite):
    """检查v1覆盖七类场景、15个唯一用例，并选出5条待人工复核样例。"""
    assert len(suite.cases) == len(set(CASE_IDS)) == 15
    assert {entry.category for entry in suite.cases} == set(CATEGORIES)
    assert sum(entry.manual_review for entry in suite.cases) == 5
    assert suite.suite_version == "v1"
    assert suite.fixture_version == "offline-v1"
    assert len(suite.source_sha256) == 64


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_versioned_behavior_case(case_id, offline_report):
    """逐条展示版本化case的结果，联动Planner、Registry和实际RAG门禁。"""
    row = find_row(offline_report, case_id)
    assert row["actual"]["passed"], json.dumps(row, ensure_ascii=False, indent=2)
    assert row["failed_checks"] == []
    assert row["actual"]["result"]["trace"]["trace_id"] == row["trace_id"]


def test_task_pass_rate_is_not_tool_success_rate(offline_report):
    """15个任务可全通过，但故意触发的拒绝/异常使工具成功率不是100%。"""
    summary = offline_report["summary"]
    assert summary["passed"] == summary["total"] == 15
    assert summary["task_pass_rate"] == 1.0
    assert summary["tool_call_attempts"] == 14
    assert summary["tool_call_successes"] == 7
    assert summary["tool_call_success_rate"] == 0.5
    assert summary["latency"]["measured_cases"] == 15
    assert offline_report["by_planner"]["offline_rules"]["total"] == 12
    assert offline_report["by_planner"]["injected_output"]["total"] == 3


def test_wrong_expectation_fails_without_changing_fixture(suite):
    """故意把预期ID写成102，实际仍返回101，防止依赖按期望答案造结果。"""
    entry = suite.cases[0]
    wrong_case = replace(entry.case, expected_data={"data": {"id": 102}})
    changed_suite = replace(suite, cases=(replace(entry, case=wrong_case),))
    row = run_suite(changed_suite)["results"][0]
    assert row["actual"]["result"]["data"]["data"]["id"] == 101
    assert row["actual"]["passed"] is False
    assert row["failed_checks"] == ["data"]


def test_safe_rejection_removes_candidate_content(offline_report):
    """低重排分数应得到成功执行的拒答结果，不能把候选交给后续模块。"""
    row = find_row(offline_report, "knowledge-low-score-001")
    result = row["actual"]["result"]
    assert result["ok"] is True
    assert result["data"]["answerable"] is False
    assert result["data"]["content"] is None
    assert result["data"]["source"] is None
    assert result["data"]["rejection_reason"] == "LOW_RERANKER_SCORE"
    assert result["data"]["trace_id"] == row["trace_id"]


def test_permission_denial_never_reaches_handler(offline_report):
    """检查授权失败发生在业务处理前，不能只断言错误码就结束。"""
    row = find_row(offline_report, "knowledge-permission-001")
    assert row["actual"]["passed"] is True
    assert row["actual"]["result"]["ok"] is False
    assert row["actual"]["result"]["error_type"] == "PERMISSION_DENIED"
    assert row["handler_calls"] == []


def test_missing_knowledge_trace_is_a_task_failure(suite, monkeypatch):
    """模拟RAG结果漏掉trace，防止只检查外层trace就误判跨层追踪正确。"""
    entry = next(entry for entry in suite.cases if entry.case.case_id == "knowledge-auth-001")

    def remove_knowledge_trace(*args, **kwargs):
        evaluation = evaluate_case(*args, **kwargs)
        data = dict(evaluation.result.data)
        del data["trace_id"]
        return replace(evaluation, result=replace(evaluation.result, data=data))

    monkeypatch.setattr(agent_evaluation, "evaluate_case", remove_knowledge_trace)
    row = run_suite(replace(suite, cases=(entry,)))["results"][0]
    assert row["failed_checks"] == ["knowledge_trace_id"]


def test_unexpected_handler_call_is_a_task_failure(suite, monkeypatch):
    """模拟错误的提前调用记录，验证即使错误码正确也会被副作用断言拦下。"""
    entry = next(entry for entry in suite.cases if entry.case.case_id == "catalog-permission-001")
    environment = build_evaluation_environment("catalog")
    environment.handler_calls.append("get_item")
    monkeypatch.setattr(agent_evaluation, "build_evaluation_environment", lambda *_: environment)
    row = run_suite(replace(suite, cases=(entry,)))["results"][0]
    assert row["actual"]["result"]["error_type"] == "PERMISSION_DENIED"
    assert row["failed_checks"] == ["handler_calls"]


def test_parser_failure_is_not_counted_as_a_registry_call(offline_report):
    """伪造权限在解析时被拒绝；没有工具调用时成功率应为不适用而非100%。"""
    row = find_row(offline_report, "injection-forged-permission-001")
    assert row["actual"]["tool_call_attempted"] is False
    assert row["handler_calls"] == []
    summary = summarize_results([row])
    assert summary["task_pass_rate"] == 1.0
    assert summary["tool_call_attempts"] == 0
    assert summary["tool_call_success_rate"] is None
    assert summarize_results([])["latency"]["mean_ms"] is None


@pytest.mark.parametrize("actual,expected,matches", [
    ({}, {"content": None}, False),
    ({"content": None}, {"content": None}, True),
    ({"answerable": 1}, {"answerable": True}, False),
    ({"total": True}, {"total": 1}, False),
    ({"data": [{"id": 101, "name": "商品"}]}, {"data": [{"id": 101}]}, True),
    ({"data": [{"id": 101}, {"id": 102}]}, {"data": [{"id": 101}]}, False),
])
def test_result_assertions_distinguish_missing_null_boolean_and_list(actual, expected, matches):
    """验证嵌套结果断言不会混淆缺字段、null、布尔值及多余的数据条目。"""
    assert _matches_expected_data(actual, expected) is matches


def test_latency_summary_uses_mean_and_nearest_rank_p95(offline_report):
    """用固定耗时验证平均值和P95公式，不依赖真实电脑的执行速度。"""
    rows = [deepcopy(offline_report["results"][0]) for _ in range(5)]
    for row, latency in zip(rows, [10, 20, 30, 40, 50]):
        row["actual"]["end_to_end_ms"] = latency
    latency = summarize_results(rows)["latency"]
    assert latency == {"unit": "ms", "measured_cases": 5, "mean_ms": 30, "p95_ms": 50}


def test_end_to_end_timer_includes_planning(monkeypatch):
    """注入时钟模拟规划2秒、执行0.1秒，证明端到端耗时不等于工具耗时。"""
    clock = {"now": 0.0}

    class Planner:
        def plan(self, _text, permissions, trace_id):
            clock["now"] = 2.0
            return ToolCall("get_item", {"item_id": 101}, trace_id, permissions)

    class Registry:
        def execute(self, call):
            clock["now"] = 2.1
            return ToolExecutionResult(
                ok=True, data={"id": 101}, error_type=None, message=None,
                trace=TraceEvent(call.trace_id, call.tool_name, "success", 100.0),
            )

    monkeypatch.setattr("app.agent.mvp.time.perf_counter", lambda: clock["now"])
    case = EvaluationCase(
        "timer", "查询商品 ID 101", frozenset({"catalog:read"}),
        expected_tool="get_item",
    )
    evaluation = evaluate_case(case, ToolCallingAgent(planner=Planner(), registry=Registry()))
    assert evaluation.passed is True
    assert evaluation.end_to_end_ms == 2100.0
    assert evaluation.result.trace.duration_ms == 100.0


@pytest.mark.parametrize("mutation", [
    "duplicate_id", "bad_ok", "missing_field", "unknown_field",
    "unknown_fixture", "bad_permissions", "empty_cases", "bad_schema_version",
])
def test_loader_rejects_invalid_versioned_cases(tmp_path, mutation):
    """损坏用例配置必须在执行前报错，不能跳过坏case后报告全绿。"""
    payload = json.loads(DEFAULT_CASES_PATH.read_text(encoding="utf-8"))
    first = payload["cases"][0]
    if mutation == "duplicate_id":
        payload["cases"][1]["case_id"] = first["case_id"]
    elif mutation == "bad_ok":
        first["expected"]["ok"] = 1
    elif mutation == "missing_field":
        del first["expected"]["error_type"]
    elif mutation == "unknown_field":
        first["expected"]["ok_typo"] = True
    elif mutation == "unknown_fixture":
        first["fixture"] = "does-not-exist"
    elif mutation == "bad_permissions":
        first["permissions"] = "catalog:read"
    elif mutation == "empty_cases":
        payload["cases"] = []
    else:
        payload["schema_version"] = True
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_suite(path)


def test_real_planner_requires_explicit_opt_in(suite, monkeypatch):
    """未开启集成开关时，拒绝真实Planner模式且不得构造任何执行环境。"""
    monkeypatch.delenv("RUN_OLLAMA_INTEGRATION", raising=False)

    def forbidden_factory(*_):
        raise AssertionError("不应该进入环境构建")

    monkeypatch.setattr(agent_evaluation, "build_evaluation_environment", forbidden_factory)
    with pytest.raises(ValueError, match="RUN_OLLAMA_INTEGRATION"):
        run_suite(suite, planner_mode="ollama")


def test_planner_groups_remain_separate_in_opt_in_mode(suite, monkeypatch):
    """模拟真实模式的分组但仍使用离线代理，确保3条注入case不算模型成功率。"""
    monkeypatch.setenv("RUN_OLLAMA_INTEGRATION", "1")

    def offline_factory(profile, mode, prompt_version):
        assert mode == "ollama"
        environment = build_evaluation_environment(profile, planner_mode="offline")
        if environment.planner_kind == "offline_rules":
            environment.planner_kind = "ollama"
        return environment

    monkeypatch.setattr(agent_evaluation, "build_evaluation_environment", offline_factory)
    report = run_suite(suite, planner_mode="ollama")
    assert report["by_planner"]["ollama"]["total"] == 12
    assert report["by_planner"]["injected_output"]["total"] == 3


def test_harness_exception_is_visible_and_does_not_stop_other_cases(suite, monkeypatch):
    """评测器异常必须显示为失败；后续case继续运行，未知调用数不能凭空算成功。"""
    original_factory = build_evaluation_environment

    def broken_factory(profile, mode, prompt_version):
        if profile == "catalog":
            raise RuntimeError("评测依赖配置损坏")
        return original_factory(profile, mode, prompt_version)

    monkeypatch.setattr(agent_evaluation, "build_evaluation_environment", broken_factory)
    report = run_suite(replace(suite, cases=(suite.cases[0], suite.cases[3])))
    assert report["results"][0]["actual"]["result"]["error_type"] == "EVALUATION_ERROR"
    assert report["results"][0]["actual"]["passed"] is False
    assert report["results"][1]["actual"]["passed"] is True
    assert report["summary"]["unknown_call_outcomes"] == 1
    assert report["summary"]["tool_call_attempts"] == 1


def test_reports_preserve_details_and_pending_reviews(tmp_path, offline_report):
    """检查JSON可回读、Markdown有5条未勾选复核项，且不能覆盖同一轮证据。"""
    json_path, markdown_path = write_reports(offline_report, tmp_path)
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved == offline_report
    assert saved["manual_review_pending"] == 5
    markdown = markdown_path.read_text(encoding="utf-8")
    assert markdown.count("- [ ]") == 5
    assert "50.00%" in markdown
    assert "尚未完成" in markdown
    assert "injected_output" in markdown
    assert "预期工具 → 实际工具" in markdown
    with pytest.raises(FileExistsError):
        write_reports(offline_report, tmp_path)


def test_separate_runs_get_unique_trace_ids(suite, offline_report):
    """相同case在第二轮评测也应有新trace，避免跨批次查日志时混淆。"""
    another = run_suite(replace(suite, cases=(suite.cases[0],)))
    assert another["run_id"] != offline_report["run_id"]
    assert another["results"][0]["trace_id"] != offline_report["results"][0]["trace_id"]
    assert len({row["trace_id"] for row in offline_report["results"]}) == 15


@pytest.mark.parametrize("wrong_expectation,expected_exit", [(False, 0), (True, 1)])
def test_cli_exit_code_reflects_task_checks(tmp_path, capsys, wrong_expectation, expected_exit):
    """调用CLI入口验证通过退出0、断言失败退出1，并生成可定位失败的报告。"""
    payload = json.loads(DEFAULT_CASES_PATH.read_text(encoding="utf-8"))
    payload["cases"] = payload["cases"][:1]
    if wrong_expectation:
        payload["cases"][0]["expected"]["data"] = {"data": {"id": 102}}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert main([
        "--cases", str(path), "--output-dir", str(tmp_path / "reports"),
    ]) == expected_exit
    output = capsys.readouterr().out
    assert "任务通过率" in output
    assert "Markdown" in output
    if wrong_expectation:
        assert "FAIL | catalog-get-001 | get_item | data" in output


def test_cli_configuration_error_returns_two(tmp_path, capsys):
    """找不到用例文件是配置错误，不能误报为测试通过或普通业务拒绝。"""
    assert main(["--cases", str(tmp_path / "missing.json")]) == 2
    assert "评测未完成" in capsys.readouterr().err


def test_full_suite_regression_blocks_gate_and_keeps_report(tmp_path, capsys):
    """只改临时副本的一条预期，确认14/15任务通过会返回1并留下失败证据。"""
    payload = json.loads(DEFAULT_CASES_PATH.read_text(encoding="utf-8"))
    # 固定工具实际返回101；这里故意期望102，不修改正式用例或业务代码。
    payload["cases"][0]["expected"]["data"] = {"data": {"id": 102}}
    cases_path = tmp_path / "regression-cases.json"
    cases_path.write_text(json.dumps(payload), encoding="utf-8")
    output_dir = tmp_path / "reports"
    exit_code = main([
        "--planner", "offline", "--cases", str(cases_path),
        "--output-dir", str(output_dir),
    ])
    assert exit_code == 1
    assert "14/15" in capsys.readouterr().out
    paths = list(output_dir.glob("*/results.json"))
    assert len(paths) == 1
    report = json.loads(paths[0].read_text(encoding="utf-8"))
    assert report["summary"]["failed"] == 1
    assert report["summary"]["task_pass_rate"] < 1.0
    assert "data" in report["results"][0]["failed_checks"]


def test_offline_report_does_not_claim_prompt_evaluation(offline_report, suite):
    """离线规则没有使用提示词，报告必须标明不适用，且不允许假装运行v1。"""
    assert offline_report["prompt"] is None
    assert all(row["prompt_version"] is None for row in offline_report["results"])
    with pytest.raises(ValueError, match="离线规则"):
        run_suite(suite, prompt_version="v1")


@pytest.mark.parametrize("version", ["v0", "v1"])
def test_prompt_version_reaches_planner_and_report(suite, monkeypatch, tmp_path, version):
    """用假HTTP响应串起版本传递与报告落盘，注入用例仍不标记提示词版本。"""
    import hashlib
    from app.agent.ollama import OllamaToolPlanner, get_planner_prompt

    monkeypatch.setenv("RUN_OLLAMA_INTEGRATION", "1")
    captured = []
    original_factory = build_evaluation_environment

    def transport(request, _timeout):
        captured.append(json.loads(request.data)["messages"][0]["content"])
        return json.dumps({"message": {"tool_calls": [{"function": {
            "name": "get_item", "arguments": {"item_id": 101},
        }}]}}).encode("utf-8")

    def fake_factory(profile, mode, prompt_version):
        environment = original_factory(profile, mode, prompt_version)
        if environment.planner_kind == "ollama":
            environment.agent.planner = replace(environment.agent.planner, transport=transport)
            assert isinstance(environment.agent.planner, OllamaToolPlanner)
        return environment

    monkeypatch.setattr(agent_evaluation, "build_evaluation_environment", fake_factory)
    selected = tuple(entry for entry in suite.cases if entry.case.case_id in {
        "catalog-get-001", "injection-unknown-tool-001",
    })
    report = run_suite(replace(suite, cases=selected), "ollama", version)
    text = get_planner_prompt(version)
    assert captured == [text]
    assert report["summary"]["passed"] == 2
    assert report["prompt"] == {
        "version": version, "text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    assert [row["prompt_version"] for row in report["results"]] == [version, None]
    json_path, markdown_path = write_reports(report, tmp_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["prompt"] == report["prompt"]
    assert f"提示词版本：{version}" in markdown_path.read_text(encoding="utf-8")
