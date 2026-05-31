import json
import os
from openai import OpenAI

client = OpenAI(
    base_url="https://api.novita.ai/openai",
    api_key="sk_mA_UckV5hBLUPAgRH_1LHL80nTuWbBinorSoTSINdGQ"
)

# 单轮对话示例
print("----- 单轮对话 -----")
completion = client.chat.completions.create(
    model="pa/gemini-3-flash-preview",
    messages=[
        {"role": "system", "content": "你是一个 AI 人工智能助手"},
        {"role": "user", "content": "请介绍一下太阳系的八大行星"},
    ],
    temperature=0.7
)
print(completion.choices[0].message.content)