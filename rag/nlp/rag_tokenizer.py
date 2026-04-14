#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
RAG 分词器模块

本模块提供了文本分词功能，支持中英文分词、词性标注、频率统计等。
主要功能：
- 文本分词（支持中英文混合）
- 细粒度分词
- 词性标注
- 词频统计
- 繁简转换
- 特殊字符处理

Note:
    本模块是对 infinity.rag_tokenizer 的包装，
    根据 DOC_ENGINE 配置选择使用不同的分词器。
"""

import infinity.rag_tokenizer


class RagTokenizer(infinity.rag_tokenizer.RagTokenizer):
    """
    RAG 分词器

    继承自 infinity 的 RagTokenizer，根据配置选择分词策略。

    Note:
        - 当 DOC_ENGINE_INFINITY=True 时，直接返回原文（使用 Infinity 的分词器）
        - 否则使用默认的分词逻辑
    """

    def tokenize(self, line: str) -> str:
        """
        对文本进行分词

        Args:
            line: 待分词的文本

        Returns:
            str: 分词结果

        Note:
            根据 DOC_ENGINE_INFINITY 配置决定是否进行分词
        """
        from common import settings  # 移到函数内避免循环导入
        if settings.DOC_ENGINE_INFINITY:
            return line  # Infinity 模式下直接返回原文
        else:
            return super().tokenize(line)  # 使用默认分词逻辑

    def fine_grained_tokenize(self, tks: str) -> str:
        """
        细粒度分词

        对已分词的文本进行更细粒度的切分。

        Args:
            tks: 已分词的文本

        Returns:
            str: 细粒度分词结果

        Note:
            根据 DOC_ENGINE_INFINITY 配置决定是否进行细粒度分词
        """
        from common import settings  # 移到函数内避免循环导入
        if settings.DOC_ENGINE_INFINITY:
            return tks  # Infinity 模式下直接返回原文
        else:
            return super().fine_grained_tokenize(tks)  # 使用默认细粒度分词


def is_chinese(s):
    """
    判断文本是否为中文

    Args:
        s: 待判断的文本

    Returns:
        bool: 是否为中文
    """
    return infinity.rag_tokenizer.is_chinese(s)


def is_number(s):
    """
    判断文本是否为数字

    Args:
        s: 待判断的文本

    Returns:
        bool: 是否为数字
    """
    return infinity.rag_tokenizer.is_number(s)


def is_alphabet(s):
    """
    判断文本是否为字母

    Args:
        s: 待判断的文本

    Returns:
        bool: 是否为字母
    """
    return infinity.rag_tokenizer.is_alphabet(s)


def naive_qie(txt):
    """
    朴素切词（用于快速分词）

    Args:
        txt: 待切分的文本

    Returns:
        str: 切分结果
    """
    return infinity.rag_tokenizer.naive_qie(txt)


# 创建全局分词器实例
tokenizer = RagTokenizer()

# 导出常用函数的快捷引用
tokenize = tokenizer.tokenize                    # 分词函数
fine_grained_tokenize = tokenizer.fine_grained_tokenize  # 细粒度分词
tag = tokenizer.tag                              # 词性标注
freq = tokenizer.freq                            # 词频统计
tradi2simp = tokenizer._tradi2simp              # 繁简转换
strQ2B = tokenizer._strQ2B                      # 特殊字符处理
