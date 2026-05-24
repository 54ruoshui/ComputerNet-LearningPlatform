"""合并两个数据集为一个统一文件，供 LLaMA-Factory 使用"""
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "datasets")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "..", "LLaMA-Factory", "data")

os.makedirs(OUTPUT_DIR, exist_ok=True)

file1 = os.path.join(DATA_DIR, "datasets-T1q_4mxIfEbg-sharegpt-2026-03-17.json")
file2 = os.path.join(DATA_DIR, "multi-turn-conversations-T1q_4mxIfEbg-2026-03-17.json")

with open(file1, "r", encoding="utf-8") as f:
    data1 = json.load(f)
with open(file2, "r", encoding="utf-8") as f:
    data2 = json.load(f)

merged = data1 + data2

output_path = os.path.join(OUTPUT_DIR, "merged_dataset.json")
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(merged, f, ensure_ascii=False, indent=2)

print(f"合并完成: {len(data1)} + {len(data2)} = {len(merged)} 条")
print(f"保存至: {output_path}")
