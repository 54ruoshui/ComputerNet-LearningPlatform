"""Qwen3-8B 基座 vs LoRA 微调版 选择题对比评测（4090 单卡）

用法: python eval_mcq_infer.py
依赖: eval_mcq.json 与本脚本同目录
"""
import json
import os
import re
import time

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE = "/root/autodl-tmp/hugging-facehf/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218"
ADAPTER = "/root/autodl-tmp/LlamaFactory/saves/Qwen3-8B-Thinking/lora/qwen3-8b-cnnet-lora"
HERE = os.path.dirname(os.path.abspath(__file__))

SYSTEM = "你是一位计算机网络课程的阅卷老师。只输出正确选项的字母（A、B、C 或 D），不要输出任何其他内容。"
MAX_NEW = 32
BATCH = 16


def build_prompt(tok, q):
    user = (
        f"{q['text']}\n"
        f"A. {q['a']}\nB. {q['b']}\nC. {q['c']}\nD. {q['d']}\n"
        "正确答案是哪个选项？只输出字母。"
    )
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    return tok.apply_chat_template(
        msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )


@torch.inference_mode()
def answer_all(tok, model, questions, tag):
    model.eval()
    preds = []
    t0 = time.time()
    for i in range(0, len(questions), BATCH):
        chunk = questions[i : i + BATCH]
        texts = [build_prompt(tok, q) for q in chunk]
        enc = tok(texts, return_tensors="pt", padding=True, padding_side="left").to(model.device)
        out = model.generate(
            **enc,
            max_new_tokens=MAX_NEW,
            do_sample=False,
            temperature=None,
            top_p=None,
            top_k=None,
            pad_token_id=tok.pad_token_id or tok.eos_token_id,
        )
        gen = out[:, enc.input_ids.shape[1]:]
        for ids in gen:
            text = tok.decode(ids, skip_special_tokens=True)
            m = re.search(r"[ABCD]", text.upper())
            preds.append(m.group(0) if m else "?")
        print(f"[{tag}] {min(i+BATCH, len(questions))}/{len(questions)}  用时 {time.time()-t0:.0f}s", flush=True)
    return preds


def parse_letter(text):
    m = re.search(r"[ABCD]", text.upper())
    return m.group(0) if m else "?"


def summarize(name, preds, questions):
    total = len(questions)
    correct = sum(p == q["answer"] for p, q in zip(preds, questions))
    by_layer = {}
    for p, q in zip(preds, questions):
        layer = q["layer"] or "未知"
        c, n = by_layer.get(layer, (0, 0))
        by_layer[layer] = (c + (p == q["answer"]), n + 1)
    print(f"\n===== {name} =====")
    print(f"总正确率: {correct}/{total} = {correct/total*100:.1f}%")
    for layer, (c, n) in sorted(by_layer.items()):
        print(f"  {layer}: {c}/{n} = {c/n*100:.0f}%")
    return {"accuracy": correct / total, "by_layer": {k: v[0] / v[1] for k, v in by_layer.items()}}


def main():
    questions = json.load(open(os.path.join(HERE, "eval_mcq.json"), encoding="utf-8"))
    tok = AutoTokenizer.from_pretrained(BASE, padding_side="left")

    print("加载基座模型...", flush=True)
    model = AutoModelForCausalLM.from_pretrained(
        BASE, torch_dtype=torch.bfloat16, device_map="cuda"
    )

    base_preds = answer_all(tok, model, questions, "base")

    print("\n挂载 LoRA 适配器...", flush=True)
    lora_model = PeftModel.from_pretrained(model, ADAPTER)
    sft_preds = answer_all(tok, lora_model, questions, "sft")

    results = {}
    results["base"] = summarize("基座 Qwen3-8B", base_preds, questions)
    results["sft"] = summarize("微调版 (LoRA)", sft_preds, questions)
    results["detail"] = [
        {
            "id": q["id"],
            "answer": q["answer"],
            "base": b,
            "sft": s,
            "layer": q["layer"],
        }
        for q, b, s in zip(questions, base_preds, sft_preds)
    ]
    out_path = os.path.join(HERE, "eval_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
