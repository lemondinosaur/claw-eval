---
language:
- en
- zh
license: mit
size_categories:
- n<1K
task_categories:
- other
pretty_name: Claw-Eval
dataset_info:
  features:
  - name: task_id
    dtype: string
  - name: query
    dtype: string
  - name: fixture
    list: string
  - name: language
    dtype: string
  - name: category
    dtype: string
  splits:
  - name: general
    num_bytes: 200118
    num_examples: 161
  - name: multimodal
    num_bytes: 72393
    num_examples: 101
  - name: multi_turn
    num_bytes: 50000
    num_examples: 38
  download_size: 155773
  dataset_size: 322511
configs:
- config_name: default
  data_files:
  - split: general
    path: data/general-*
  - split: multimodal
    path: data/multimodal-*
  - split: multi_turn
    path: data/multi_turn-*
tags:
- agent-bench
- evaluation
- real-world
- multimodal
---

<div align="center">

<h1>Claw-Eval</h1>

<img src="assets/claw_eval.png" alt="Claw-Eval Logo" width="200">

[![Tasks](https://img.shields.io/badge/tasks-300-blue)](#dataset-structure)
[![Leaderboard](https://img.shields.io/badge/leaderboard-live-purple)](https://claw-eval.github.io)
[![License](https://img.shields.io/badge/license-MIT-orange)](https://github.com/claw-eval/claw-eval/blob/main/LICENSE)

**End-to-end transparent benchmark for AI agents acting in the real world.**

[Paper](https://huggingface.co/papers/2604.06132) | [Leaderboard](https://claw-eval.github.io) | [Code](https://github.com/claw-eval/claw-eval)

</div>

---

## Dataset Structure

### Splits

| Split | Examples | Description |
|---|---:|---|
| `general` | 161 | Core agent tasks across 24 categories (communication, finance, ops, productivity, etc.) |
| `multimodal` | 101 | Multimodal agentic tasks requiring perception and creation (webpage generation, video QA, document extraction, etc.) |
| `multi_turn` | 38 | Multi-turn conversational tasks where the agent interacts with a simulated user persona to clarify needs and provide advice |

### Fields

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Unique task identifier |
| `query` | string | Task instruction / description |
| `fixture` | list[string] | Fixture files required for the task (available in `data/fixtures.tar.gz`) |
| `language` | string | Task language (`en` or `zh`) |
| `category` | string | Task domain |

## Usage

```python
from datasets import load_dataset

# Load all splits
dataset = load_dataset("claw-eval/Claw-Eval")

# Load a specific split
general = load_dataset("claw-eval/Claw-Eval", split="general")
multimodal = load_dataset("claw-eval/Claw-Eval", split="multimodal")
multi_turn = load_dataset("claw-eval/Claw-Eval", split="multi_turn")

# Inspect a sample
print(general[0])
```

## Acknowledgements

Our test cases are built on the work of the community. We draw from and adapt tasks contributed by OpenClaw, PinchBench, OfficeQA, OneMillion-Bench, Finance Agent, and Terminal-Bench 2.0.

## Citation

If you use Claw-Eval in your research, please cite:

```bibtex
@article{ye2026claw,
  title={Claw-Eval: Toward Trustworthy Evaluation of Autonomous Agents},
  author={Ye, Bowen and Li, Rang and Yang, Qibin and Liu, Yuanxin and Yao, Linli and Lv, Hanglong and Xie, Zhihui and An, Chenxin and Li, Lei and Kong, Lingpeng and others},
  journal={arXiv preprint arXiv:2604.06132},
  year={2026}
}
```

## Core Contributors
[Bowen Ye](https://github.com/pkuYmiracle)(PKU), [Rang Li](https://github.com/lirang04) (PKU), [Qibin Yang](https://github.com/yangqibin-caibi) (PKU), [Zhihui Xie](https://zhxie.site/)(HKU), [Yuanxin Liu](https://llyx97.github.io/)(PKU), [Linli Yao](https://yaolinli.github.io/)(PKU), [Hanglong Lyu](https://github.com/Albus2002)(PKU), [Lei Li](lilei-nlp.github.io)(HKU, project lead)

## Advisors:
[Tong Yang](https://yangtonghome.github.io/) (PKU), [Zhifang Sui](https://cs.pku.edu.cn/info/1226/2014.htm) (PKU), [Lingpeng Kong](https://ikekonglp.github.io/) (HKU), [Qi Liu](https://leuchine.github.io/) (HKU)

## Contribution

We welcome any kind of contribution. Let us know if you have any suggestions!

## License

This dataset is released under the [MIT License](https://github.com/claw-eval/claw-eval/blob/main/LICENSE).