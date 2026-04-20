#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use it except in compliance with the License.
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
高亮工具模块

为搜索结果提供关键词高亮功能（用 <em> 标签包裹）。
"""

import re
from collections.abc import Callable


def highlight_text(
    txt: str,
    keywords: list[str],
    is_english_fn: Callable[[str], bool] | None = None,
) -> str:
    """
    将文本中的关键词用 <em> 标签高亮，按句子处理

    - 如果 is_english_fn(sentence) 为 True：使用单词边界正则表达式
    - 否则：字面替换（优先处理长关键词）
    - 仅返回包含匹配项的句子

    :param txt: 原始文本
    :param keywords: 关键词列表
    :param is_english_fn: 判断是否为英文句子的函数
    :return: 高亮后的文本
    """
    if not txt or not keywords:
        return ""

    txt = re.sub(r"[\r\n]", " ", txt, flags=re.IGNORECASE | re.MULTILINE)
    txt_list = []

    for t in re.split(r"[.?!;\n]", txt):
        t = t.strip()
        if not t:
            continue

        if is_english_fn is None or is_english_fn(t):
            for w in keywords:
                t = re.sub(
                    r"(^|[ .?/'\"\(\)!,:;-])(%s)([ .?/'\"\(\)!,:;-]|$)" % re.escape(w),
                    r"\1<em>\2</em>\3",
                    t,
                    flags=re.IGNORECASE | re.MULTILINE,
                )
        else:
            for w in sorted(keywords, key=len, reverse=True):
                t = re.sub(
                    re.escape(w),
                    f"<em>{w}</em>",
                    t,
                    flags=re.IGNORECASE | re.MULTILINE,
                )

        if re.search(r"<em>[^<>]+</em>", t, flags=re.IGNORECASE | re.MULTILINE):
            txt_list.append(t)

    return "...".join(txt_list) if txt_list else txt


def get_highlight_from_messages(
    messages: list[dict] | None,
    keywords: list[str],
    field_name: str,
    is_english_fn: Callable[[str], bool] | None = None,
) -> dict[str, str]:
    """
    从消息列表构建 id -> 高亮文本的映射

    :param messages: 消息字典列表
    :param keywords: 关键词列表
    :param field_name: 需要高亮的字段名
    :param is_english_fn: 判断是否为英文的函数
    :return: {消息ID: 高亮文本} 的字典
    """
    if not messages or not keywords:
        return {}

    ans = {}
    for doc in messages:
        doc_id = doc.get("id")
        if not doc_id:
            continue
        txt = doc.get(field_name)
        if not txt or not isinstance(txt, str):
            continue
        highlighted = highlight_text(txt, keywords, is_english_fn)
        if highlighted and re.search(r"<em>[^<>]+</em>", highlighted, flags=re.IGNORECASE | re.MULTILINE):
            ans[doc_id] = highlighted
    return ans
