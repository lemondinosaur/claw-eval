#!/usr/bin/env python3
"""Step 1: analyze one task and generate 5 new task descriptions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from create_query.common import (  # noqa: E402
    BASE_URL,
    API_KEY,
    MODEL,
    DEFAULT_MAX_FILE_CHARS,
    QueryPipelineError,
    call_chat,
    dump_json,
    extract_json_object,
    file_sections,
    infer_task_instruction_language,
    load_task_tags,
    load_task_bundle,
    now_str,
    read_text,
    repo_root_default,
    resolve_run_output_dir,
    save_task_bundle,
    supported_fixture_files,
    task_definition,
    task_in_generation_scope,
    task_summary,
    task_bundle_path,
)


def render_prompt(source_task_id: str, source_summary: dict[str, Any], source_files: dict[str, str]) -> str:
    summary_text = json.dumps(source_summary, ensure_ascii=False, indent=2)
    language = infer_task_instruction_language(
        source_task_id,
        fallback=str(source_summary.get("language") or ""),
    )
    if language == "zh":
        return f"""
你是一个 Claw-Eval 数据构造助手。现在给你一个已有 benchmark task 的 task.yaml 和 fixtures。

你的任务：基于这个原始 task 的任务类型，设计 5 个新的任务描述。注意：这里只生成“新任务描述 JSON”，不要生成 task.yaml、fixtures、grader.py。

必须遵守：
1. 只输出一个 JSON 对象，不要 markdown，不要解释文字。
2. 5 个新任务要和原 task 属于同一大类能力，能复用相同或高度相近的 service/tool schema。
3. 5 个新任务必须和原题有较大区别，不能只是改名字、改日期、改语言。
4. 每个新任务描述要足够具体，后续另一个模型可以直接根据描述生成 task.yaml、fixtures 和 grader.py。
5. 不要复用原 fixture 中的具体实体名、邮箱、客户名、工单 ID、SKU、数值、原文段落。
6. 尽量增加场景多样性：业务触发器、用户目标、状态分布、干扰信息、边界条件都要有变化。
7. 新任务 id 必须严格使用：{source_task_id}_plus_1 到 {source_task_id}_plus_5。
8. user_request 要和原任务语言一致；如果原题是中文，优先写中文。
9. 不要引入原 task 没有的多轮澄清流程。

输出 JSON 格式必须是：
{{
  "source_task_id": "{source_task_id}",
  "source_summary": {{
    "task_type": "一句话概括原任务类型",
    "services": ["从 task.yaml 读取的服务名"],
    "tools": ["从 task.yaml 读取的工具名"],
    "fixture_paths": ["fixtures/..."],
    "core_capability": "这个任务主要考察的能力"
  }},
  "new_task_descriptions": [
    {{
      "index": 1,
      "new_task_id": "{source_task_id}_plus_1",
      "new_task_name": "简短英文或拼音名称",
      "task_goal": "新任务要完成什么，写具体",
      "user_request": "最终写入 task.yaml prompt.text 的自然语言用户请求",
      "scenario": "新的业务/世界状态场景，必须具体",
      "difference_from_source": "说明和原始任务的核心差异，不要空泛",
      "service_and_tool_plan": "应该继续使用哪些服务和工具，以及为什么",
      "fixture_generation_plan": "需要生成哪些 fixture、多少条记录、关键字段和状态分布",
      "expected_behavior": "agent 应该如何完成任务，包括关键工具调用和最终输出要求",
      "grading_plan": "后续生成 grader.py 时应检查什么，写成自然语言即可"
    }}
  ]
}}

原始任务元信息：
{summary_text}

原始 task 文件如下：
{file_sections(source_files)}
""".strip() + "\n"

    return f"""
You are a Claw-Eval data construction assistant. You are given an existing benchmark task's task.yaml and fixtures.

Your task: design 5 new task descriptions based on the original task type. Only generate the "new task descriptions JSON"; do not generate task.yaml, fixtures, or grader.py.

Must follow these rules:
1. Output exactly one JSON object only. No markdown. No explanations.
2. The 5 new tasks must stay in the same broad capability class and reuse the same or a very similar service/tool schema.
3. The 5 new tasks must differ materially from the original task; do not just change the name, date, or language.
4. Each new task description must be detailed enough for another model to directly generate task.yaml, fixtures, and grader.py from it.
5. Do not reuse specific entity names, email addresses, customer names, ticket IDs, SKUs, numbers, or source text passages from the original fixtures.
6. Maximize scenario diversity: vary business triggers, user goals, state distributions, distractors, and edge cases.
7. The new task IDs must be exactly: {source_task_id}_plus_1 through {source_task_id}_plus_5.
8. `user_request` must match the original task language; if the source task is English, write it in English.
9. Do not introduce multi-turn clarification flows that the original task did not have.

Output JSON format must be:
{{
  "source_task_id": "{source_task_id}",
  "source_summary": {{
    "task_type": "one-sentence summary of the original task type",
    "services": ["service names from task.yaml"],
    "tools": ["tool names from task.yaml"],
    "fixture_paths": ["fixtures/..."],
    "core_capability": "the main capability being tested"
  }},
  "new_task_descriptions": [
    {{
      "index": 1,
      "new_task_id": "{source_task_id}_plus_1",
      "new_task_name": "short English or pinyin name",
      "task_goal": "what the new task should accomplish, described concretely",
      "user_request": "the natural-language user request to write into task.yaml prompt.text",
      "scenario": "a new business/world-state scenario, with concrete details",
      "difference_from_source": "explain the core difference from the original task, not vaguely",
      "service_and_tool_plan": "which services and tools to keep using, and why",
      "fixture_generation_plan": "which fixtures to generate, how many records, key fields, and state distribution",
      "expected_behavior": "how the agent should complete the task, including key tool calls and final output requirements",
      "grading_plan": "what a future grader.py should check, written in natural language"
    }}
  ]
}}

Original task metadata:
{summary_text}

Original task files:
{file_sections(source_files)}
""".strip() + "\n"


def normalise_step1_output(
    obj: dict[str, Any],
    *,
    source_task_id: str,
    source_summary: dict[str, Any],
) -> dict[str, Any]:
    tasks = obj.get("new_task_descriptions") or obj.get("tasks") or obj.get("descriptions")
    if not isinstance(tasks, list):
        raise QueryPipelineError("Model output must contain new_task_descriptions as a list.")
    if len(tasks) != 5:
        raise QueryPipelineError(f"Expected exactly 5 task descriptions, got {len(tasks)}.")

    normalized_tasks = []
    for i, item in enumerate(tasks, start=1):
        if not isinstance(item, dict):
            raise QueryPipelineError(f"Task description {i} is not an object.")
        item = dict(item)
        item["index"] = i
        item["new_task_id"] = f"{source_task_id}_plus_{i}"
        required = [
            "new_task_name",
            "task_goal",
            "user_request",
            "scenario",
            "difference_from_source",
            "service_and_tool_plan",
            "fixture_generation_plan",
            "expected_behavior",
            "grading_plan",
        ]
        missing = [k for k in required if not str(item.get(k, "")).strip()]
        if missing:
            raise QueryPipelineError(f"Task description {i} missing fields: {missing}")
        normalized_tasks.append(item)

    output_summary = dict(source_summary)
    model_summary = obj.get("source_summary")
    if isinstance(model_summary, dict):
        if str(model_summary.get("task_type", "")).strip():
            output_summary["task_type"] = str(model_summary["task_type"]).strip()
        if str(model_summary.get("core_capability", "")).strip():
            output_summary["core_capability"] = str(model_summary["core_capability"]).strip()

    return {
        "schema_version": "step1.task_descriptions.v2",
        "generated_at": now_str(),
        "source_task_id": source_task_id,
        "source_summary": output_summary,
        "new_task_descriptions": normalized_tasks,
    }


def collect_source_files(task_dir: Path, max_file_chars: int) -> dict[str, str]:
    files: dict[str, str] = {"task.yaml": read_text(task_dir / "task.yaml", max_file_chars)}
    for p in supported_fixture_files(task_dir):
        files[str(p.relative_to(task_dir))] = read_text(p, max_file_chars)
    return files


def select_tasks_for_all(repo_root: Path) -> list[str]:
    tasks_dir = repo_root / "tasks"
    if not tasks_dir.exists():
        raise QueryPipelineError(f"No tasks dir found under {repo_root}")

    task_tags_path = repo_root / "judge_task" / "task_tags.jsonl"
    task_tags = load_task_tags(task_tags_path) if task_tags_path.exists() else None

    selected: list[str] = []
    for child in sorted(tasks_dir.iterdir()):
        if not child.is_dir() or not (child / "task.yaml").exists():
            continue
        ok, _reason = task_in_generation_scope(child, task_tags=task_tags)
        if not ok:
            continue
        selected.append(child.name)
    return selected


def process_one_task(args: argparse.Namespace, task_name: str) -> Path:
    repo_root = Path(args.repo_root).resolve()
    task_dir = repo_root / "tasks" / task_name
    if not task_dir.exists():
        raise QueryPipelineError(f"Task dir not found: {task_dir}")

    ok, reason = task_in_generation_scope(task_dir)
    if not ok:
        raise QueryPipelineError(f"Unsupported task {task_name}: {reason}")

    task = task_definition(task_dir)
    fixture_paths = [str(p.relative_to(task_dir)) for p in supported_fixture_files(task_dir)]
    source_summary = task_summary(task, fixture_paths, has_grader=(task_dir / "grader.py").exists())
    source_files = collect_source_files(task_dir, args.max_file_chars)
    prompt = render_prompt(task_name, source_summary, source_files)
    prompt_language = infer_task_instruction_language(
        task_name,
        fallback=str(source_summary.get("language") or ""),
    )

    out_dir = resolve_run_output_dir(args.output_dir)
    step1_dir = out_dir / "tasks" / task_name / "step1"
    step1_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = task_bundle_path(out_dir, task_name)
    bundle = load_task_bundle(
        bundle_path,
        source_task_id=task_name,
        source_task_dir=str(task_dir),
        out_base=out_dir,
    )

    prompt_path = step1_dir / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    bundle["step1"]["status"] = "prompt_saved"
    bundle["step1"]["generated_at"] = now_str()
    bundle["step1"]["prompt"] = str(prompt_path)
    bundle["step1"]["error"] = ""
    save_task_bundle(bundle_path, bundle)

    if args.dry_run:
        print(f"[OK] dry-run prompt saved: {prompt_path}")
        return prompt_path

    try:
        raw = call_chat(
            prompt,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            system_prompt=(
                "你只分析 benchmark 任务并输出 JSON。"
                if prompt_language == "zh"
                else "You analyze benchmark tasks and output JSON only."
            ),
        )
        raw_path = step1_dir / "raw.txt"
        raw_path.write_text(raw, encoding="utf-8")
        bundle["step1"]["raw"] = str(raw_path)

        parsed = extract_json_object(raw)
        normalized = normalise_step1_output(parsed, source_task_id=task_name, source_summary=source_summary)
        out_path = step1_dir / "output.json"
        dump_json(out_path, normalized)
        bundle["step1"]["status"] = "completed"
        bundle["step1"]["generated_at"] = now_str()
        bundle["step1"]["output"] = normalized
        bundle["step1"]["error"] = ""
        save_task_bundle(bundle_path, bundle)
        print(f"[OK] step1 output saved: {out_path}")
        return out_path
    except Exception as exc:
        bundle["step1"]["status"] = "failed"
        bundle["step1"]["generated_at"] = now_str()
        bundle["step1"]["error"] = str(exc)
        save_task_bundle(bundle_path, bundle)
        raise


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Step1: generate 5 new task descriptions from one Claw-Eval task.")
    p.add_argument("--task", help="Task dir name under ../tasks, e.g. T001zh_email_triage")
    p.add_argument("--all", action="store_true", help="Process all supported tasks, including both zh and en variants.")
    p.add_argument("--repo-root", default=str(repo_root_default()), help="Claw-Eval repo root. Default: parent of create_query.")
    p.add_argument("--output-dir", default=None, help="Run output directory. Default: create_query/output_<BJ_TIMESTAMP>/")
    p.add_argument("--base-url", default=os.environ.get("CREATE_QUERY_BASE_URL", BASE_URL))
    p.add_argument("--api-key", default=os.environ.get("CREATE_QUERY_API_KEY", API_KEY))
    p.add_argument("--model", default=os.environ.get("CREATE_QUERY_MODEL", MODEL))
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--max-tokens", type=int, default=128000)
    p.add_argument("--max-file-chars", type=int, default=DEFAULT_MAX_FILE_CHARS)
    p.add_argument("--dry-run", action="store_true", help="Only write prompt, do not call API.")
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        if not args.task and not args.all:
            raise QueryPipelineError("Pass --task TASK_NAME or --all.")
        if args.task and args.all:
            raise QueryPipelineError("Use either --task or --all, not both.")

        args.output_dir = str(resolve_run_output_dir(args.output_dir))

        if args.all:
            names = select_tasks_for_all(Path(args.repo_root).resolve())
            print(f"[INFO] selected {len(names)} supported tasks")
            for name in names:
                try:
                    process_one_task(args, name)
                except Exception as exc:
                    print(f"[WARN] failed {name}: {exc}", file=sys.stderr)
            return 0

        process_one_task(args, args.task)
        return 0
    except QueryPipelineError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
