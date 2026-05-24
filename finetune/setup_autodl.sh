#!/bin/bash
# AutoDL 环境初始化脚本
# 使用方法: bash setup_autodl.sh

set -e

echo "=== 1. 安装 LLaMA-Factory ==="
cd /root
if [ ! -d "LLaMA-Factory" ]; then
    git clone --depth 1 https://github.com/hiyouga/LLaMA-Factory.git
fi
cd LLaMA-Factory
pip install -e ".[torch,metrics]"

echo "=== 2. 下载 Qwen2.5-7B-Instruct 模型 ==="
# 方式一: 使用 modelscope（国内速度快）
pip install modelscope
model_dir="/root/autodl-tmp/Qwen2.5-7B-Instruct"
if [ ! -d "$model_dir" ]; then
    modelscope download --model Qwen/Qwen2.5-7B-Instruct --local_dir "$model_dir"
fi

echo "=== 3. 准备数据集 ==="
# 将数据集复制到 LLaMA-Factory 数据目录
cp -f /root/autodl-tmp/finetune/datasets/*.json /root/LLaMA-Factory/data/

# 注册数据集到 dataset_info.json
python3 /root/autodl-tmp/finetune/merge_dataset.py

# 合并 dataset_info.json
python3 -c "
import json
with open('data/dataset_info.json', 'r') as f:
    existing = json.load(f)
with open('/root/autodl-tmp/finetune/dataset_info.json', 'r') as f:
    new = json.load(f)
existing.update(new)
with open('data/dataset_info.json', 'w') as f:
    json.dump(existing, f, indent=2, ensure_ascii=False)
print('数据集注册完成')
"

echo "=== 4. 开始训练 ==="
llamafactory-cli train /root/autodl-tmp/finetune/train_qwen2.5_7b_lora.yaml

echo "=== 5. 合并 LoRA 权重 ==="
llamafactory-cli export /root/autodl-tmp/finetune/merge_config.yaml

echo "=== 完成 ==="
echo "合并后的模型保存在: /root/autodl-tmp/qwen2.5-7b-cn-network-merged"
