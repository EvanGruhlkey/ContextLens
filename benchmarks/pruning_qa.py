"""Exploratory, post-hoc paired code comprehension smoke test.

Three questions, one greedy decode per condition, one small open coding model.
This is not an agent benchmark or a statistical quality-preservation claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from benchmarks.pruning_t4 import use_efficient_sdpa

QUESTIONS = {
    "repository-scope": (
        "What exception does _normalize_targets raise when an absolute target "
        "is outside the repository root? Return JSON with key exception and "
        "the exception class name as its value.",
        {"exception": "ValueError"},
    ),
    "ast-repair": (
        "In _branch_headers, does the implementation add the lineno of every "
        "exception handler when node is ast.Try? Return JSON with key "
        "adds_handler_lineno and a boolean value.",
        {"adds_handler_lineno": True},
    ),
    "paired-order": (
        "When order_seed is None, what are the orders for trials 1 and 2 in "
        "ContextExperimentRunner.run? Return JSON with keys trial_1 and trial_2 "
        "whose values are lists of variant names.",
        {"trial_1": ["base", "candidate"], "trial_2": ["candidate", "base"]},
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pruning-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    name = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    tokenizer = AutoTokenizer.from_pretrained(name)
    model = (
        AutoModelForCausalLM.from_pretrained(
            name,
            torch_dtype=torch.float16,
            attn_implementation="sdpa",
        )
        .to("cuda")
        .eval()
    )
    report = {
        "benchmark_kind": "exploratory_posthoc_code_comprehension_smoke",
        "model": name,
        "model_revision": model.config._commit_hash,
        "agent_quality_measured": False,
        "statistical_quality_preservation_claim": None,
        "questions_selected_after_pruning_run": True,
        "do_sample": False,
        "max_new_tokens": 96,
        "attention_variant": "experimental_efficient_sdpa_expanded_gqa",
        "conditions": [],
    }
    pruning = json.loads(args.pruning_report.read_text())
    for index, row in enumerate(x for x in pruning["cases"] if x["repeat"] == 0):
        source = Path(row["path"]).read_bytes().decode()
        assert hashlib.sha256(source.encode()).hexdigest() == row["source_sha256"]
        question, expected = QUESTIONS[row["case"]]
        # Alternate order and use an independent prompt with no history.
        order = ("full", "pruned") if index % 2 == 0 else ("pruned", "full")
        for condition in order:
            context = source if condition == "full" else row["output"]
            messages = [
                {
                    "role": "system",
                    "content": "Answer using only the supplied code. "
                    'If required implementation is omitted, return {"unknown":true}. '
                    "Return only JSON. Do not infer missing implementation.",
                },
                {
                    "role": "user",
                    "content": f"Code:\n{context}\n\nQuestion: {question}",
                },
            ]
            prompt = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
            inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
            started = time.perf_counter()
            with torch.inference_mode(), use_efficient_sdpa():
                output = model.generate(
                    **inputs,
                    do_sample=False,
                    max_new_tokens=96,
                    pad_token_id=tokenizer.eos_token_id,
                )
            torch.cuda.synchronize()
            length = inputs["input_ids"].shape[1]
            answer = tokenizer.decode(output[0, length:], skip_special_tokens=True)
            match = re.search(r"\{.*\}", answer, re.DOTALL)
            try:
                parsed = json.loads(match.group() if match else answer)
            except json.JSONDecodeError:
                parsed = None
            report["conditions"].append(
                {
                    "case": row["case"],
                    "condition": condition,
                    "question": question,
                    "expected": expected,
                    "answer": answer,
                    "parsed": parsed,
                    "schema_correct": parsed == expected,
                    "factual_correct": isinstance(parsed, dict)
                    and all(
                        parsed.get(key) == value for key, value in expected.items()
                    ),
                    "input_tokens": length,
                    "output_tokens": output.shape[1] - length,
                    "wall_ms": (time.perf_counter() - started) * 1000,
                    "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                }
            )
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            del inputs, output
            torch.cuda.empty_cache()
    report["summary"] = {
        metric: {
            condition: sum(
                x[metric] for x in report["conditions"] if x["condition"] == condition
            )
            for condition in ("full", "pruned")
        }
        for metric in ("factual_correct", "schema_correct")
    }
    report["summary"]["questions"] = len(QUESTIONS)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
