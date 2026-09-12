---
library_name: peft
license: other
base_model: /root/autodl-tmp/hugging-facehf/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218
tags:
- base_model:adapter:/root/autodl-tmp/hugging-facehf/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218
- llama-factory
- lora
- transformers
pipeline_tag: text-generation
model-index:
- name: qwen3-8b-cnnet-lora
  results: []
---

<!-- This model card has been generated automatically according to the information the Trainer had access to. You
should probably proofread and complete it, then remove this comment. -->

# qwen3-8b-cnnet-lora

This model is a fine-tuned version of [/root/autodl-tmp/hugging-facehf/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218](https://huggingface.co//root/autodl-tmp/hugging-facehf/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218) on the ComputerNet_2 and the ComputerNet_3_turn datasets.

## Model description

More information needed

## Intended uses & limitations

More information needed

## Training and evaluation data

More information needed

## Training procedure

### Training hyperparameters

The following hyperparameters were used during training:
- learning_rate: 0.0001
- train_batch_size: 2
- eval_batch_size: 8
- seed: 42
- gradient_accumulation_steps: 8
- total_train_batch_size: 16
- optimizer: Use OptimizerNames.ADAMW_TORCH with betas=(0.9,0.999) and epsilon=1e-08 and optimizer_args=No additional optimizer arguments
- lr_scheduler_type: cosine
- lr_scheduler_warmup_steps: 0.1
- num_epochs: 3.0

### Training results



### Framework versions

- PEFT 0.18.1
- Transformers 5.8.0
- Pytorch 2.14.0+cu130
- Datasets 4.0.0
- Tokenizers 0.22.2