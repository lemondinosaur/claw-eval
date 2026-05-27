#!/usr/bin/env python3
"""Step 2 (new): generate concrete task dirs without grader/scoring artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claw_eval.models.task import TaskDefinition

from create_query.common import (  # noqa: E402
    ALLOWED_FIXTURE_SUFFIXES,
    API_KEY,
    BASE_URL,
    DEFAULT_MAX_SHOT_FILE_CHARS,
    MODEL,
    QueryPipelineError,
    SERVICE_FIXTURE_ENV_KEYS,
    call_chat,
    dump_json,
    extract_json_object,
    file_sections,
    find_latest_run_dir_for_task,
    fixture_files,
    infer_task_instruction_language,
    infer_run_dir_from_path,
    load_task_bundle,
    normalise_files_payload,
    now_str,
    read_text,
    repo_root_default,
    resolve_run_output_dir,
    save_task_bundle,
    supported_fixture_files,
    task_definition,
    task_summary,
    task_bundle_path,
    validate_rel_path,
)


FORBIDDEN_TASK_YAML_KEYS = {
    "expected_actions",
    "judge_rubric",
    "local_grader_files",
    "primary_dimensions",
    "reference_solution",
    "sandbox_grader_files",
    "safety_checks",
    "scoring_components",
}


def load_yaml_mapping(text: str, *, label: str) -> dict[str, Any]:
    try:
        obj = yaml.safe_load(text)
    except Exception as exc:
        raise QueryPipelineError(f"{label} is not valid YAML: {exc}") from exc
    if not isinstance(obj, dict):
        raise QueryPipelineError(f"{label} must be a YAML object at top level.")
    return obj


def dump_yaml_text(obj: dict[str, Any]) -> str:
    return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False).rstrip() + "\n"


def strip_scoring_fields(task_yaml_obj: dict[str, Any]) -> dict[str, Any]:
    cleaned = deepcopy(task_yaml_obj)
    for key in FORBIDDEN_TASK_YAML_KEYS:
        cleaned.pop(key, None)
    return cleaned


def display_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


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


def step2_item_map(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    step2 = bundle.setdefault("step2", {})
    items = step2.setdefault("items", [])
    if not isinstance(items, list):
        items = []
        step2["items"] = items
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict):
            task_id = str(item.get("task_id", "")).strip()
            if task_id:
                out[task_id] = item
    return out


def ensure_step2_item(bundle: dict[str, Any], task_id: str, index: int | None = None) -> dict[str, Any]:
    items = bundle.setdefault("step2", {}).setdefault("items", [])
    item_map = step2_item_map(bundle)
    if task_id in item_map:
        item = item_map[task_id]
    else:
        item = {
            "task_id": task_id,
            "index": index,
            "status": "not_started",
            "generated_at": "",
            "prompt": "",
            "raw": "",
            "parsed": "",
            "task_dir": "",
            "error": "",
        }
        items.append(item)
    if index is not None:
        item["index"] = index
    item.setdefault("status", "not_started")
    item.setdefault("generated_at", "")
    item.setdefault("prompt", "")
    item.setdefault("raw", "")
    item.setdefault("parsed", "")
    item.setdefault("task_dir", "")
    item.setdefault("error", "")
    return item


def collect_shot_files(task_dir: Path, max_file_chars: int) -> dict[str, str]:
    if not task_dir.exists():
        raise QueryPipelineError(f"Source task dir not found: {task_dir}")
    if not (task_dir / "task.yaml").exists():
        raise QueryPipelineError(f"Missing task.yaml under {task_dir}")

    source_task_yaml_obj = load_yaml_mapping(
        read_text(task_dir / "task.yaml", max_file_chars),
        label="source task.yaml",
    )
    runtime_task_yaml = dump_yaml_text(strip_scoring_fields(source_task_yaml_obj))
    files: dict[str, str] = {"task.yaml": runtime_task_yaml}
    for p in supported_fixture_files(task_dir):
        files[str(p.relative_to(task_dir))] = read_text(p, max_file_chars)
    return files


def required_fixture_paths(task_dir: Path) -> list[str]:
    return [str(p.relative_to(task_dir)) for p in supported_fixture_files(task_dir)]


def selected_descriptions(step1_obj: dict[str, Any], only_index: int | None) -> list[dict[str, Any]]:
    descs = step1_obj["new_task_descriptions"]
    if only_index is None:
        return descs
    selected = [d for d in descs if int(d.get("index", -1)) == only_index]
    if not selected:
        raise QueryPipelineError(f"No task description found for index {only_index}")
    return selected


def extract_fixture_rel_from_env_value(value: Any, fixture_paths: list[str]) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.replace("\\", "/")
    for rel in fixture_paths:
        if normalized.endswith(rel):
            return rel
    marker = "fixtures/"
    idx = normalized.find(marker)
    if idx >= 0:
        candidate = normalized[idx:]
        if candidate in fixture_paths:
            return candidate
    return None


def infer_service_fixture_rel(service: dict[str, Any], fixture_paths: list[str]) -> str | None:
    service_name = service.get("name")
    if not isinstance(service_name, str):
        return None

    env_key = SERVICE_FIXTURE_ENV_KEYS.get(service_name)
    env = service.get("env")
    if env_key and isinstance(env, dict):
        rel = extract_fixture_rel_from_env_value(env.get(env_key), fixture_paths)
        if rel:
            return rel

    matches = [rel for rel in fixture_paths if rel.startswith(f"fixtures/{service_name}/")]
    if len(matches) == 1:
        return matches[0]
    return None


def rewrite_services(
    services: list[dict[str, Any]],
    *,
    fixture_paths: list[str],
    output_task_dir: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for service in services:
        if not isinstance(service, dict):
            raise QueryPipelineError("source task.yaml services must be objects.")
        item = deepcopy(service)
        env = deepcopy(item.get("env") or {})
        if not isinstance(env, dict):
            raise QueryPipelineError(f"service {item.get('name')} env must be a mapping.")

        rewritten_any = False
        for env_name, value in list(env.items()):
            rel = extract_fixture_rel_from_env_value(value, fixture_paths)
            if rel:
                env[env_name] = f"{output_task_dir}/{rel}"
                rewritten_any = True

        service_name = item.get("name")
        required_env_key = SERVICE_FIXTURE_ENV_KEYS.get(service_name) if isinstance(service_name, str) else None
        if required_env_key:
            rel = infer_service_fixture_rel(item, fixture_paths)
            if rel is None:
                raise QueryPipelineError(
                    f"Cannot infer fixture path for service {service_name}; expected one fixture under fixtures/{service_name}/."
                )
            env[required_env_key] = f"{output_task_dir}/{rel}"
            rewritten_any = True

        if env or "env" in item or rewritten_any:
            item["env"] = env
        out.append(item)
    return out


def validate_runtime_shape(
    task_yaml_obj: dict[str, Any],
    source_task_yaml_obj: dict[str, Any],
    *,
    fixture_paths: list[str],
) -> None:
    for key in FORBIDDEN_TASK_YAML_KEYS:
        if key in task_yaml_obj:
            raise QueryPipelineError(f"task.yaml should not contain {key} in step2_new output.")

    prompt = task_yaml_obj.get("prompt")
    source_prompt = source_task_yaml_obj.get("prompt")
    if not isinstance(prompt, dict) or not isinstance(source_prompt, dict):
        raise QueryPipelineError("task.yaml.prompt must stay a mapping like the source task.")
    for key in source_prompt:
        if key == "text":
            continue
        if prompt.get(key) != source_prompt.get(key):
            raise QueryPipelineError(f"task.yaml.prompt.{key} must stay consistent with source task.")

    for key in ["version", "category", "difficulty", "tags", "tools", "tool_endpoints", "sandbox_files", "user_agent"]:
        if task_yaml_obj.get(key) != source_task_yaml_obj.get(key):
            raise QueryPipelineError(f"task.yaml.{key} must stay consistent with source task.")

    if task_yaml_obj.get("environment") != source_task_yaml_obj.get("environment"):
        raise QueryPipelineError("task.yaml.environment must stay consistent with source task.")

    source_services = source_task_yaml_obj.get("services")
    actual_services = task_yaml_obj.get("services")
    if not isinstance(source_services, list) or not isinstance(actual_services, list):
        raise QueryPipelineError("task.yaml.services must be a list.")
    if len(source_services) != len(actual_services):
        raise QueryPipelineError("task.yaml.services length must match source task.")

    for idx, (source_service, actual_service) in enumerate(zip(source_services, actual_services, strict=True)):
        if not isinstance(source_service, dict) or not isinstance(actual_service, dict):
            raise QueryPipelineError("task.yaml.services entries must be mappings.")
        for key in ["name", "command", "port", "health_check", "health_check_method", "ready_timeout", "reset_endpoint"]:
            if actual_service.get(key) != source_service.get(key):
                raise QueryPipelineError(f"task.yaml.services[{idx}].{key} must stay consistent with source task.")

        source_env = source_service.get("env") or {}
        actual_env = actual_service.get("env") or {}
        if not isinstance(source_env, dict) or not isinstance(actual_env, dict):
            raise QueryPipelineError(f"task.yaml.services[{idx}].env must be a mapping.")
        for env_key, env_value in source_env.items():
            rel = extract_fixture_rel_from_env_value(env_value, fixture_paths)
            if rel is not None:
                actual_value = actual_env.get(env_key)
                if not isinstance(actual_value, str) or not actual_value.endswith(rel):
                    raise QueryPipelineError(
                        f"task.yaml.services[{idx}].env.{env_key} must point to generated {rel}."
                    )
                continue
            if actual_env.get(env_key) != env_value:
                raise QueryPipelineError(
                    f"task.yaml.services[{idx}].env.{env_key} must preserve non-fixture source values."
                )


def build_task_yaml(
    *,
    task_id: str,
    task_name: str,
    prompt_text: str,
    output_task_dir: str,
    source_task_yaml_obj: dict[str, Any],
    fixture_paths: list[str],
) -> str:
    task_yaml_obj = strip_scoring_fields(source_task_yaml_obj)
    task_yaml_obj["task_id"] = task_id
    task_yaml_obj["task_name"] = task_name

    prompt = deepcopy(task_yaml_obj.get("prompt") or {})
    if not isinstance(prompt, dict):
        raise QueryPipelineError("source task.yaml prompt must be a mapping.")
    prompt["text"] = prompt_text
    task_yaml_obj["prompt"] = prompt

    services = task_yaml_obj.get("services") or []
    if not isinstance(services, list):
        raise QueryPipelineError("source task.yaml services must be a list.")
    task_yaml_obj["services"] = rewrite_services(
        services,
        fixture_paths=fixture_paths,
        output_task_dir=output_task_dir,
    )

    validate_runtime_shape(task_yaml_obj, source_task_yaml_obj, fixture_paths=fixture_paths)
    try:
        TaskDefinition.model_validate(task_yaml_obj)
    except Exception as exc:
        raise QueryPipelineError(f"Generated task.yaml schema validation failed: {exc}") from exc
    return dump_yaml_text(task_yaml_obj)


def render_prompt(
    *,
    source_task_id: str,
    new_desc: dict[str, Any],
    shot_files: dict[str, str],
    shot_summary: dict[str, Any],
    fixture_paths: list[str],
    source_task_yaml_obj: dict[str, Any],
    prompt_language: str,
) -> str:
    new_task_id = new_desc["new_task_id"]
    desc_text = json.dumps(new_desc, ensure_ascii=False, indent=2)
    summary_text = json.dumps(shot_summary, ensure_ascii=False, indent=2)
    fixture_text = json.dumps(fixture_paths, ensure_ascii=False, indent=2)
    mock_today = ((source_task_yaml_obj.get("environment") or {}) if isinstance(source_task_yaml_obj.get("environment"), dict) else {}).get("mock_today")
    if prompt_language == "zh":
        mock_today_line = (
            f"- 源任务 environment.mock_today = {mock_today}，如新任务涉及日期，请让 fixture 中的绝对日期与该日期锚点兼容。"
            if mock_today
            else "- 源任务没有 mock_today；若涉及日期，请保持 fixture 内部自洽即可。"
        )
        language_rule_line = "8. 如果 source task 是中文，task_name、prompt_text、fixture 文本内容优先使用中文。"
        return f"""
你是 Claw-Eval 的 fixture 生成器。现在给你：
1. 一个原始 benchmark task 的运行时 task.yaml（已去掉 grader/scoring 字段）和原始 fixtures 作为参考。
2. 一个由 Step1 生成的新任务描述。

你的任务：只为这个新任务生成新的 fixtures 数据，以及可选的 task_name / prompt_text。
脚本会自动复用 source task 的 services、tools、tool_endpoints、sandbox_files、environment，并程序化写出 task.yaml。

非常重要：你只能输出一个 JSON 对象，不要 markdown，不要解释文字，不要输出 task.yaml，不要输出 grader.py。

输出 JSON 必须严格是：
{{
  "task_id": "{new_task_id}",
  "task_name": "新任务名称",
  "prompt_text": "最终写入 task.yaml 的 prompt.text，可在 Step1 user_request 基础上做必要细化",
  "files": {{
    "fixtures/...json": [{{}}],
    "fixtures/...txt": "完整文本字符串"
  }},
  "notes": {{
    "difference_summary": "一句话说明新题与原题区别",
    "fixture_design_summary": ["..."]
  }}
}}

必须遵守：
1. 只生成 fixtures，不要生成 task.yaml、grader.py、scoring、rubric、reference solution 等任何打分相关内容。
2. files 里的路径必须严格等于下面这组相对路径，不能多、不能少、不能改名：
{fixture_text}
3. `.json` fixture 请直接输出 JSON 对象或数组，不要再包成字符串；`.txt` fixture 才输出字符串。
4. fixtures 的实体、ID、邮件地址、工单号、SKU、数值、正文、会议主题等必须是全新的，不能复用 source fixture 中的具体内容。
5. 需要保证跨 fixture 的实体引用一致。例如邮箱、联系人、客户、会议参会人、ticket_id、product_id 等在不同文件里要能对上。
6. 生成的数据必须能支撑 Step1 描述中的新任务目标，并且仍然适配 source task 已有的 services / tools / tool_endpoints。
7. sandbox_files 会自动沿用 source task；即使 source task 的 sandbox_files 为空，你仍然必须生成上面列出的 fixture 文件，因为服务会直接读取它们。
{language_rule_line}
9. 整体逻辑尽量简单直接，不要额外引入复杂评分陷阱、多轮澄清或 grader 假设。
10. 如果任务涉及具体日期、相对日期或日程类信息，请注意运行时日期锚点：
{mock_today_line}

Step1 生成的新任务描述如下：
{desc_text}

原始 source task 摘要如下：
{summary_text}

下面是 source task 的运行时 task.yaml 和 fixtures：
{file_sections(shot_files)}

最后再次提醒：只输出一个 JSON 对象。
""".strip() + "\n"

    mock_today_line = (
        f"- The source task environment.mock_today = {mock_today}; if the new task involves dates, keep the absolute dates in the fixtures compatible with that anchor."
        if mock_today
        else "- The source task has no mock_today; if dates are involved, keep the fixtures internally consistent."
    )
    language_rule_line = "8. If the source task is English, prefer English for `task_name`, `prompt_text`, and fixture text content."
    return f"""
You are the Claw-Eval fixture generator. You are given:
1. The original benchmark task's runtime task.yaml (with grader/scoring fields removed) and the original fixtures as reference.
2. A new task description produced by Step1.

Your task: generate new fixture data only for this new task, plus optional `task_name` and `prompt_text`.
The script will automatically reuse the source task's services, tools, tool_endpoints, sandbox_files, and environment, then programmatically write task.yaml.

Very important: output exactly one JSON object only. No markdown. No explanations. Do not output task.yaml. Do not output grader.py.

The output JSON must be exactly:
{{
  "task_id": "{new_task_id}",
  "task_name": "new task name",
  "prompt_text": "the prompt.text that will be written into task.yaml, with minor refinements allowed based on Step1 user_request",
  "files": {{
    "fixtures/...json": [{{}}],
    "fixtures/...txt": "full text string"
  }},
  "notes": {{
    "difference_summary": "one sentence describing how the new task differs from the source",
    "fixture_design_summary": ["..."]
  }}
}}

Must follow these rules:
1. Generate fixtures only. Do not generate task.yaml, grader.py, scoring, rubric, reference solution, or any other grading-related content.
2. The file paths in `files` must match exactly the following relative paths, with no additions, omissions, or renames:
{fixture_text}
3. `.json` fixtures must be emitted directly as JSON objects or arrays, not as strings. Only `.txt` fixtures should be strings.
4. Fixture entities, IDs, email addresses, ticket numbers, SKUs, numbers, bodies, meeting subjects, and similar content must all be new and must not reuse specific content from the source fixtures.
5. Cross-fixture entity references must stay consistent. For example, email addresses, contacts, customers, meeting attendees, ticket_id, and product_id must line up across files.
6. The generated data must support the Step1 task goal and remain compatible with the source task's existing services, tools, and tool_endpoints.
7. sandbox_files are inherited automatically from the source task. Even if the source task has an empty sandbox_files list, you must still generate the fixture files listed above because the services read them directly.
{language_rule_line}
9. Keep the logic simple and direct. Do not introduce extra scoring traps, multi-turn clarification flows, or grader assumptions.
10. If the task involves concrete dates, relative dates, or scheduling information, pay attention to the runtime date anchor:
{mock_today_line}

The Step1-generated task description is:
{desc_text}

The source task summary is:
{summary_text}

Below are the source task's runtime task.yaml and fixtures:
{file_sections(shot_files)}

One last reminder: output only one JSON object.
""".strip() + "\n"


def validate_fixture_payload(
    obj: dict[str, Any],
    *,
    expected_task_id: str,
    required_paths: list[str],
    fallback_task_name: str,
    fallback_prompt_text: str,
) -> dict[str, Any]:
    if obj.get("task_id") != expected_task_id:
        raise QueryPipelineError(f"task_id mismatch: expected {expected_task_id}, got {obj.get('task_id')}")

    task_name = str(obj.get("task_name") or fallback_task_name).strip()
    if not task_name:
        raise QueryPipelineError("Model output must include non-empty task_name.")

    prompt_text = str(obj.get("prompt_text") or fallback_prompt_text).strip()
    if not prompt_text:
        raise QueryPipelineError("Model output must include non-empty prompt_text, or Step1 user_request must be non-empty.")

    files = normalise_files_payload(obj)
    for rel in files:
        validate_rel_path(rel)

    required_set = set(required_paths)
    actual_set = set(files.keys())
    missing = sorted(required_set - actual_set)
    extra = sorted(actual_set - required_set)
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"missing={missing}")
        if extra:
            parts.append(f"extra={extra}")
        raise QueryPipelineError("Generated fixtures must exactly match required paths: " + "; ".join(parts))

    for rel in required_paths:
        if rel.endswith(".json"):
            try:
                json.loads(files[rel])
            except json.JSONDecodeError as exc:
                raise QueryPipelineError(f"Fixture {rel} is not valid JSON: {exc}") from exc
        elif rel.endswith(".txt"):
            continue
        else:
            raise QueryPipelineError(f"Unsupported fixture suffix in generated output: {rel}")

    return {
        "task_name": task_name,
        "prompt_text": prompt_text,
        "files": files,
        "notes": obj.get("notes"),
    }


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
        path.write_text(content, encoding="utf-8")
    return stage_dir


def finalize_task_dir(out_base: Path, task_id: str, stage_dir: Path) -> Path:
    task_dir = out_base / "generated" / task_id
    task_dir.parent.mkdir(parents=True, exist_ok=True)
    stage_dir.replace(task_dir)
    return task_dir


def validate_materialized_task_dir(
    task_dir: Path,
    *,
    source_task_yaml_obj: dict[str, Any],
    required_paths: list[str],
) -> None:
    raw_task_yaml = load_yaml_mapping(read_text(task_dir / "task.yaml"), label="materialized task.yaml")
    for key in FORBIDDEN_TASK_YAML_KEYS:
        if key in raw_task_yaml:
            raise QueryPipelineError(f"materialized task.yaml should not contain {key}")

    validate_runtime_shape(raw_task_yaml, source_task_yaml_obj, fixture_paths=required_paths)

    try:
        task = task_definition(task_dir)
    except Exception as exc:
        raise QueryPipelineError(f"Materialized task.yaml schema invalid: {exc}") from exc
    if task.task_id != task_dir.name:
        raise QueryPipelineError(f"Materialized task_id mismatch: {task.task_id} vs {task_dir.name}")
    if (task_dir / "grader.py").exists():
        raise QueryPipelineError("step2_new output should not contain grader.py")

    if task.tools or task.tool_endpoints:
        tool_names = [tool.name for tool in task.tools]
        endpoint_names = [endpoint.tool_name for endpoint in task.tool_endpoints]
        if tool_names != endpoint_names:
            raise QueryPipelineError(
                f"tools/tool_endpoints mismatch: tools={tool_names}, endpoints={endpoint_names}"
            )

    for rel in required_paths:
        validate_rel_path(rel)
        if not (task_dir / rel).exists():
            raise QueryPipelineError(f"required fixture missing on disk: {rel}")

    for rel in task.sandbox_files:
        validate_rel_path(rel)
        if not (task_dir / rel).exists():
            raise QueryPipelineError(f"sandbox_files entry missing on disk: {rel}")

    for rel in task.prompt.attachments:
        validate_rel_path(rel)
        if not (task_dir / rel).exists():
            raise QueryPipelineError(f"prompt.attachments entry missing on disk: {rel}")

    for svc in task.services:
        for env_name, value in (svc.env or {}).items():
            rel = extract_fixture_rel_from_env_value(value, required_paths)
            if rel is None:
                continue
            resolved = (repo_root_default() / value).resolve()
            if not resolved.exists():
                raise QueryPipelineError(f"service env fixture path does not exist: {value}")
            if task_dir.resolve() not in resolved.parents:
                raise QueryPipelineError(f"service env fixture path must stay inside generated task dir: {value}")
            expected_rel = f"{display_path(task_dir, repo_root_default())}/{rel}"
            if value != expected_rel:
                raise QueryPipelineError(
                    f"service {svc.name} env {env_name} should point to {expected_rel}, got {value}"
                )


def process_one_description(
    args: argparse.Namespace,
    *,
    source_task_id: str,
    desc: dict[str, Any],
    shot_files: dict[str, str],
    shot_summary: dict[str, Any],
    source_task_yaml_obj: dict[str, Any],
    prompt_language: str,
    fixture_paths: list[str],
    out_base: Path,
    bundle_path: Path,
) -> Path:
    task_id = desc["new_task_id"]
    final_task_dir = out_base / "generated" / task_id
    bundle = load_task_bundle(
        bundle_path,
        source_task_id=source_task_id,
        source_task_dir=str(Path(args.repo_root).resolve() / "tasks" / source_task_id),
        out_base=out_base,
    )
    step2_item = ensure_step2_item(bundle, task_id, index=int(desc.get("index", 0) or 0))
    prompt = render_prompt(
        source_task_id=source_task_id,
        new_desc=desc,
        shot_files=shot_files,
        shot_summary=shot_summary,
        fixture_paths=fixture_paths,
        source_task_yaml_obj=source_task_yaml_obj,
        prompt_language=prompt_language,
    )

    task_step2_dir = out_base / "tasks" / source_task_id / "step2" / task_id
    task_step2_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = task_step2_dir / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    bundle["step2"]["status"] = "running"
    bundle["step2"]["generated_at"] = now_str()
    bundle["step2"]["error"] = ""
    step2_item["status"] = "prompt_saved"
    step2_item["generated_at"] = now_str()
    step2_item["prompt"] = str(prompt_path)
    step2_item["error"] = ""
    save_task_bundle(bundle_path, bundle)

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
            "你只为 Claw-Eval 单轮任务生成具体 fixture 文件。"
            "只输出一个 JSON 对象，不要输出 task.yaml 或 grader.py。"
            if prompt_language == "zh"
            else "You generate only concrete fixture files for Claw-Eval single-turn tasks. "
            "Output one JSON object only. Do not output task.yaml or grader.py."
        ),
    )
    raw_path = task_step2_dir / "raw.txt"
    raw_path.write_text(raw, encoding="utf-8")
    step2_item["raw"] = str(raw_path)
    save_task_bundle(bundle_path, bundle)

    parsed = extract_json_object(raw)
    parsed_path = task_step2_dir / "parsed.json"
    dump_json(parsed_path, parsed)
    step2_item["parsed"] = str(parsed_path)
    save_task_bundle(bundle_path, bundle)

    fixture_bundle = validate_fixture_payload(
        parsed,
        expected_task_id=task_id,
        required_paths=fixture_paths,
        fallback_task_name=str(desc.get("new_task_name", "")).strip(),
        fallback_prompt_text=str(desc.get("user_request", "")).strip(),
    )

    output_task_dir_for_yaml = display_path(final_task_dir, repo_root_default())
    task_yaml_text = build_task_yaml(
        task_id=task_id,
        task_name=fixture_bundle["task_name"],
        prompt_text=fixture_bundle["prompt_text"],
        output_task_dir=output_task_dir_for_yaml,
        source_task_yaml_obj=source_task_yaml_obj,
        fixture_paths=fixture_paths,
    )

    final_files: dict[str, str] = {"task.yaml": task_yaml_text}
    final_files.update(fixture_bundle["files"])
    stage_dir = write_task_dir(out_base, task_id, final_files, overwrite=args.overwrite)
    task_dir = finalize_task_dir(out_base, task_id, stage_dir)
    try:
        validate_materialized_task_dir(
            task_dir,
            source_task_yaml_obj=source_task_yaml_obj,
            required_paths=fixture_paths,
        )
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise
    step2_item["status"] = "completed"
    step2_item["generated_at"] = now_str()
    step2_item["task_dir"] = str(task_dir)
    step2_item["error"] = ""
    save_task_bundle(bundle_path, bundle)
    print(f"[OK] wrote task directory: {task_dir}")
    return task_dir


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Step2_new: generate concrete task dirs without grader/scoring from Step1 descriptions."
    )
    p.add_argument("--task", help="Source task id, e.g. T001zh_email_triage. Reads step1_output/<task>.json.")
    p.add_argument("--step1-json", help="Explicit Step1 JSON path. Overrides --task for input path.")
    p.add_argument("--repo-root", default=str(repo_root_default()), help="Claw-Eval repo root. Default: parent of create_query.")
    p.add_argument("--output-dir", default=None, help="Run output directory. Default: create_query/output_<BJ_TIMESTAMP>/")
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

        cwd = Path.cwd()
        bundle_path: Path | None = None
        if args.output_dir:
            out_base = resolve_run_output_dir(args.output_dir)
        elif os.environ.get("CREATE_QUERY_RUN_DIR", "").strip():
            out_base = resolve_run_output_dir(None)
        elif args.step1_json:
            step1_json_path = Path(args.step1_json)
            if not step1_json_path.is_absolute():
                step1_json_path = cwd / step1_json_path
            out_base = infer_run_dir_from_path(step1_json_path) or resolve_run_output_dir(None)
        elif args.task:
            out_base = find_latest_run_dir_for_task(args.task) or resolve_run_output_dir(None)
        else:
            out_base = resolve_run_output_dir(None)
        out_base.mkdir(parents=True, exist_ok=True)

        if args.step1_json:
            step1_path = Path(args.step1_json)
            if not step1_path.is_absolute():
                step1_path = cwd / step1_path
        else:
            if not args.task:
                raise QueryPipelineError("When --step1-json is not provided, --task is required.")
            bundle_path = task_bundle_path(out_base, args.task)
            bundle = load_task_bundle(
                bundle_path,
                source_task_id=args.task,
                source_task_dir=str(Path(args.repo_root).resolve() / "tasks" / args.task),
                out_base=out_base,
            )
            step1_output = bundle.get("step1", {}).get("output")
            if isinstance(step1_output, dict):
                step1_obj = step1_output
                step1_path = bundle_path
            else:
                legacy_path = out_base / "tasks" / args.task / "step1" / "output.json"
                if not legacy_path.exists():
                    raise QueryPipelineError(
                        f"Missing Step1 output in bundle and on disk for {args.task}. Run step1 first."
                    )
                step1_path = legacy_path

        if 'step1_obj' not in locals():
            step1_obj = load_step1_json(step1_path)
        source_task_id = step1_obj.get("source_task_id") or args.task
        if not source_task_id:
            raise QueryPipelineError("Cannot determine source_task_id.")
        if bundle_path is None:
            bundle_path = task_bundle_path(out_base, source_task_id)

        repo_root = Path(args.repo_root).resolve()
        source_task_dir = repo_root / "tasks" / source_task_id
        task = task_definition(source_task_dir)
        source_task_yaml_obj = load_yaml_mapping(
            read_text(source_task_dir / "task.yaml", args.max_file_chars),
            label="source task.yaml",
        )
        prompt_language = infer_task_instruction_language(
            source_task_id,
            fallback=str(
                ((source_task_yaml_obj.get("prompt") or {}) if isinstance(source_task_yaml_obj.get("prompt"), dict) else {}).get("language")
                or ""
            ),
        )
        shot_files = collect_shot_files(source_task_dir, args.max_file_chars)
        shot_summary = task_summary(
            task,
            [str(p.relative_to(source_task_dir)) for p in fixture_files(source_task_dir)],
            has_grader=(source_task_dir / "grader.py").exists(),
        )
        fixture_paths = required_fixture_paths(source_task_dir)
        bundle = load_task_bundle(
            bundle_path,
            source_task_id=source_task_id,
            source_task_dir=str(source_task_dir),
            out_base=out_base,
        )
        bundle["step2"]["status"] = "running"
        bundle["step2"]["generated_at"] = now_str()
        bundle["step2"]["error"] = ""
        save_task_bundle(bundle_path, bundle)

        generated: list[dict[str, str]] = []
        for desc in selected_descriptions(step1_obj, args.only_index):
            task_id = desc["new_task_id"]
            try:
                output_path = process_one_description(
                    args,
                    source_task_id=source_task_id,
                    desc=desc,
                    shot_files=shot_files,
                    shot_summary=shot_summary,
                    source_task_yaml_obj=source_task_yaml_obj,
                    prompt_language=prompt_language,
                    fixture_paths=fixture_paths,
                    out_base=out_base,
                    bundle_path=bundle_path,
                )
            except Exception as exc:
                bundle = load_task_bundle(
                    bundle_path,
                    source_task_id=source_task_id,
                    source_task_dir=str(source_task_dir),
                    out_base=out_base,
                )
                step2_item = ensure_step2_item(bundle, task_id, index=int(desc.get("index", 0) or 0))
                step2_item["status"] = "failed"
                step2_item["generated_at"] = now_str()
                step2_item["error"] = str(exc)
                bundle["step2"]["status"] = "partial_failed"
                bundle["step2"]["generated_at"] = now_str()
                bundle["step2"]["error"] = f"{task_id}: {exc}"
                save_task_bundle(bundle_path, bundle)
                print(f"[WARN] failed {task_id}: {exc}", file=sys.stderr)
                continue
            if args.dry_run:
                continue
            generated.append(
                {
                    "task_id": desc["new_task_id"],
                    "task_dir": str(output_path),
                    "bundle": str(bundle_path),
                }
            )

        if args.dry_run:
            return 0

        manifest = {
            "schema_version": "step2_new.generated_tasks.v1",
            "generated_at": now_str(),
            "source_task_id": source_task_id,
            "source_step1_json": str(step1_path),
            "bundle": str(bundle_path),
            "items": generated,
        }
        manifest_path = out_base / "tasks" / source_task_id / "step2" / "manifest.json"
        dump_json(manifest_path, manifest)
        bundle = load_task_bundle(
            bundle_path,
            source_task_id=source_task_id,
            source_task_dir=str(source_task_dir),
            out_base=out_base,
        )
        if bundle["step2"].get("status") != "partial_failed":
            bundle["step2"]["status"] = "completed"
        bundle["step2"]["generated_at"] = now_str()
        bundle["step2"]["manifest"] = str(manifest_path)
        save_task_bundle(bundle_path, bundle)
        print(f"[OK] manifest saved: {manifest_path}")
        return 0
    except QueryPipelineError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
