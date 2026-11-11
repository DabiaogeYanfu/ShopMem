#!/bin/bash
# Load / unload a LoRA in the Reader vLLM (:18100):  bash vllm_lora.sh load <name> <path> | unload <name>
if [ "$1" = load ]; then
  curl -s -X POST localhost:18100/v1/load_lora_adapter -H "Content-Type: application/json" -d "{\"lora_name\":\"$2\",\"lora_path\":\"$3\"}"
else
  curl -s -X POST localhost:18100/v1/unload_lora_adapter -H "Content-Type: application/json" -d "{\"lora_name\":\"$2\"}"
fi
echo
