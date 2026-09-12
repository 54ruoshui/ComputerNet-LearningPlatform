"""开放问答对比：基座 vs LoRA 微调版，输出完整回答供人工挑选典型案例"""
import json
import os

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE = "/root/autodl-tmp/hugging-facehf/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218"
ADAPTER = "/root/autodl-tmp/LlamaFactory/saves/Qwen3-8B-Thinking/lora/qwen3-8b-cnnet-lora"
HERE = os.path.dirname(os.path.abspath(__file__))

SYSTEM = (
    "你是一位计算机网络教学AI，严格按以下规则生成回答：\n"
    "1. 关联教材：每次回答必须基于当前材料中已明确提到的关联知识点（例如：讲“TCP”时，必须提到教材中“传输层”和“三次握手”部分；"
    "讲“OSPF”时，必须提到“路由协议”和“区域划分”）。禁止编造材料未提及的内容。\n"
    "2. 回答风格：先给出严谨的概念定义，再用生活化类比帮助理解，然后解释机制与教材中的具体场景。\n"
    "3. 教材为本：所有技术描述必须与教材一致。"
)

QUESTIONS = [
    {"id": "q1", "layer": "传输层", "text": "TCP 三次握手为什么不能简化成两次？如果只用两次握手会出现什么问题？"},
    {"id": "q2", "layer": "应用层", "text": "HTTPS 中 TLS 是如何保证报文不被篡改的？如果攻击者删除了某个报文段会怎样？"},
    {"id": "q3", "layer": "传输层", "text": "为什么实时视频通话通常用 UDP 而不是 TCP？TCP 的重传机制在这种场景下会带来什么问题？"},
    {"id": "q4", "layer": "网络层", "text": "请解释 ARP 协议的工作过程，以及 ARP 欺骗的原理和防范方法。"},
    {"id": "q5", "layer": "网络层", "text": "路由器和交换机在工作层次、转发依据和适用场景上有什么区别？"},
    {"id": "q6", "layer": "传输层", "text": "TCP 拥塞控制中慢启动和拥塞避免阶段的区别是什么？cwnd 是如何变化的？"},
    {"id": "q7", "layer": "应用层", "text": "在双向鉴别协议中，不重数（nonce）的作用是什么？攻击者如何利用不重数交互的漏洞实施攻击？"},
    {"id": "q8", "layer": "应用层", "text": "DNS 的递归查询和迭代查询有什么区别？域名解析的完整过程是怎样的？"},
]

MAX_NEW = 1024
BATCH = 4


def build_prompt(tok, q):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q["text"]}]
    return tok.apply_chat_template(
        msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )


@torch.inference_mode()
def answer_all(tok, model, tag):
    preds = {}
    for i in range(0, len(QUESTIONS), BATCH):
        chunk = QUESTIONS[i : i + BATCH]
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
        for q, ids in zip(chunk, gen):
            preds[q["id"]] = tok.decode(ids, skip_special_tokens=True).strip()
        print(f"[{tag}] {i+len(chunk)}/{len(QUESTIONS)}", flush=True)
    return preds


def main():
    tok = AutoTokenizer.from_pretrained(BASE, padding_side="left")
    model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.bfloat16, device_map="cuda")

    base = answer_all(tok, model, "base")
    lora_model = PeftModel.from_pretrained(model, ADAPTER)
    sft = answer_all(tok, lora_model, "sft")

    result = {
        "system_prompt": SYSTEM,
        "questions": QUESTIONS,
        "answers": {
            q["id"]: {"layer": q["layer"], "question": q["text"], "base": base[q["id"]], "sft": sft[q["id"]]}
            for q in QUESTIONS
        },
    }
    with open(os.path.join(HERE, "qa_showcase.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("已保存 qa_showcase.json")


if __name__ == "__main__":
    main()
