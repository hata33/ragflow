import os

PROMPT_DIR = os.path.dirname(__file__)

_loaded_prompts = {}

"""
提示词模板加载模块

本模块提供提示词模板的加载和缓存功能。

主要功能：
- 从 markdown 文件加载提示词模板
- 提示词缓存
- 提示词文件路径解析

使用方式：
    from rag.prompts.template import load_prompt
    prompt = load_prompt("question_prompt")

Note:
    - 提示词文件应放在 prompts/ 目录下
    - 文件格式为 .md
    - 支持缓存避免重复读取
"""


def load_prompt(name: str) -> str:
    if name in _loaded_prompts:
        return _loaded_prompts[name]

    path = os.path.join(PROMPT_DIR, f"{name}.md")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Prompt file '{name}.md' not found in prompts/ directory.")

    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        _loaded_prompts[name] = content
        return content
