#!/usr/bin/env python3
"""Step 2: use Step 1 descriptions plus one source task as shot and generate task bundles."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claw_eval.models.task import TaskDefinition

from create_query.common import (  # noqa: E402
    ALLOWED_FIXTURE_SUFFIXES,
    BASE_URL,
    API_KEY,
    MODEL,
    DEFAULT_MAX_SHOT_FILE_CHARS,
    QueryPipelineError,
    SERVICE_FIXTURE_ENV_KEYS,
    call_chat,
    dump_json,
    extract_json_object,
    file_sections,
    fixture_files,
    now_str,
    normalise_files_payload,
    read_text,
    repo_root_default,
    resolve_output_dir,
    strip_file_fence,
    task_definition,
    task_summary,
    validate_rel_path,
)


SUPPORTED_SCORING_CHECK_KEYS = {
    "type",
    "field",
    "tool_name",
    "min_calls",
    "categories",
    "min_length",
    "patterns",
    "keywords",
    "description",
    "rubric",
}
SUPPORTED_SAFETY_CHECK_KEYS = {"type", "tool_name", "patterns", "description"}


def load_yaml_mapping(text: str, *, label: str) -> dict[str, Any]:
    try:
        obj = yaml.safe_load(text)
    except Exception as exc:
        raise QueryPipelineError(f"{label} is not valid YAML: {exc}") from exc
    if not isinstance(obj, dict):
        raise QueryPipelineError(f"{label} must be a YAML object at top level.")
    return obj


def require_same_keys(
    label: str,
    actual: dict[str, Any],
    expected: dict[str, Any],
    *,
    allowed_extra: set[str] | None = None,
) -> None:
    actual_keys = set(actual.keys())
    expected_keys = set(expected.keys())
    extra_keys = actual_keys - expected_keys
    if allowed_extra:
        extra_keys -= allowed_extra
    if not (expected_keys - actual_keys) and not extra_keys:
        return
    missing = sorted(expected_keys - actual_keys)
    extra = sorted(extra_keys)
    parts: list[str] = []
    if missing:
        parts.append(f"missing={missing}")
    if extra:
        parts.append(f"extra={extra}")
    raise QueryPipelineError(f"{label} keys must match source template: {'; '.join(parts)}")


def reference_item(items: list[Any], *, match_key: str | None, match_value: Any) -> dict[str, Any] | None:
    dict_items = [item for item in items if isinstance(item, dict)]
    if not dict_items:
        return None
    if match_key is not None:
        for item in dict_items:
            if item.get(match_key) == match_value:
                return item
    return dict_items[0]


def validate_mapping_list_shape(
    label: str,
    actual_items: Any,
    expected_items: Any,
    *,
    match_key: str | None = None,
    allowed_extra: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(actual_items, list):
        raise QueryPipelineError(f"{label} must be a list.")
    expected_list = expected_items if isinstance(expected_items, list) else []
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(actual_items):
        if not isinstance(item, dict):
            raise QueryPipelineError(f"{label}[{idx}] must be an object.")
        ref = reference_item(expected_list, match_key=match_key, match_value=item.get(match_key))
        if ref is not None:
            require_same_keys(f"{label}[{idx}]", item, ref, allowed_extra=allowed_extra)
        out.append(item)
    return out


def validate_scoring_checks(scoring_components: list[dict[str, Any]]) -> None:
    for idx, component in enumerate(scoring_components):
        check = component.get("check")
        if not isinstance(check, dict):
            raise QueryPipelineError(f"task.yaml.scoring_components[{idx}].check must be an object.")
        unsupported = sorted(set(check.keys()) - SUPPORTED_SCORING_CHECK_KEYS)
        if unsupported:
            raise QueryPipelineError(
                "task.yaml.scoring_components"
                f"[{idx}].check has unsupported keys: {unsupported}. "
                "Use the same check fields as the source task."
            )


def validate_safety_checks(safety_checks: Any) -> None:
    if not isinstance(safety_checks, list):
        raise QueryPipelineError("task.yaml.safety_checks must be a list.")
    for idx, check in enumerate(safety_checks):
        if not isinstance(check, dict):
            raise QueryPipelineError(f"task.yaml.safety_checks[{idx}] must be an object.")
        unsupported = sorted(set(check.keys()) - SUPPORTED_SAFETY_CHECK_KEYS)
        if unsupported:
            raise QueryPipelineError(
                f"task.yaml.safety_checks[{idx}] has unsupported keys: {unsupported}."
            )


def validate_task_yaml_shape(task_yaml_obj: dict[str, Any], shot_task_yaml_obj: dict[str, Any]) -> None:
    require_same_keys("task.yaml", task_yaml_obj, shot_task_yaml_obj)

    prompt = task_yaml_obj.get("prompt")
    shot_prompt = shot_task_yaml_obj.get("prompt")
    if not isinstance(prompt, dict) or not isinstance(shot_prompt, dict):
        raise QueryPipelineError("task.yaml.prompt must stay a mapping like the source task.")
    require_same_keys("task.yaml.prompt", prompt, shot_prompt)

    environment = task_yaml_obj.get("environment")
    shot_environment = shot_task_yaml_obj.get("environment")
    if not isinstance(environment, dict) or not isinstance(shot_environment, dict):
        raise QueryPipelineError("task.yaml.environment must stay a mapping like the source task.")
    require_same_keys("task.yaml.environment", environment, shot_environment)

    services = validate_mapping_list_shape(
        "task.yaml.services",
        task_yaml_obj.get("services"),
        shot_task_yaml_obj.get("services"),
        match_key="name",
        allowed_extra={"env"},
    )
    for idx, service in enumerate(services):
        service_name = service.get("name")
        env = service.get("env")
        if env is not None and not isinstance(env, dict):
            raise QueryPipelineError(f"task.yaml.services[{idx}].env must be a mapping.")
        required_env_key = SERVICE_FIXTURE_ENV_KEYS.get(service_name) if isinstance(service_name, str) else None
        if required_env_key:
            if not isinstance(env, dict):
                raise QueryPipelineError(
                    f"task.yaml.services[{idx}] must include env.{required_env_key} for isolated fixtures."
                )
            if not str(env.get(required_env_key, "")).strip():
                raise QueryPipelineError(
                    f"task.yaml.services[{idx}].env missing required key: {required_env_key}"
                )

    tools = validate_mapping_list_shape(
        "task.yaml.tools",
        task_yaml_obj.get("tools"),
        shot_task_yaml_obj.get("tools"),
        match_key="name",
    )
    for idx, tool in enumerate(tools):
        input_schema = tool.get("input_schema")
        if not isinstance(input_schema, dict):
            raise QueryPipelineError(f"task.yaml.tools[{idx}].input_schema must be a mapping.")
        ref_tool = reference_item(shot_task_yaml_obj.get("tools", []), match_key="name", match_value=tool.get("name"))
        if ref_tool is not None and isinstance(ref_tool.get("input_schema"), dict):
            require_same_keys(
                f"task.yaml.tools[{idx}].input_schema",
                input_schema,
                ref_tool["input_schema"],
            )

    validate_mapping_list_shape(
        "task.yaml.tool_endpoints",
        task_yaml_obj.get("tool_endpoints"),
        shot_task_yaml_obj.get("tool_endpoints"),
        match_key="tool_name",
    )
    scoring_components = validate_mapping_list_shape(
        "task.yaml.scoring_components",
        task_yaml_obj.get("scoring_components"),
        shot_task_yaml_obj.get("scoring_components"),
    )
    validate_scoring_checks(scoring_components)
    validate_safety_checks(task_yaml_obj.get("safety_checks"))


def task_yaml_shape_summary(shot_task_yaml_obj: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "top_level_keys": list(shot_task_yaml_obj.keys()),
        "prompt_keys": list((shot_task_yaml_obj.get("prompt") or {}).keys()),
        "environment_keys": list((shot_task_yaml_obj.get("environment") or {}).keys()),
        "service_item_keys": [],
        "tool_item_keys": [],
        "tool_input_schema_keys": [],
        "tool_endpoint_item_keys": [],
        "scoring_component_item_keys": [],
        "allowed_scoring_check_keys": sorted(SUPPORTED_SCORING_CHECK_KEYS),
        "allowed_safety_check_keys": sorted(SUPPORTED_SAFETY_CHECK_KEYS),
    }
    if shot_task_yaml_obj.get("services"):
        service_item_keys = list(shot_task_yaml_obj["services"][0].keys())
        required_service_env: dict[str, str] = {}
        for service in shot_task_yaml_obj["services"]:
            if not isinstance(service, dict):
                continue
            service_name = service.get("name")
            if not isinstance(service_name, str):
                continue
            env_key = SERVICE_FIXTURE_ENV_KEYS.get(service_name)
            if env_key:
                required_service_env[service_name] = env_key
        if required_service_env and "env" not in service_item_keys:
            service_item_keys.append("env")
        summary["service_item_keys"] = service_item_keys
        summary["required_service_env"] = required_service_env
    if shot_task_yaml_obj.get("tools"):
        first_tool = shot_task_yaml_obj["tools"][0]
        if isinstance(first_tool, dict):
            summary["tool_item_keys"] = list(first_tool.keys())
            if isinstance(first_tool.get("input_schema"), dict):
                summary["tool_input_schema_keys"] = list(first_tool["input_schema"].keys())
    if shot_task_yaml_obj.get("tool_endpoints"):
        summary["tool_endpoint_item_keys"] = list(shot_task_yaml_obj["tool_endpoints"][0].keys())
    if shot_task_yaml_obj.get("scoring_components"):
        first_component = shot_task_yaml_obj["scoring_components"][0]
        if isinstance(first_component, dict):
            summary["scoring_component_item_keys"] = list(first_component.keys())
    return summary


def render_prompt(
    *,
    source_task_id: str,
    new_desc: dict[str, Any],
    shot_files: dict[str, str],
    output_task_dir: str,
    shot_summary: dict[str, Any],
    shot_task_yaml_obj: dict[str, Any],
) -> str:
    new_task_id = new_desc["new_task_id"]
    desc_text = json.dumps(new_desc, ensure_ascii=False, indent=2)
    summary_text = json.dumps(shot_summary, ensure_ascii=False, indent=2)
    task_yaml_shape_text = json.dumps(task_yaml_shape_summary(shot_task_yaml_obj), ensure_ascii=False, indent=2)
    service_env_rules: list[str] = []
    service_env_examples: list[str] = []
    for service_name in shot_summary.get("services", []):
        env_key = SERVICE_FIXTURE_ENV_KEYS.get(service_name)
        if env_key:
            service_env_rules.append(
                f"- 服务 {service_name} 必须在 services[].env 中声明 {env_key}，"
                f"并指向 {output_task_dir}/fixtures/... 下的新 fixture 文件"
            )
            service_env_examples.append(
                f"- {service_name} 的 services[] 项必须包含：env: {{{env_key}: "
                f"\"{output_task_dir}/fixtures/{service_name}/...\"}}"
            )
    service_env_text = "\n".join(service_env_rules) if service_env_rules else "- 当前 shot 无需额外 service fixture env 约束"
    service_env_example_text = (
        "\n".join(service_env_examples) if service_env_examples else "- 当前 shot 无需额外 env 示例"
    )
    return f"""
你是 Claw-Eval task 目录生成器。你会收到：
1. 一个原始 benchmark task 的 task.yaml / fixtures / grader.py 作为 shot。
2. 一个由 Step1 生成的新任务描述。

你的任务：生成一个完整的新 task 目录内容。目录会由脚本写入：{output_task_dir}

非常重要：你只能输出一个 JSON 对象，不要 markdown，不要解释文字，不要在 JSON 外输出任何东西。

输出 JSON 必须严格是：
{{
  "task_id": "{new_task_id}",
  "files": {{
    "task.yaml": "完整 YAML 字符串",
    "grader.py": "完整 Python grader 字符串",
    "fixtures/...json": [{{}}],
    "fixtures/...txt": "完整文本字符串"
  }},
  "notes": {{
    "source_task_id": "{source_task_id}",
    "difference_summary": "一句话说明新题和原题的核心区别",
    "expected_behavior_summary": ["..."],
    "expected_fixture_labels_or_answers": {{}}
  }}
}}

必须遵守的文件规则：
1. files 里的路径必须相对 task 根目录，例如 task.yaml、grader.py、fixtures/gmail/inbox.json。
2. 不要输出绝对路径，不要输出包含 .. 的路径。
3. 必须生成 task.yaml、grader.py、至少一个 fixtures/ 下的 .json 或 .txt 文件。
4. fixture 内容必须和新任务描述一致，不能复用 shot 中的具体实体名、ID、正文、数值。
5. 如果 fixture 是 `.json`，不要把整个 JSON 文件再包成字符串；请直接输出 JSON 对象或 JSON 数组，脚本会自动落盘。只有 `.txt` fixture 才输出字符串。
6. 如果 JSON 里的某个字段值本身有换行，仍然必须用合法 JSON 形式表示该字符串。
7. task.yaml 不要从零自由发挥。请直接把 shot 的 task.yaml 当模板，保持字段名对齐，只修改值。
8. task.yaml 顶层 key、prompt key、environment key、tools/tool_endpoints/scoring_components 的常见子结构 key 都必须和 shot 对齐；`services[].env` 允许额外补上，但只用于指向新 fixture。
9. grader.py 可以按新任务重写，不要求和 shot 内容相同；但必须和新 fixture、task.yaml、prompt 互相一致。
10. grader.py 应尽量模仿 shot grader 的风格和 import 方式，使用 Claw-Eval 的 AbstractGrader / DimensionScores / ToolDispatch / TraceMessage 等接口。
11. 不要引入 bench 里不存在的新服务或工具，除非新任务描述明确要求且 shot 已经显示类似 schema。
12. task.yaml 中服务 env 的 fixture 路径应指向隔离输出目录：{output_task_dir}/fixtures/...，但 sandbox_files 仍使用相对路径 fixtures/...
13. 如果源任务是中文 task，新任务 prompt.text、grader 里用于 judge 的分类或说明也优先使用中文。
14. 新任务要能作为独立 task 运行，不依赖 source task 的 fixture。
15. 如果 shot 里没有 services/tools，也不要硬造；保持同类任务的结构形态。
16. task.yaml 必须通过当前仓库的 TaskDefinition schema。scoring_components[].check 只能使用模板/仓库里已有的字段，不要发明 `max_calls`、`check_params`、`prompt` 之类额外 key。
17. grader.py 必须是合法 Python 文件，第一行只能是 docstring、注释或 import，不能出现解释性的裸文本。
18. grader.py 必须可以被本仓库的 grader loader 直接 import 并实例化。
19. 即使 shot 的 services[] 里原本没有 env 字段，只要上面的 env 规则要求该服务绑定 fixture，你也必须补上 services[].env。

如果用了 mock service，请额外遵守下面的 env 规则：
{service_env_text}

services[].env 的写法示例如下：
{service_env_example_text}

task.yaml 的结构模板摘要如下，生成时请严格对齐字段名：
{task_yaml_shape_text}

Step1 生成的新任务描述如下：
{desc_text}

原始 shot 任务元信息如下：
{summary_text}

下面是原始 benchmark shot 文件：
{file_sections(shot_files)}

最后再次提醒：只输出 JSON 对象。
""".strip() + "\n"


def load_step1_json(step1_path: Path) -> dict[str, Any]:
    if not step1_path.exists():
        raise QueryPipelineError(f"Step1 JSON not found: {step1_path}")
    obj = json.loads(step1_path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise QueryPipelineError("Step1 JSON must be an object.")
    tasks = obj.get("new_task_descriptions")
    if not isinstance(tasks, list) or not tasks:
        raise QueryPipelineError("Step1 JSON must contain non-empty new_task_descriptions.")
    return obj


def collect_shot_files(task_dir: Path, max_file_chars: int) -> dict[str, str]:
    if not task_dir.exists():
        raise QueryPipelineError(f"Source task dir not found: {task_dir}")
    if not (task_dir / "task.yaml").exists():
        raise QueryPipelineError(f"Missing task.yaml under {task_dir}")

    files: dict[str, str] = {"task.yaml": read_text(task_dir / "task.yaml", max_file_chars)}
    for p in fixture_files(task_dir):
        if p.suffix.lower() in ALLOWED_FIXTURE_SUFFIXES:
            files[str(p.relative_to(task_dir))] = read_text(p, max_file_chars)
    grader = task_dir / "grader.py"
    if grader.exists():
        files["grader.py"] = read_text(grader, max_file_chars)
    return files


def validate_generated_bundle(
    obj: dict[str, Any],
    expected_task_id: str,
    *,
    shot_task_yaml_obj: dict[str, Any],
) -> dict[str, str]:
    if obj.get("task_id") != expected_task_id:
        raise QueryPipelineError(f"task_id mismatch: expected {expected_task_id}, got {obj.get('task_id')}")

    files = normalise_files_payload(obj)
    for rel in files:
        validate_rel_path(rel)

    required = ["task.yaml", "grader.py"]
    missing = [p for p in required if p not in files]
    if missing:
        raise QueryPipelineError(f"Missing required files: {missing}")

    fixture_paths = [p for p in files if p.startswith("fixtures/")]
    if not fixture_paths:
        raise QueryPipelineError("At least one fixtures/... file is required.")

    task_yaml_obj = load_yaml_mapping(files["task.yaml"], label="task.yaml")
    validate_task_yaml_shape(task_yaml_obj, shot_task_yaml_obj)
    if task_yaml_obj.get("task_id") != expected_task_id:
        raise QueryPipelineError(
            f"task.yaml task_id mismatch: expected {expected_task_id}, got {task_yaml_obj.get('task_id')}"
        )
    for key in ["judge_rubric", "scoring_components", "safety_checks", "reference_solution", "primary_dimensions"]:
        if key not in task_yaml_obj:
            raise QueryPipelineError(f"task.yaml missing {key}.")

    try:
        TaskDefinition.model_validate(task_yaml_obj)
    except Exception as exc:
        raise QueryPipelineError(f"Generated task.yaml schema validation failed: {exc}") from exc

    grader = files["grader.py"]
    try:
        compile(grader, "grader.py", "exec")
    except SyntaxError as exc:
        raise QueryPipelineError(f"grader.py is not valid Python: {exc}") from exc
    if "load_peer_grader" not in grader and "AbstractGrader" not in grader:
        raise QueryPipelineError("grader.py should use AbstractGrader or load_peer_grader like Claw-Eval graders.")
    if "AbstractGrader" in grader and "DimensionScores" not in grader:
        raise QueryPipelineError("grader.py should use DimensionScores when defining a standalone grader.")

    for rel in fixture_paths:
        if rel.endswith(".json"):
            try:
                json.loads(files[rel])
            except json.JSONDecodeError as exc:
                raise QueryPipelineError(f"Fixture {rel} is not valid JSON: {exc}") from exc
        elif rel.endswith(".txt"):
            pass
        else:
            raise QueryPipelineError(f"Unsupported fixture suffix in generated output: {rel}")
    return files


def write_task_dir(out_base: Path, task_id: str, files: dict[str, str], overwrite: bool) -> Path:
    task_dir = out_base / "generated" / task_id
    stage_dir = out_base / "_staging" / task_id
    if task_dir.exists() and not overwrite:
        raise QueryPipelineError(f"Output dir already exists: {task_dir}. Use --overwrite.")
    if task_dir.exists():
        shutil.rmtree(task_dir)
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        validate_rel_path(rel)
        path = stage_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(strip_file_fence(content), encoding="utf-8")
    return stage_dir


def finalize_task_dir(out_base: Path, task_id: str, stage_dir: Path) -> Path:
    task_dir = out_base / "generated" / task_id
    task_dir.parent.mkdir(parents=True, exist_ok=True)
    stage_dir.replace(task_dir)
    return task_dir


def validate_materialized_task_dir(task_dir: Path) -> None:
    try:
        task = task_definition(task_dir)
    except Exception as exc:
        raise QueryPipelineError(f"Materialized task.yaml schema invalid: {exc}") from exc
    if task.task_id != task_dir.name:
        raise QueryPipelineError(f"Materialized task_id mismatch: {task.task_id} vs {task_dir.name}")

    if not str(task.judge_rubric).strip():
        raise QueryPipelineError("task.yaml judge_rubric is empty.")
    if not str(task.reference_solution).strip():
        raise QueryPipelineError("task.yaml reference_solution is empty.")
    if not task.primary_dimensions:
        raise QueryPipelineError("task.yaml primary_dimensions is empty.")
    if not task.scoring_components:
        raise QueryPipelineError("task.yaml scoring_components is empty.")
    if not task.sandbox_files:
        raise QueryPipelineError("task.yaml sandbox_files is empty.")
    score_total = sum(component.weight for component in task.scoring_components)
    if abs(score_total - 1.0) > 0.01:
        raise QueryPipelineError(f"scoring_components weights sum to {score_total:.3f}, expected 1.0")

    if task.tools or task.tool_endpoints:
        tool_names = {tool.name for tool in task.tools}
        endpoint_names = {endpoint.tool_name for endpoint in task.tool_endpoints}
        if tool_names != endpoint_names:
            raise QueryPipelineError(
                f"tools/tool_endpoints mismatch: tools={sorted(tool_names)}, endpoints={sorted(endpoint_names)}"
            )

    for rel in task.sandbox_files:
        validate_rel_path(rel)
        if not (task_dir / rel).exists():
            raise QueryPipelineError(f"sandbox_files entry missing on disk: {rel}")

    for rel in task.prompt.attachments:
        validate_rel_path(rel)
        if not (task_dir / rel).exists():
            raise QueryPipelineError(f"prompt.attachments entry missing on disk: {rel}")

    repo_root = repo_root_default()
    for svc in task.services:
        required_env_key = SERVICE_FIXTURE_ENV_KEYS.get(svc.name)
        if required_env_key and not str(svc.env.get(required_env_key, "")).strip():
            raise QueryPipelineError(f"service {svc.name} missing required env key: {required_env_key}")
        for _env_name, value in svc.env.items():
            if not isinstance(value, str):
                continue
            if "fixtures/" in value or value.endswith(".json") or value.endswith(".txt"):
                candidate = Path(value)
                if candidate.is_absolute():
                    resolved = candidate.resolve()
                else:
                    repo_candidate = (repo_root / value).resolve()
                    task_candidate = (task_dir / value).resolve()
                    if repo_candidate.exists():
                        resolved = repo_candidate
                    else:
                        resolved = task_candidate
                if not resolved.exists():
                    raise QueryPipelineError(f"service env fixture path does not exist: {value}")
                if task_dir.resolve() not in resolved.parents:
                    raise QueryPipelineError(
                        f"service env fixture path must stay inside generated task dir, got: {value}"
                    )

    try:
        from claw_eval.graders.registry import get_grader

        _grader = get_grader(task.task_id, tasks_dir=repo_root / "tasks", task_dir=task_dir)
    except Exception as exc:
        raise QueryPipelineError(f"grader.py cannot be imported/instantiated: {exc}") from exc


def selected_descriptions(step1_obj: dict[str, Any], only_index: int | None) -> list[dict[str, Any]]:
    descs = step1_obj["new_task_descriptions"]
    if only_index is None:
        return descs
    selected = [d for d in descs if int(d.get("index", -1)) == only_index]
    if not selected:
        raise QueryPipelineError(f"No task description found for index {only_index}")
    return selected


def display_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def process_one_description(
    args: argparse.Namespace,
    *,
    source_task_id: str,
    desc: dict[str, Any],
    shot_files: dict[str, str],
    shot_summary: dict[str, Any],
    shot_task_yaml_obj: dict[str, Any],
    out_base: Path,
) -> Path:
    task_id = desc["new_task_id"]
    final_task_dir = out_base / "generated" / task_id
    output_task_dir_for_yaml = display_path(final_task_dir, repo_root_default())
    prompt = render_prompt(
        source_task_id=source_task_id,
        new_desc=desc,
        shot_files=shot_files,
        output_task_dir=output_task_dir_for_yaml,
        shot_summary=shot_summary,
        shot_task_yaml_obj=shot_task_yaml_obj,
    )

    prompt_dir = out_base / "_prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / f"{task_id}.prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    if args.dry_run:
        print(f"[OK] dry-run prompt saved: {prompt_path}")
        return prompt_path

    raw = call_chat(
        prompt,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        system_prompt=(
            "You generate Claw-Eval task directories. "
            "Output one JSON object only and reuse the source task.yaml field structure."
        ),
    )
    raw_dir = out_base / "_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{task_id}.raw.txt"
    raw_path.write_text(raw, encoding="utf-8")

    parsed = extract_json_object(raw)
    parsed_dir = out_base / "_parsed"
    dump_json(parsed_dir / f"{task_id}.json", parsed)

    files = validate_generated_bundle(parsed, task_id, shot_task_yaml_obj=shot_task_yaml_obj)
    stage_dir = write_task_dir(out_base, task_id, files, overwrite=args.overwrite)
    task_dir = finalize_task_dir(out_base, task_id, stage_dir)
    try:
        validate_materialized_task_dir(task_dir)
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise
    print(f"[OK] wrote task directory: {task_dir}")
    return task_dir


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Step2: generate complete task directories from Step1 descriptions.")
    p.add_argument("--task", help="Source task id, e.g. T001zh_email_triage. Reads step1_output/<task>.json.")
    p.add_argument("--step1-json", help="Explicit Step1 JSON path. Overrides --task for input path.")
    p.add_argument("--repo-root", default=str(repo_root_default()), help="Claw-Eval repo root. Default: parent of create_query.")
    p.add_argument("--output-dir", default="step2_output", help="Output directory relative to create_query/")
    p.add_argument("--only-index", type=int, choices=[1, 2, 3, 4, 5], help="Only generate one plus_i task.")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing output task dirs.")
    p.add_argument("--base-url", default=os.environ.get("CREATE_QUERY_BASE_URL", BASE_URL))
    p.add_argument("--api-key", default=os.environ.get("CREATE_QUERY_API_KEY", API_KEY))
    p.add_argument("--model", default=os.environ.get("CREATE_QUERY_MODEL", MODEL))
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--max-tokens", type=int, default=128000)
    p.add_argument("--max-file-chars", type=int, default=DEFAULT_MAX_SHOT_FILE_CHARS)
    p.add_argument("--dry-run", action="store_true", help="Only write prompts, do not call API.")
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        if not args.task and not args.step1_json:
            raise QueryPipelineError("Pass --task TASK_NAME or --step1-json PATH.")

        out_base = resolve_output_dir(args.output_dir)
        out_base.mkdir(parents=True, exist_ok=True)

        cwd = Path.cwd()
        if args.step1_json:
            step1_path = Path(args.step1_json)
            if not step1_path.is_absolute():
                step1_path = cwd / step1_path
        else:
            step1_path = Path(__file__).resolve().parent / "step1_output" / f"{args.task}.json"

        step1_obj = load_step1_json(step1_path)
        source_task_id = step1_obj.get("source_task_id") or args.task
        if not source_task_id:
            raise QueryPipelineError("Cannot determine source_task_id.")

        repo_root = Path(args.repo_root).resolve()
        source_task_dir = repo_root / "tasks" / source_task_id
        task = task_definition(source_task_dir)
        shot_files = collect_shot_files(source_task_dir, args.max_file_chars)
        shot_task_yaml_obj = load_yaml_mapping(shot_files["task.yaml"], label="source task.yaml")
        shot_summary = task_summary(task, [str(p.relative_to(source_task_dir)) for p in fixture_files(source_task_dir)], has_grader=(source_task_dir / "grader.py").exists())

        generated: list[dict[str, str]] = []
        for desc in selected_descriptions(step1_obj, args.only_index):
            output_path = process_one_description(
                args,
                source_task_id=source_task_id,
                desc=desc,
                shot_files=shot_files,
                shot_summary=shot_summary,
                shot_task_yaml_obj=shot_task_yaml_obj,
                out_base=out_base,
            )
            if args.dry_run:
                continue
            generated.append(
                {
                    "task_id": desc["new_task_id"],
                    "task_dir": display_path(output_path, Path(__file__).resolve().parent),
                    "prompt": display_path(out_base / "_prompts" / f"{desc['new_task_id']}.prompt.txt", Path(__file__).resolve().parent),
                    "raw": display_path(out_base / "_raw" / f"{desc['new_task_id']}.raw.txt", Path(__file__).resolve().parent),
                    "parsed": display_path(out_base / "_parsed" / f"{desc['new_task_id']}.json", Path(__file__).resolve().parent),
                }
            )

        if args.dry_run:
            return 0

        manifest = {
            "schema_version": "step2.generated_tasks.v1",
            "generated_at": now_str(),
            "source_task_id": source_task_id,
            "source_step1_json": display_path(step1_path, Path(__file__).resolve().parent),
            "items": generated,
        }
        dump_json(out_base / f"{source_task_id}.manifest.json", manifest)
        print(f"[OK] manifest saved: {out_base / f'{source_task_id}.manifest.json'}")
        return 0
    except QueryPipelineError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
