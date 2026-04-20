#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""
消息工具模块

提供 LLM 响应的 JSON 解析功能。
"""
import json


def get_json_result_from_llm_response(response_str: str) -> dict:
    """
    从 LLM 响应字符串中提取 JSON 内容

    查找第一个和最后一个花括号来识别 JSON 部分。
    如果解析失败，返回空字典。

    :param response_str: LLM 响应字符串
    :return: 从响应中解析出的 JSON 字典
    """
    try:
        clean_str = response_str.strip()
        if clean_str.startswith('```json'):
            clean_str = clean_str[7:]  # Remove the starting ```json
        if clean_str.endswith('```'):
            clean_str = clean_str[:-3]  # Remove the ending ```

        return json.loads(clean_str.strip())
    except (ValueError, json.JSONDecodeError):
        return {}
