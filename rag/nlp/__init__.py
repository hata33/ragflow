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
NLP 文本处理模块

本模块提供 RAGFlow 的核心文本处理功能，包括：
- 文本分块（chunking）：将长文本分割为适合检索的小块
- 分词（tokenization）：为文本添加分词和权重信息
- 语言检测：识别中文、英文等语言
- 层级合并：按标题层级组织文档结构
- 表格和图片处理：为表格和图片添加上下文
- 编码检测：自动检测文本编码

主要函数：
- naive_merge(): 朴素文本分块算法
- naive_merge_with_images(): 带图片的分块算法
- naive_merge_docx(): DOCX 文档专用分块算法
- tokenize_chunks(): 为文本块添加分词信息
- attach_media_context(): 为媒体元素添加上下文
- is_english()/is_chinese(): 语言检测
"""

import logging
import random
from collections import Counter, defaultdict

from common.token_utils import num_tokens_from_string
import re
import copy
import roman_numbers as r
from word2number import w2n
from cn2an import cn2an
from PIL import Image

import chardet

__all__ = ['rag_tokenizer']

# ========== 支持的文本编码列表 ==========
# 包含了几乎所有常见的文本编码格式，用于自动编码检测
all_codecs = [
    'utf-8', 'gb2312', 'gbk', 'utf_16', 'ascii', 'big5', 'big5hkscs',
    'cp037', 'cp273', 'cp424', 'cp437',
    'cp500', 'cp720', 'cp737', 'cp775', 'cp850', 'cp852', 'cp855', 'cp856', 'cp857',
    'cp858', 'cp860', 'cp861', 'cp862', 'cp863', 'cp864', 'cp865', 'cp866', 'cp869',
    'cp874', 'cp875', 'cp932', 'cp949', 'cp950', 'cp1006', 'cp1026', 'cp1125',
    'cp1140', 'cp1250', 'cp1251', 'cp1252', 'cp1253', 'cp1254', 'cp1255', 'cp1256',
    'cp1257', 'cp1258', 'euc_jp', 'euc_jis_2004', 'euc_jisx0213', 'euc_kr',
    'gb18030', 'hz', 'iso2022_jp', 'iso2022_jp_1', 'iso2022_jp_2',
    'iso2022_jp_2004', 'iso2022_jp_3', 'iso2022_jp_ext', 'iso2022_kr', 'latin_1',
    'iso8859_2', 'iso8859_3', 'iso8859_4', 'iso8859_5', 'iso8859_6', 'iso8859_7',
    'iso8859_8', 'iso8859_9', 'iso8859_10', 'iso8859_11', 'iso8859_13',
    'iso8859_14', 'iso8859_15', 'iso8859_16', 'johab', 'koi8_r', 'koi8_t', 'koi8_u',
    'kz1048', 'mac_cyrillic', 'mac_greek', 'mac_iceland', 'mac_latin2', 'mac_roman',
    'mac_turkish', 'ptcp154', 'shift_jis', 'shift_jis_2004', 'shift_jisx0213',
    'utf_32', 'utf_32_be', 'utf_32_le', 'utf_16_be', 'utf_16_le', 'utf_7', 'windows-1250', 'windows-1251',
    'windows-1252', 'windows-1253', 'windows-1254', 'windows-1255', 'windows-1256',
    'windows-1257', 'windows-1258', 'latin-2'
]


def find_codec(blob):
    """
    自动检测文本编码

    该函数用于检测二进制数据的文本编码，支持几乎所有常见的编码格式。
    首先使用 chardet 库进行检测，如果置信度不够则逐个尝试编码列表。

    Args:
        blob: 二进制文本数据

    Returns:
        str: 检测到的编码名称，默认返回 "utf-8"

    Processing Steps:
        1. 使用 chardet 检测前 1024 字节的编码
        2. 如果置信度 > 0.5，使用检测结果（ascii 转为 utf-8）
        3. 否则逐个尝试 all_codecs 中的编码
        4. 如果都失败，返回 utf-8 作为默认值

    Note:
        - 优先检测前 1024 字节以提高性能
        - ascii 编码会被转换为 utf-8（兼容性更好）
    """
    # 步骤1：使用 chardet 快速检测
    detected = chardet.detect(blob[:1024])
    if detected['confidence'] > 0.5:
        if detected['encoding'] == "ascii":
            return "utf-8"

    # 步骤2：逐个尝试编码列表
    for c in all_codecs:
        try:
            # 先尝试前 1024 字节
            blob[:1024].decode(c)
            return c
        except Exception:
            pass
        try:
            # 再尝试整个文件
            blob.decode(c)
            return c
        except Exception:
            pass

    # 步骤3：返回默认编码
    return "utf-8"


# ========== 问题编号模式列表 ==========
# 用于识别文档中的问题编号（如"第一问"、"第1条"等）
# 支持中文数字、阿拉伯数字、罗马数字等多种格式
QUESTION_PATTERN = [
    r"第([零一二三四五六七八九十百0-9]+)问",      # 中文数字 + 问
    r"第([零一二三四五六七八九十百0-9]+)条",      # 中文数字 + 条
    r"[\(（]([零一二三四五六七八九十百]+)[\)）]",  # 括号包围的中文数字
    r"第([0-9]+)问",                           # 阿拉伯数字 + 问
    r"第([0-9]+)条",                           # 阿拉伯数字 + 条
    r"([0-9]{1,2})[\. 、]",                    # 1-2位数字 + 点或空格
    r"([零一二三四五六七八九十百]+)[ 、]",      # 中文数字 + 空格
    r"[\(（]([0-9]{1,2})[\)）]",             # 括号包围的阿拉伯数字
    r"QUESTION (ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)",  # 英文数字单词
    r"QUESTION (I+V?|VI*|XI|IX|X)",           # 罗马数字
    r"QUESTION ([0-9]+)",                     # QUESTION + 阿拉伯数字
]


def has_qbullet(reg, box, last_box, last_index, last_bull, bull_x0_list):
    """
    检查文本框是否包含问题编号

    该函数用于识别文档中的问题编号（如试卷中的题目编号），
    并验证编号的有效性（位置、顺序、格式等）。

    Args:
        reg: 正则表达式模式，用于匹配问题编号
        box: 当前文本框，包含 text、x0、top、layout_type 等字段
        last_box: 上一个文本框
        last_index: 上一个问题编号的索引值
        last_bull: 上一个是否为问题编号的标志
        bull_x0_list: 问题编号的 x0 坐标列表

    Returns:
        tuple: (match, index)
            - match: 匹配到的正则表达式对象，如果不是有效编号则为 None
            - index: 问题编号的整数值，失败时返回 last_index

    Validation Rules:
        1. 位置验证：x0 坐标不能偏离平均值超过 10
        2. 顺序验证：编号应该递增（特殊情况下可递减）
        3. 格式验证：上一段不能以冒号结尾
        4. 特殊情况：以问号结尾或为标题时允许递减

    Note:
        - 用于文档解析时识别问题结构
        - 支持中英文问题编号格式
    """
    section, last_section = box['text'], last_box['text']
    q_reg = r'(\w|\W)*?(?:？|\?|\n|$)+'  # 匹配到问号或行尾
    full_reg = reg + q_reg
    has_bull = re.match(full_reg, section)
    index_str = None

    if has_bull:
        # 初始化位置信息
        if 'x0' not in last_box:
            last_box['x0'] = box['x0']
        if 'top' not in last_box:
            last_box['top'] = box['top']

        # 位置验证：检查 x0 坐标偏差
        if last_bull and box['x0'] - last_box['x0'] > 10:
            return None, last_index
        if not last_bull and box['x0'] >= last_box['x0'] and box['top'] - last_box['top'] < 20:
            return None, last_index

        # 计算平均 x0 坐标
        avg_bull_x0 = 0
        if bull_x0_list:
            avg_bull_x0 = sum(bull_x0_list) / len(bull_x0_list)
        else:
            avg_bull_x0 = box['x0']

        if box['x0'] - avg_bull_x0 > 10:
            return None, last_index

        # 提取编号索引
        index_str = has_bull.group(1)
        index = index_int(index_str)

        # 格式验证：上一段不能以冒号结尾
        if last_section[-1] == ':' or last_section[-1] == '：':
            return None, last_index

        # 顺序验证：编号应该递增
        if not last_index or index >= last_index:
            bull_x0_list.append(box['x0'])
            return has_bull, index

        # 特殊情况：允许递减的情况
        if section[-1] == '?' or section[-1] == '？':
            bull_x0_list.append(box['x0'])
            return has_bull, index
        if box['layout_type'] == 'title':
            bull_x0_list.append(box['x0'])
            return has_bull, index

        # 检查是否为问句开头
        pure_section = section.lstrip(re.match(reg, section).group()).lower()
        ask_reg = r'(what|when|where|how|why|which|who|whose|为什么|为啥|哪)'
        if re.match(ask_reg, pure_section):
            bull_x0_list.append(box['x0'])
            return has_bull, index

    return None, last_index


def index_int(index_str):
    """
    将各种格式的数字字符串转换为整数

    支持中文数字、阿拉伯数字、英文单词、罗马数字等多种格式。

    Args:
        index_str: 数字字符串（如"一"、"1"、"one"、"I"）

    Returns:
        int: 转换后的整数值，失败时返回 -1

    Conversion Order:
        1. 尝试直接转换为 int（阿拉伯数字）
        2. 尝试 word_to_num（英文单词）
        3. 尝试 cn2an（中文数字）
        4. 尝试 roman_numbers（罗马数字）

    Examples:
        >>> index_int("123")
        123
        >>> index_int("一百二十三")
        123
        >>> index_int("one")
        1
        >>> index_int("IV")
        4
    """
    res = -1
    try:
        res = int(index_str)
    except ValueError:
        try:
            res = w2n.word_to_num(index_str)
        except ValueError:
            try:
                res = cn2an(index_str)
            except ValueError:
                try:
                    res = r.number(index_str)
                except ValueError:
                    return -1
    return res


def qbullets_category(sections):
    """
    识别问题编号的格式类型

    该函数用于分析文档中问题编号的格式，选择最匹配的模式。

    Args:
        sections: 文本段落列表

    Returns:
        tuple: (index, pattern)
            - index: 匹配到的模式索引（0-based）
            - pattern: 匹配到的正则表达式模式

    Processing Steps:
        1. 遍历所有问题编号模式
        2. 统计每个模式匹配到的段落数
        3. 返回匹配次数最多的模式

    Note:
        - 用于自动识别文档的问题编号格式
    """
    global QUESTION_PATTERN
    hits = [0] * len(QUESTION_PATTERN)
    for i, pro in enumerate(QUESTION_PATTERN):
        for sec in sections:
            if re.match(pro, sec) and not not_bullet(sec):
                hits[i] += 1
                break

    # 找到匹配次数最多的模式
    maximum = 0
    res = -1
    for i, h in enumerate(hits):
        if h <= maximum:
            continue
        res = i
        maximum = h
    return res, QUESTION_PATTERN[res]


# ========== 项目符号模式列表 ==========
# 用于识别文档中的项目符号和标题层级
# 支持中文、英文、Markdown 等多种格式
BULLET_PATTERN = [[
    r"第[零一二三四五六七八九十百0-9]+(分?编|部分)",  # 中文分编/部分
    r"第[零一二三四五六七八九十百0-9]+章",        # 中文章节
    r"第[零一二三四五六七八九十百0-9]+节",        # 中文节
    r"第[零一二三四五六七八九十百0-9]+条",        # 中文条
    r"[\(（][零一二三四五六七八九十百]+[\)）]",   # 中文括号编号
], [
    r"第[0-9]+章",                             # 阿拉伯数字章节
    r"第[0-9]+节",                             # 阿拉伯数字节
    r"[0-9]{,2}[\. 、]",                      # 1-2位数字 + 点
    r"[0-9]{,2}\.[0-9]{,2}[^a-zA-Z/%~-]",     # 二级编号（1.1）
    r"[0-9]{,2}\.[0-9]{,2}\.[0-9]{,2}",       # 三级编号（1.1.1）
    r"[0-9]{,2}\.[0-9]{,2}\.[0-9]{,2}\.[0-9]{,2}",  # 四级编号（1.1.1.1）
], [
    r"第[零一二三四五六七八九十百0-9]+章",        # 中文章节
    r"第[零一二三四五六七八九十百0-9]+节",        # 中文节
    r"[零一二三四五六七八九十百]+[ 、]",         # 中文数字 + 空格
    r"[\(（][零一二三四五六七八九十百]+[\)）]",  # 中文括号编号
    r"[\(（][0-9]{,2}[\)）]",                 # 阿拉伯数字括号编号
], [
    r"PART (ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)",  # 英文部分
    r"Chapter (I+V?|VI*|XI|IX|X)",           # 英文章节（罗马数字）
    r"Section [0-9]+",                        # 英文节
    r"Article [0-9]+"                         # 英文条
], [
    r"^#[^#]",                                # Markdown 一级标题
    r"^##[^#]",                               # Markdown 二级标题
    r"^###.*",                                # Markdown 三级标题
    r"^####.*",                               # Markdown 四级标题
    r"^#####.*",                              # Markdown 五级标题
    r"^######.*",                             # Markdown 六级标题
]
]


def random_choices(arr, k):
    """
    从数组中随机选择 k 个元素

    Args:
        arr: 输入数组
        k: 选择的元素数量

    Returns:
        list: 随机选择的元素列表

    Note:
        - k 不会超过数组长度
    """
    k = min(len(arr), k)
    return random.choices(arr, k=k)


def not_bullet(line):
    """
    判断文本行是否不是有效的项目符号

    该函数用于过滤掉一些看起来像编号但实际上不是编号的文本。

    Args:
        line: 待判断的文本行

    Returns:
        bool: 如果是无效的项目符号则返回 True

    Filter Patterns:
        - "0": 单独的 0
        - "[0-9]+ +[0-9~个只-]": 数字 + 空格 + 数字/符号（如 "123 456"）
        - "[0-9]+\.{2,}": 数字 + 两个或以上的点（如 "123..."）

    Note:
        - 用于 bullets_category 函数中过滤无效编号
    """
    patt = [
        r"0", r"[0-9]+ +[0-9~个只-]", r"[0-9]+\.{2,}"
    ]
    return any([re.match(r, line) for r in patt])


def bullets_category(sections):
    """
    识别项目符号的格式类型

    该函数用于分析文档中项目符号的格式，选择最匹配的模式。
    支持中文、英文、Markdown 等多种格式。

    Args:
        sections: 文本段落列表

    Returns:
        int: 匹配到的模式索引（0-based）

    Processing Steps:
        1. 遍历所有项目符号模式
        2. 统计每个模式匹配到的段落数
        3. 返回匹配次数最多的模式索引

    Note:
        - 用于自动识别文档的标题/列表格式
        - 支持 5 种预定义的格式模式
    """
    global BULLET_PATTERN
    hits = [0] * len(BULLET_PATTERN)
    for i, pro in enumerate(BULLET_PATTERN):
        for sec in sections:
            sec = sec.strip()
            for p in pro:
                if re.match(p, sec) and not not_bullet(sec):
                    hits[i] += 1
                    break

    # 找到匹配次数最多的模式
    maximum = 0
    res = -1
    for i, h in enumerate(hits):
        if h <= maximum:
            continue
        res = i
        maximum = h
    return res


def is_english(texts):
    """
    判断文本是否为英文

    该函数通过检测英文字符、数字和标点符号的比例来判断文本语言。

    Args:
        texts: 待判断的文本或文本列表

    Returns:
        bool: 如果 80% 以上的文本是英文字符则返回 True

    Detection Method:
        - 匹配英文字符、数字、空格和常见标点
        - 计算匹配的文本比例
        - 比例 > 0.8 则判定为英文

    Examples:
        >>> is_english("Hello, world!")
        True
        >>> is_english("你好，世界")
        False
    """
    if not texts:
        return False

    # 英文字符模式：字母、数字、空格、标点
    pattern = re.compile(r"[`a-zA-Z0-9\s.,':;/\"?<>!\(\)\-]")

    if isinstance(texts, str):
        texts = list(texts)
    elif isinstance(texts, list):
        texts = [t for t in texts if isinstance(t, str) and t.strip()]
    else:
        return False

    if not texts:
        return False

    # 计算英文文本的比例
    eng = sum(1 for t in texts if pattern.fullmatch(t.strip()))
    return (eng / len(texts)) > 0.8


def is_chinese(text):
    """
    判断文本是否包含中文

    该函数通过检测中文字符的比例来判断文本是否包含中文内容。

    Args:
        text: 待判断的文本

    Returns:
        bool: 如果中文字符占比 > 20% 则返回 True

    Detection Method:
        - 检测 Unicode 范围 \u4e00-\u9fff 中的中文字符
        - 计算中文字符占比
        - 占比 > 0.2 则判定为包含中文

    Examples:
        >>> is_chinese("你好世界")
        True
        >>> is_chinese("Hello world")
        False
    """
    if not text:
        return False
    chinese = 0
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            chinese += 1
    if chinese / len(text) > 0.2:
        return True
    return False


def tokenize(d, txt, eng):
    """
    为文本添加分词和权重信息

    该函数是 RAGFlow 分词的核心入口，为文本块添加多种分词字段。

    Args:
        d: 文档字典（会被原地修改）
        txt: 待分词的文本
        eng: 是否为英文（影响分词策略）

    Adds to d:
        - content_with_weight: 原始文本（带权重）
        - content_ltks: 长token分词（用于检索）
        - content_sm_ltks: 短token分词（用于高精度）

    Processing Steps:
        1. 保存原始文本到 content_with_weight
        2. 移除表格标签（<table>、<td> 等）
        3. 使用 rag_tokenizer 进行分词
        4. 生成细粒度分词

    Note:
        - 分词结果用于 Elasticsearch 向量检索
        - 英文和中文使用不同的分词策略
    """
    from . import rag_tokenizer
    d["content_with_weight"] = txt
    # 移除表格标签，避免影响分词
    t = re.sub(r"</?(table|td|caption|tr|th)( [^<>]{0,12})?>", " ", txt)
    # 长token分词（用于检索）
    d["content_ltks"] = rag_tokenizer.tokenize(t)
    # 短token分词（用于高精度匹配）
    d["content_sm_ltks"] = rag_tokenizer.fine_grained_tokenize(d["content_ltks"])


def split_with_pattern(d, pattern: str, content: str, eng) -> list:
    """
    使用正则表达式模式分割文本并分词

    该函数用于根据自定义分隔符将文本分割为多个块，并为每个块添加分词信息。

    Args:
        d: 文档字典模板
        pattern: 正则表达式分隔符模式
        content: 待分割的文本内容
        eng: 是否为英文

    Returns:
        list: 分割后的文档块列表，每个元素都包含分词信息

    Processing Steps:
        1. 验证并编译正则表达式模式
        2. 使用模式分割文本（保留分隔符）
        3. 为每个分割后的块添加分词信息
        4. 返回文档块列表

    Error Handling:
        - 如果正则表达式无效，记录警告并返回整个文本作为单个块

    Note:
        - 分隔符会被保留并附加到前面的文本块
        - 用于处理嵌套内容（如代码块、引用等）
    """
    docs = []

    # 验证并编译正则表达式模式
    try:
        compiled_pattern = re.compile(r"(%s)" % pattern, flags=re.DOTALL)
    except re.error as e:
        logging.warning(f"Invalid delimiter regex pattern '{pattern}': {e}. Falling back to no split.")
        # 失败时返回整个文本作为单个块
        dd = copy.deepcopy(d)
        tokenize(dd, content, eng)
        return [dd]

    # 分割文本（保留分隔符）
    txts = [txt for txt in compiled_pattern.split(content)]
    for j in range(0, len(txts), 2):
        txt = txts[j]
        if not txt:
            continue
        # 将分隔符附加到文本块
        if j + 1 < len(txts):
            txt += txts[j + 1]
        dd = copy.deepcopy(d)
        tokenize(dd, txt, eng)
        docs.append(dd)
    return docs


def tokenize_chunks(chunks, doc, eng, pdf_parser=None, child_delimiters_pattern=None):
    """
    为文本块列表添加分词信息

    该函数是 RAGFlow 文档处理的核心环节，将纯文本块转换为
    可用于 Elasticsearch 检索的文档格式。

    Args:
        chunks: 文本块列表
        doc: 文档元数据模板
        eng: 是否为英文
        pdf_parser: PDF 解析器（可选，用于提取位置和图片）
        child_delimiters_pattern: 子元素分隔符模式（可选）

    Returns:
        list: 分词后的文档块列表

    Document Structure:
        每个文档块包含：
        - content_with_weight: 原始文本
        - content_ltks: 长token分词
        - content_sm_ltks: 短token分词
        - page_num_int: 页码列表（如果有）
        - position_int: 位置列表（如果有）
        - top_int: 顶部位置列表（如果有）
        - mom_with_weight: 原始文本（使用子分隔符时）

    Processing Steps:
        1. 遍历所有文本块
        2. 如果提供了 pdf_parser，提取图片和位置信息
        3. 如果提供了子分隔符，使用 split_with_pattern 分割
        4. 否则直接使用 tokenize 添加分词信息
        5. 返回处理后的文档块列表

    Note:
        - 这是文档处理流程中的关键步骤
        - 分词结果直接影响检索质量
    """
    res = []
    # 将文本块包装为 Elasticsearch 文档
    for ii, ck in enumerate(chunks):
        if len(ck.strip()) == 0:
            continue
        logging.debug("-- {}".format(ck))
        d = copy.deepcopy(doc)

        # 如果提供了 PDF 解析器，提取图片和位置信息
        if pdf_parser:
            try:
                d["image"], poss = pdf_parser.crop(ck, need_position=True)
                add_positions(d, poss)
                ck = pdf_parser.remove_tag(ck)
            except NotImplementedError:
                pass
        else:
            # 添加默认位置信息
            add_positions(d, [[ii] * 5])

        # 如果提供了子分隔符，使用自定义分割
        if child_delimiters_pattern:
            d["mom_with_weight"] = ck
            res.extend(split_with_pattern(d, child_delimiters_pattern, ck, eng))
            continue

        # 标准分词流程
        tokenize(d, ck, eng)
        res.append(d)
    return res


def doc_tokenize_chunks_with_images(chunks, doc, eng, child_delimiters_pattern=None, batch_size=10):
    """
    为包含文本和图片的文档块添加分词信息（DOCX 专用）

    该函数专门用于处理 DOCX 文档的文本块，支持文本、图片、表格三种类型。

    Args:
        chunks: 文档块列表，每个元素为字典格式
        doc: 文档元数据模板
        eng: 是否为英文
        child_delimiters_pattern: 子元素分隔符模式（可选）
        batch_size: 表格批处理大小（默认 10）

    Returns:
        list: 分词后的文档块列表

    Chunk Types:
        - text: 纯文本块
        - image: 图片块（设置 doc_type_kwd = "image"）
        - table: 表格块（设置 doc_type_kwd = "table"）

    Processing Steps:
        1. 提取上下文信息（context_above + text + context_below）
        2. 根据块类型设置不同的处理方式
        3. 为文本块添加分词信息
        4. 为图片/表格块添加类型标记

    Note:
        - DOCX 文档专用函数
        - 支持表格和图片的上下文增强
    """
    res = []
    for ii, ck in enumerate(chunks):
        # 合并上下文信息
        text = ck.get("context_above", "") + ck.get("text") + ck.get("context_below", "")
        if len(text.strip()) == 0:
            continue
        logging.debug("-- {}".format(ck))
        d = copy.deepcopy(doc)

        # 处理图片
        if ck.get("image"):
            d["image"] = ck.get("image")
        add_positions(d, [[ii] * 5])

        # 根据块类型设置处理方式
        if ck.get("ck_type") == "text":
            if child_delimiters_pattern:
                d["mom_with_weight"] = text
                res.extend(split_with_pattern(d, child_delimiters_pattern, text, eng))
                continue
        elif ck.get("ck_type") == "image":
            d["doc_type_kwd"] = "image"
        elif ck.get("ck_type") == "table":
            d["doc_type_kwd"] = "table"

        tokenize(d, text, eng)
        res.append(d)
    return res


def tokenize_chunks_with_images(chunks, doc, eng, images, child_delimiters_pattern=None):
    """
    为带图片的文本块添加分词信息

    该函数用于处理包含图片的文本块，为每个块添加分词和图片信息。

    Args:
        chunks: 文本块列表
        doc: 文档元数据模板
        eng: 是否为英文
        images: 图片列表（与 chunks 一一对应）
        child_delimiters_pattern: 子元素分隔符模式（可选）

    Returns:
        list: 分词后的文档块列表，每个元素包含图片和分词信息

    Processing Steps:
        1. 遍历文本块和图片
        2. 为每个块添加图片信息
        3. 如果提供了子分隔符，使用自定义分割
        4. 否则直接添加分词信息

    Note:
        - 用于 Markdown、PDF 等包含图片的文档格式
        - 图片和文本块必须一一对应
    """
    res = []
    # 将文本块包装为 Elasticsearch 文档
    for ii, (ck, image) in enumerate(zip(chunks, images)):
        if len(ck.strip()) == 0:
            continue
        logging.debug("-- {}".format(ck))
        d = copy.deepcopy(doc)
        d["image"] = image
        add_positions(d, [[ii] * 5])

        if child_delimiters_pattern:
            d["mom_with_weight"] = ck
            res.extend(split_with_pattern(d, child_delimiters_pattern, ck, eng))
            continue

        tokenize(d, ck, eng)
        res.append(d)
    return res


def tokenize_table(tbls, doc, eng, batch_size=10):
    """
    为表格数据添加分词信息

    该函数用于处理文档中的表格，将表格行转换为可检索的文档块。

    Args:
        tbls: 表格列表，每个元素为 ((img, rows), poss)
        doc: 文档元数据模板
        eng: 是否为英文
        batch_size: 每个文档块包含的行数（默认 10）

    Returns:
        list: 分词后的表格文档块列表

    Table Format:
        - img: 表格图片（可选）
        - rows: 表格行列表（字符串或列表）
        - poss: 位置信息列表

    Processing Steps:
        1. 遍历所有表格
        2. 如果 rows 是字符串，直接处理
        3. 如果 rows 是列表，按 batch_size 分批处理
        4. 使用分隔符连接行（英文用 "; "，中文用 "； "）
        5. 添加表格类型标记和图片

    Note:
        - 表格会被标记为 doc_type_kwd = "table"
        - 如果没有 <tr> 标签，可能被标记为 "image"
    """
    res = []
    # 添加表格
    for (img, rows), poss in tbls:
        if not rows:
            continue

        # 处理字符串格式的表格
        if isinstance(rows, str):
            d = copy.deepcopy(doc)
            tokenize(d, rows, eng)
            d["content_with_weight"] = rows
            d["doc_type_kwd"] = "table"
            if img:
                d["image"] = img
                # 如果没有 <tr> 标签，可能是图片
                if d["content_with_weight"].find("<tr>") < 0:
                    d["doc_type_kwd"] = "image"
            if poss:
                add_positions(d, poss)
            res.append(d)
            continue

        # 处理列表格式的表格
        de = "; " if eng else "； "
        for i in range(0, len(rows), batch_size):
            d = copy.deepcopy(doc)
            r = de.join(rows[i:i + batch_size])
            tokenize(d, r, eng)
            d["doc_type_kwd"] = "table"
            if img:
                d["image"] = img
                if d["content_with_weight"].find("<tr>") < 0:
                    d["doc_type_kwd"] = "image"
            add_positions(d, poss)
            res.append(d)
    return res


def attach_media_context(chunks, table_context_size=0, image_context_size=0):
    """
    Attach surrounding text chunk content to media chunks (table/image).
    Best-effort ordering: if positional info exists on any chunk, use it to
    order chunks before collecting context; otherwise keep original order.
    """
    from . import rag_tokenizer

    if not chunks or (table_context_size <= 0 and image_context_size <= 0):
        return chunks

    def is_image_chunk(ck):
        if ck.get("doc_type_kwd") == "image":
            return True

        text_val = ck.get("content_with_weight") if isinstance(ck.get("content_with_weight"), str) else ck.get("text")
        has_text = isinstance(text_val, str) and text_val.strip()
        return bool(ck.get("image")) and not has_text

    def is_table_chunk(ck):
        return ck.get("doc_type_kwd") == "table"

    def is_text_chunk(ck):
        return not is_image_chunk(ck) and not is_table_chunk(ck)

    def get_text(ck):
        if isinstance(ck.get("content_with_weight"), str):
            return ck["content_with_weight"]
        if isinstance(ck.get("text"), str):
            return ck["text"]
        return ""

    def split_sentences(text):
        pattern = r"([.。！？!?；;：:\n])"
        parts = re.split(pattern, text)
        sentences = []
        buf = ""
        for p in parts:
            if not p:
                continue
            if re.fullmatch(pattern, p):
                buf += p
                sentences.append(buf)
                buf = ""
            else:
                buf += p
        if buf:
            sentences.append(buf)
        return sentences

    def get_bounds_by_page(ck):
        bounds = {}
        try:
            if ck.get("position_int"):
                for pos in ck["position_int"]:
                    if not pos or len(pos) < 5:
                        continue
                    pn, _, _, top, bottom = pos
                    if pn is None or top is None:
                        continue
                    top_val = float(top)
                    bottom_val = float(bottom) if bottom is not None else top_val
                    if bottom_val < top_val:
                        top_val, bottom_val = bottom_val, top_val
                    pn = int(pn)
                    if pn in bounds:
                        bounds[pn] = (min(bounds[pn][0], top_val), max(bounds[pn][1], bottom_val))
                    else:
                        bounds[pn] = (top_val, bottom_val)
            else:
                pn = None
                if ck.get("page_num_int"):
                    pn = ck["page_num_int"][0]
                elif ck.get("page_number") is not None:
                    pn = ck.get("page_number")
                if pn is None:
                    return bounds
                top = None
                if ck.get("top_int"):
                    top = ck["top_int"][0]
                elif ck.get("top") is not None:
                    top = ck.get("top")
                if top is None:
                    return bounds
                bottom = ck.get("bottom")
                pn = int(pn)
                top_val = float(top)
                bottom_val = float(bottom) if bottom is not None else top_val
                if bottom_val < top_val:
                    top_val, bottom_val = bottom_val, top_val
                bounds[pn] = (top_val, bottom_val)
        except Exception:
            return {}
        return bounds

    def trim_to_tokens(text, token_budget, from_tail=False):
        if token_budget <= 0 or not text:
            return ""
        sentences = split_sentences(text)
        if not sentences:
            return ""

        collected = []
        remaining = token_budget
        seq = reversed(sentences) if from_tail else sentences
        for s in seq:
            tks = num_tokens_from_string(s)
            if tks <= 0:
                continue
            if tks > remaining:
                collected.append(s)
                break
            collected.append(s)
            remaining -= tks

        if from_tail:
            collected = list(reversed(collected))
        return "".join(collected)

    def find_mid_sentence_index(sentences):
        if not sentences:
            return 0
        total = sum(max(0, num_tokens_from_string(s)) for s in sentences)
        if total <= 0:
            return max(0, len(sentences) // 2)
        target = total / 2.0
        best_idx = 0
        best_diff = None
        cum = 0
        for i, s in enumerate(sentences):
            cum += max(0, num_tokens_from_string(s))
            diff = abs(cum - target)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_idx = i
        return best_idx

    def collect_context_from_sentences(sentences, boundary_idx, token_budget):
        prev_ctx = []
        remaining_prev = token_budget
        for s in reversed(sentences[:boundary_idx + 1]):
            if remaining_prev <= 0:
                break
            tks = num_tokens_from_string(s)
            if tks <= 0:
                continue
            if tks > remaining_prev:
                s = trim_to_tokens(s, remaining_prev, from_tail=True)
                tks = num_tokens_from_string(s)
            prev_ctx.append(s)
            remaining_prev -= tks
        prev_ctx.reverse()

        next_ctx = []
        remaining_next = token_budget
        for s in sentences[boundary_idx + 1:]:
            if remaining_next <= 0:
                break
            tks = num_tokens_from_string(s)
            if tks <= 0:
                continue
            if tks > remaining_next:
                s = trim_to_tokens(s, remaining_next, from_tail=False)
                tks = num_tokens_from_string(s)
            next_ctx.append(s)
            remaining_next -= tks
        return prev_ctx, next_ctx

    def extract_position(ck):
        pn = None
        top = None
        left = None
        try:
            if ck.get("page_num_int"):
                pn = ck["page_num_int"][0]
            elif ck.get("page_number") is not None:
                pn = ck.get("page_number")

            if ck.get("top_int"):
                top = ck["top_int"][0]
            elif ck.get("top") is not None:
                top = ck.get("top")

            if ck.get("position_int"):
                left = ck["position_int"][0][1]
            elif ck.get("x0") is not None:
                left = ck.get("x0")
        except Exception:
            pn = top = left = None
        return pn, top, left

    indexed = list(enumerate(chunks))
    positioned_indices = []
    unpositioned_indices = []
    for idx, ck in indexed:
        pn, top, left = extract_position(ck)
        if pn is not None and top is not None:
            positioned_indices.append((idx, pn, top, left if left is not None else 0))
        else:
            unpositioned_indices.append(idx)

    if positioned_indices:
        positioned_indices.sort(key=lambda x: (int(x[1]), int(x[2]), int(x[3]), x[0]))
        ordered_indices = [i for i, _, _, _ in positioned_indices] + unpositioned_indices
    else:
        ordered_indices = [idx for idx, _ in indexed]

    text_bounds = []
    for idx, ck in indexed:
        if not is_text_chunk(ck):
            continue
        bounds = get_bounds_by_page(ck)
        if bounds:
            text_bounds.append((idx, bounds))

    for sorted_pos, idx in enumerate(ordered_indices):
        ck = chunks[idx]
        token_budget = image_context_size if is_image_chunk(ck) else table_context_size if is_table_chunk(ck) else 0
        if token_budget <= 0:
            continue

        prev_ctx = []
        next_ctx = []
        media_bounds = get_bounds_by_page(ck)
        best_idx = None
        best_dist = None
        candidate_count = 0
        if media_bounds and text_bounds:
            for text_idx, bounds in text_bounds:
                for pn, (t_top, t_bottom) in bounds.items():
                    if pn not in media_bounds:
                        continue
                    m_top, m_bottom = media_bounds[pn]
                    if m_bottom < t_top or m_top > t_bottom:
                        continue
                    candidate_count += 1
                    m_mid = (m_top + m_bottom) / 2.0
                    t_mid = (t_top + t_bottom) / 2.0
                    dist = abs(m_mid - t_mid)
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        best_idx = text_idx
        if best_idx is None and media_bounds:
            media_page = min(media_bounds.keys())
            page_order = []
            for ordered_idx in ordered_indices:
                pn, _, _ = extract_position(chunks[ordered_idx])
                if pn == media_page:
                    page_order.append(ordered_idx)
            if page_order and idx in page_order:
                pos_in_page = page_order.index(idx)
                if pos_in_page == 0:
                    for neighbor in page_order[pos_in_page + 1:]:
                        if is_text_chunk(chunks[neighbor]):
                            best_idx = neighbor
                            break
                elif pos_in_page == len(page_order) - 1:
                    for neighbor in reversed(page_order[:pos_in_page]):
                        if is_text_chunk(chunks[neighbor]):
                            best_idx = neighbor
                            break
        if best_idx is not None:
            base_text = get_text(chunks[best_idx])
            sentences = split_sentences(base_text)
            if sentences:
                boundary_idx = find_mid_sentence_index(sentences)
                prev_ctx, next_ctx = collect_context_from_sentences(sentences, boundary_idx, token_budget)

        if not prev_ctx and not next_ctx:
            continue

        self_text = get_text(ck)
        pieces = [*prev_ctx]
        if self_text:
            pieces.append(self_text)
        pieces.extend(next_ctx)
        combined = "\n".join(pieces)

        original = ck.get("content_with_weight")
        if "content_with_weight" in ck:
            ck["content_with_weight"] = combined
        elif "text" in ck:
            original = ck.get("text")
            ck["text"] = combined

        if combined != original:
            if "content_ltks" in ck:
                ck["content_ltks"] = rag_tokenizer.tokenize(combined)
            if "content_sm_ltks" in ck:
                ck["content_sm_ltks"] = rag_tokenizer.fine_grained_tokenize(
                    ck.get("content_ltks", rag_tokenizer.tokenize(combined)))

    if positioned_indices:
        chunks[:] = [chunks[i] for i in ordered_indices]

    return chunks


def append_context2table_image4pdf(sections: list, tabls: list, table_context_size=0, return_context=False):
    from deepdoc.parser import PdfParser
    if table_context_size <=0:
        return [] if return_context else tabls

    page_bucket = defaultdict(list)
    for i, item in enumerate(sections):
        if isinstance(item, (tuple, list)):
            if len(item) > 2:
                txt, _sec_id, poss = item[0], item[1], item[2]
            else:
                txt = item[0] if item else ""
                poss = item[1] if len(item) > 1 else ""
        else:
            txt = item
            poss = ""
        # Normal: (text, "@@...##") from naive parser -> poss is a position tag string.
        # Manual: (text, sec_id, poss_list) -> poss is a list of (page, left, right, top, bottom).
        # Paper: (text_with_@@tag, layoutno) -> poss is layoutno; parse from txt when it contains @@ tags.
        if isinstance(poss, list):
            poss = poss
        elif isinstance(poss, str):
            if "@@" not in poss and isinstance(txt, str) and "@@" in txt:
                poss = txt
            poss = PdfParser.extract_positions(poss)
        else:
            if isinstance(txt, str) and "@@" in txt:
                poss = PdfParser.extract_positions(txt)
            else:
                poss = []
        if isinstance(txt, str) and "@@" in txt:
            txt = re.sub(r"@@[0-9-]+\t[0-9.\t]+##", "", txt).strip()
        for page, left, right, top, bottom in poss:
            if isinstance(page, list):
                page = page[0] if page else 0
            page_bucket[page].append(((left, right, top, bottom), txt))

    def upper_context(page, i):
        txt = ""
        if page not in page_bucket:
            i = -1
        while num_tokens_from_string(txt) < table_context_size:
            if i < 0:
                page -= 1
                if page < 0 or page not in page_bucket:
                    break
                i = len(page_bucket[page]) -1
            blks = page_bucket[page]
            (_, _, _, _), cnt = blks[i]
            txts = re.split(r"([。!?？；！\n]|\. )", cnt, flags=re.DOTALL)[::-1]
            for j in range(0, len(txts), 2):
                txt = (txts[j+1] if j+1<len(txts) else "") + txts[j] + txt
                if num_tokens_from_string(txt) > table_context_size:
                    break
            i -= 1
        return txt

    def lower_context(page, i):
        txt = ""
        if page not in page_bucket:
            return txt
        while num_tokens_from_string(txt) < table_context_size:
            if i >= len(page_bucket[page]):
                page += 1
                if page not in page_bucket:
                    break
                i = 0
            blks = page_bucket[page]
            (_, _, _, _), cnt = blks[i]
            txts = re.split(r"([。!?？；！\n]|\. )", cnt, flags=re.DOTALL)
            for j in range(0, len(txts), 2):
                txt += txts[j] + (txts[j+1] if j+1<len(txts) else "")
                if num_tokens_from_string(txt) > table_context_size:
                    break
            i += 1
        return txt

    res = []
    contexts = []
    for (img, tb), poss in tabls:
        page, left, right, top, bott = poss[0]
        _page, _left, _right, _top, _bott = poss[-1]
        if isinstance(tb, list):
            tb = "\n".join(tb)

        i = 0
        blks = page_bucket.get(page, [])
        _tb = tb
        while i < len(blks):
            if i + 1 >= len(blks):
                if _page > page:
                    page += 1
                    i = 0
                    blks = page_bucket.get(page, [])
                    continue
                upper = upper_context(page, i)
                lower = lower_context(page + 1, 0)
                tb = upper + tb + lower
                contexts.append((upper.strip(), lower.strip()))
                break
            (_, _, t, b), txt = blks[i]
            if b > top:
                break
            (_, _, _t, _b), _txt = blks[i+1]
            if _t < _bott:
                i += 1
                continue

            upper = upper_context(page, i)
            lower = lower_context(page, i)
            tb = upper + tb + lower
            contexts.append((upper.strip(), lower.strip()))
            break

        if _tb == tb:
            upper = upper_context(page, -1)
            lower = lower_context(page + 1, 0)
            tb = upper + tb + lower
            contexts.append((upper.strip(), lower.strip()))
        if len(contexts) < len(res) + 1:
            contexts.append(("", ""))
        res.append(((img, tb), poss))
    return contexts if return_context else res


def add_positions(d, poss):
    if not poss:
        return
    page_num_int = []
    position_int = []
    top_int = []
    for pn, left, right, top, bottom in poss:
        page_num_int.append(int(pn + 1))
        top_int.append(int(top))
        position_int.append((int(pn + 1), int(left), int(right), int(top), int(bottom)))
    d["page_num_int"] = page_num_int
    d["position_int"] = position_int
    d["top_int"] = top_int


def remove_contents_table(sections, eng=False):
    i = 0
    while i < len(sections):
        def get(i):
            nonlocal sections
            return (sections[i] if isinstance(sections[i],
                                              type("")) else sections[i][0]).strip()

        if not re.match(r"(contents|目录|目次|table of contents|致谢|acknowledge)$",
                        re.sub(r"( | |\u3000)+", "", get(i).split("@@")[0], flags=re.IGNORECASE)):
            i += 1
            continue
        sections.pop(i)
        if i >= len(sections):
            break
        prefix = get(i)[:3] if not eng else " ".join(get(i).split()[:2])
        while not prefix:
            sections.pop(i)
            if i >= len(sections):
                break
            prefix = get(i)[:3] if not eng else " ".join(get(i).split()[:2])
        sections.pop(i)
        if i >= len(sections) or not prefix:
            break
        for j in range(i, min(i + 128, len(sections))):
            if not re.match(prefix, get(j)):
                continue
            for _ in range(i, j):
                sections.pop(i)
            break


def make_colon_as_title(sections):
    if not sections:
        return []
    if isinstance(sections[0], type("")):
        return sections
    i = 0
    while i < len(sections):
        txt, layout = sections[i]
        i += 1
        txt = txt.split("@")[0].strip()
        if not txt:
            continue
        if txt[-1] not in ":：":
            continue
        txt = txt[::-1]
        arr = re.split(r"([。？！!?;；]| \.)", txt)
        if len(arr) < 2 or len(arr[1]) < 32:
            continue
        sections.insert(i - 1, (arr[0][::-1], "title"))
        i += 1


def title_frequency(bull, sections):
    bullets_size = len(BULLET_PATTERN[bull])
    levels = [bullets_size + 1 for _ in range(len(sections))]
    if not sections or bull < 0:
        return bullets_size + 1, levels

    for i, (txt, layout) in enumerate(sections):
        for j, p in enumerate(BULLET_PATTERN[bull]):
            if re.match(p, txt.strip()) and not not_bullet(txt):
                levels[i] = j
                break
        else:
            if re.search(r"(title|head)", layout) and not not_title(txt.split("@")[0]):
                levels[i] = bullets_size
    most_level = bullets_size + 1
    for level, c in sorted(Counter(levels).items(), key=lambda x: x[1] * -1):
        if level <= bullets_size:
            most_level = level
            break
    return most_level, levels


def not_title(txt):
    if re.match(r"第[零一二三四五六七八九十百0-9]+条", txt):
        return False
    if len(txt.split()) > 12 or (txt.find(" ") < 0 and len(txt) >= 32):
        return True
    return re.search(r"[,;，。；！!]", txt)


def tree_merge(bull, sections, depth):
    if not sections or bull < 0:
        return sections
    if isinstance(sections[0], type("")):
        sections = [(s, "") for s in sections]

    # filter out position information in pdf sections
    sections = [(t, o) for t, o in sections if
                t and len(t.split("@")[0].strip()) > 1 and not re.match(r"[0-9]+$", t.split("@")[0].strip())]

    def get_level(bull, section):
        text, layout = section
        text = re.sub(r"\u3000", " ", text).strip()

        for i, title in enumerate(BULLET_PATTERN[bull]):
            if re.match(title, text.strip()):
                return i + 1, text
        else:
            if re.search(r"(title|head)", layout) and not not_title(text):
                return len(BULLET_PATTERN[bull]) + 1, text
            else:
                return len(BULLET_PATTERN[bull]) + 2, text

    level_set = set()
    lines = []
    for section in sections:
        level, text = get_level(bull, section)
        if not text.strip("\n"):
            continue

        lines.append((level, text))
        level_set.add(level)

    sorted_levels = sorted(list(level_set))

    if depth <= len(sorted_levels):
        target_level = sorted_levels[depth - 1]
    else:
        target_level = sorted_levels[-1]

    if target_level == len(BULLET_PATTERN[bull]) + 2:
        target_level = sorted_levels[-2] if len(sorted_levels) > 1 else sorted_levels[0]

    root = Node(level=0, depth=target_level, texts=[])
    root.build_tree(lines)

    return [element for element in root.get_tree() if element]


def hierarchical_merge(bull, sections, depth):
    if not sections or bull < 0:
        return []
    if isinstance(sections[0], type("")):
        sections = [(s, "") for s in sections]
    sections = [(t, o) for t, o in sections if
                t and len(t.split("@")[0].strip()) > 1 and not re.match(r"[0-9]+$", t.split("@")[0].strip())]
    bullets_size = len(BULLET_PATTERN[bull])
    levels = [[] for _ in range(bullets_size + 2)]

    for i, (txt, layout) in enumerate(sections):
        for j, p in enumerate(BULLET_PATTERN[bull]):
            if re.match(p, txt.strip()):
                levels[j].append(i)
                break
        else:
            if re.search(r"(title|head)", layout) and not not_title(txt):
                levels[bullets_size].append(i)
            else:
                levels[bullets_size + 1].append(i)
    sections = [t for t, _ in sections]

    # for s in sections: print("--", s)

    def binary_search(arr, target):
        if not arr:
            return -1
        if target > arr[-1]:
            return len(arr) - 1
        if target < arr[0]:
            return -1
        s, e = 0, len(arr)
        while e - s > 1:
            i = (e + s) // 2
            if target > arr[i]:
                s = i
                continue
            elif target < arr[i]:
                e = i
                continue
            else:
                assert False
        return s

    cks = []
    readed = [False] * len(sections)
    levels = levels[::-1]
    for i, arr in enumerate(levels[:depth]):
        for j in arr:
            if readed[j]:
                continue
            readed[j] = True
            cks.append([j])
            if i + 1 == len(levels) - 1:
                continue
            for ii in range(i + 1, len(levels)):
                jj = binary_search(levels[ii], j)
                if jj < 0:
                    continue
                if levels[ii][jj] > cks[-1][-1]:
                    cks[-1].pop(-1)
                cks[-1].append(levels[ii][jj])
            for ii in cks[-1]:
                readed[ii] = True

    if not cks:
        return cks

    for i in range(len(cks)):
        cks[i] = [sections[j] for j in cks[i][::-1]]
        logging.debug("\n* ".join(cks[i]))

    res = [[]]
    num = [0]
    for ck in cks:
        if len(ck) == 1:
            n = num_tokens_from_string(re.sub(r"@@[0-9]+.*", "", ck[0]))
            if n + num[-1] < 218:
                res[-1].append(ck[0])
                num[-1] += n
                continue
            res.append(ck)
            num.append(n)
            continue
        res.append(ck)
        num.append(218)

    return res


def naive_merge(sections: str | list, chunk_token_num=128, delimiter="\n。；！？", overlapped_percent=0):
    """
    朴素文本分块算法（核心函数）

    该函数是 RAGFlow 文本分块的核心算法，将长文本按分隔符切分并合并为
    不超过 token 限制的文本块。

    Args:
        sections: 输入文本，可以是字符串或字符串列表
        chunk_token_num: 单个文本块的最大 token 数（默认 128）
        delimiter: 分隔符字符串，用于切分文本（默认 "\n。；！？"）
        overlapped_percent: 相邻块之间的重叠百分比（默认 0）

    Returns:
        list: 分块后的文本列表

    Processing Steps:
        1. 规范化输入为列表格式 [(text, position), ...]
        2. 处理自定义分隔符（反引号包裹的格式）
        3. 按分隔符切分文本
        4. 逐段合并，直到达到 token 限制
        5. 处理重叠百分比

    Custom Delimiters:
        - 支持使用反引号包裹自定义分隔符：`##`, `---` 等
        - 自定义分隔符会被优先处理
        - 按长度降序排序，避免短分隔符误匹配

    Overlap Handling:
        - overlapped_percent > 0 时，相邻块会有重叠内容
        - 重叠内容取自上一个块的末尾部分
        - 用于保留上下文，提高检索质量

    Examples:
        >>> naive_merge("第一段。第二段。第三段", chunk_token_num=10)
        ['第一段。', '第二段。', '第三段']

        >>> naive_merge("第一段。第二段", chunk_token_num=128, overlapped_percent=20)
        ['第一段。第二段', '第一段。第二段']  # 有重叠

    Note:
        - 这是 RAGFlow 最基础的分块算法
        - 被 naive.py 中的 chunk() 函数调用
        - 不支持图片和表格（使用 naive_merge_with_images 或 naive_merge_docx）
    """
    from deepdoc.parser.pdf_parser import RAGFlowPdfParser
    if not sections:
        return []
    if isinstance(sections, str):
        sections = [sections]
    if isinstance(sections[0], str):
        sections = [(s, "") for s in sections]
    cks = [""]
    tk_nums = [0]

    def add_chunk(t, pos):
        """
        添加文本块到结果列表

        该函数处理分块的核心逻辑：
        - 检查是否需要创建新块
        - 处理重叠百分比
        - 追加文本到当前块

        Args:
            t: 待添加的文本
            pos: 位置信息
        """
        nonlocal cks, tk_nums, delimiter
        tnum = num_tokens_from_string(t)
        if not pos:
            pos = ""
        if tnum < 8:
            pos = ""

        # 检查是否需要创建新块
        # Ensure that the length of the merged chunk does not exceed chunk_token_num
        if cks[-1] == "" or tk_nums[-1] > chunk_token_num * (100 - overlapped_percent) / 100.:
            if cks:
                # 处理重叠：从上一个块的末尾提取重叠内容
                overlapped = RAGFlowPdfParser.remove_tag(cks[-1])
                t = overlapped[int(len(overlapped) * (100 - overlapped_percent) / 100.):] + t
            if t.find(pos) < 0:
                t += pos
            cks.append(t)
            tk_nums.append(tnum)
        else:
            # 追加到当前块
            if cks[-1].find(pos) < 0:
                t += pos
            cks[-1] += t
            tk_nums[-1] += tnum

    # 处理自定义分隔符（反引号包裹的格式）
    custom_delimiters = [m.group(1) for m in re.finditer(r"`([^`]+)`", delimiter)]
    has_custom = bool(custom_delimiters)

    if has_custom:
        # 按长度降序排序，避免短分隔符误匹配
        custom_pattern = "|".join(re.escape(t) for t in sorted(set(custom_delimiters), key=len, reverse=True))
        cks, tk_nums = [], []
        for sec, pos in sections:
            # 按自定义分隔符切分
            split_sec = re.split(r"(%s)" % custom_pattern, sec, flags=re.DOTALL)
            for sub_sec in split_sec:
                # 跳过分隔符本身
                if re.fullmatch(custom_pattern, sub_sec or ""):
                    continue
                text = "\n" + sub_sec
                local_pos = pos
                if num_tokens_from_string(text) < 8:
                    local_pos = ""
                if local_pos and text.find(local_pos) < 0:
                    text += local_pos
                cks.append(text)
                tk_nums.append(num_tokens_from_string(text))
        return cks

    # 标准处理流程
    for sec, pos in sections:
        add_chunk("\n" + sec, pos)

    return cks


def naive_merge_with_images(texts, images, chunk_token_num=128, delimiter="\n。；！？", overlapped_percent=0):
    """
    带图片的文本分块算法

    该函数是 naive_merge 的扩展版本，支持处理包含图片的文本。
    图片会随文本一起合并，同一块中的多张图片会被拼接。

    Args:
        texts: 文本列表或元组列表 [(text, position), ...]
        images: 图片列表（与 texts 一一对应）
        chunk_token_num: 单个文本块的最大 token 数（默认 128）
        delimiter: 分隔符字符串（默认 "\n。；！？"）
        overlapped_percent: 相邻块之间的重叠百分比（默认 0）

    Returns:
        tuple: (chunks, result_images)
            - chunks: 分块后的文本列表
            - result_images: 分块后的图片列表（与 chunks 一一对应）

    Image Handling:
        - None 图片会被保留（不拼接）
        - 同一块中的多张图片会被垂直拼接
        - 图片使用 concat_img 函数拼接

    Processing Steps:
        1. 验证输入（texts 和 images 长度必须相同）
        2. 处理自定义分隔符
        3. 逐段合并文本和图片
        4. 处理重叠百分比
        5. 拼接同一块中的多张图片

    Examples:
        >>> texts = ["第一段", "第二段", "第三段"]
        >>> images = [img1, img2, img3]
        >>> naive_merge_with_images(texts, images, chunk_token_num=200)
        (["第一段\n第二段", "第三段"], [merged_img1, img3])

    Note:
        - 用于 Markdown、PDF 等包含图片的文档
        - 图片拼接使用 PIL 库
    """
    from deepdoc.parser.pdf_parser import RAGFlowPdfParser
    if not texts or len(texts) != len(images):
        return [], []
    cks = [""]
    result_images = [None]
    tk_nums = [0]

    def add_chunk(t, image, pos=""):
        """
        添加文本和图片到结果列表

        Args:
            t: 待添加的文本
            image: 待添加的图片
            pos: 位置信息
        """
        nonlocal cks, result_images, tk_nums, delimiter
        tnum = num_tokens_from_string(t)
        if not pos:
            pos = ""
        if tnum < 8:
            pos = ""

        # 检查是否需要创建新块
        # Ensure that the length of the merged chunk does not exceed chunk_token_num
        if cks[-1] == "" or tk_nums[-1] > chunk_token_num * (100 - overlapped_percent) / 100.:
            if cks:
                # 处理重叠：从上一个块的末尾提取重叠内容
                overlapped = RAGFlowPdfParser.remove_tag(cks[-1])
                t = overlapped[int(len(overlapped) * (100 - overlapped_percent) / 100.):] + t
            if t.find(pos) < 0:
                t += pos
            cks.append(t)
            result_images.append(image)
            tk_nums.append(tnum)
        else:
            # 追加到当前块
            if cks[-1].find(pos) < 0:
                t += pos
            cks[-1] += t
            # 拼接图片
            if result_images[-1] is None:
                result_images[-1] = image
            else:
                result_images[-1] = concat_img(result_images[-1], image)
            tk_nums[-1] += tnum

    # 处理自定义分隔符（反引号包裹的格式）
    custom_delimiters = [m.group(1) for m in re.finditer(r"`([^`]+)`", delimiter)]
    has_custom = bool(custom_delimiters)

    if has_custom:
        # 按长度降序排序，避免短分隔符误匹配
        custom_pattern = "|".join(re.escape(t) for t in sorted(set(custom_delimiters), key=len, reverse=True))
        cks, result_images, tk_nums = [], [], []
        for text, image in zip(texts, images):
            # 解包文本（可能是元组）
            text_str = text[0] if isinstance(text, tuple) else text
            if text_str is None:
                text_str = ""
            text_pos = text[1] if isinstance(text, tuple) and len(text) > 1 else ""
            # 按自定义分隔符切分
            split_sec = re.split(r"(%s)" % custom_pattern, text_str)
            for sub_sec in split_sec:
                # 跳过分隔符本身
                if re.fullmatch(custom_pattern, sub_sec or ""):
                    continue
                text_seg = "\n" + sub_sec
                local_pos = text_pos
                if num_tokens_from_string(text_seg) < 8:
                    local_pos = ""
                if local_pos and text_seg.find(local_pos) < 0:
                    text_seg += local_pos
                cks.append(text_seg)
                result_images.append(image)
                tk_nums.append(num_tokens_from_string(text_seg))
        return cks, result_images

    # 标准处理流程
    for text, image in zip(texts, images):
        # 解包文本（可能是元组）
        if isinstance(text, tuple):
            text_str = text[0] if text[0] is not None else ""
            text_pos = text[1] if len(text) > 1 else ""
            add_chunk("\n" + text_str, image, text_pos)
        else:
            add_chunk("\n" + (text or ""), image)

    return cks, result_images


def docx_question_level(p, bull=-1):
    txt = re.sub(r"\u3000", " ", p.text).strip()
    if hasattr(p.style, 'name') and p.style.name and p.style.name.startswith('Heading'):
        return int(p.style.name.split(' ')[-1]), txt
    else:
        if bull < 0:
            return 0, txt
        for j, title in enumerate(BULLET_PATTERN[bull]):
            if re.match(title, txt):
                return j + 1, txt
    return len(BULLET_PATTERN[bull]) + 1, txt


def concat_img(img1, img2):
    """
    垂直拼接两张图片

    该函数将两张图片垂直拼接成一张新图片，用于合并同一文本块中的多张图片。
    支持 PIL Image 对象和 LazyImage 对象。

    Args:
        img1: 第一张图片（PIL Image 或 LazyImage 或 None）
        img2: 第二张图片（PIL Image 或 LazyImage 或 None）

    Returns:
        Image: 拼接后的图片，如果两张都为 None 则返回 None

    Processing Steps:
        1. 处理 LazyImage 对象（使用 LazyImage.merge）
        2. 确保 PIL Image 格式
        3. 处理特殊情况（一张为 None、同一图片等）
        4. 计算新图片尺寸（宽度取最大值，高度相加）
        5. 创建新图片并粘贴两张图片

    Edge Cases:
        - 两张都为 None：返回 None
        - 一张为 None：返回另一张
        - 同一张图片：返回原图片
        - 像素数据相同：返回第一张

    Note:
        - 用于 naive_merge_with_images 和 naive_merge_docx
        - 拼接顺序：img1 在上，img2 在下
        - 新图片宽度 = max(img1.width, img2.width)
        - 新图片高度 = img1.height + img2.height
    """
    from rag.utils.lazy_image import ensure_pil_image, LazyImage

    # 处理 LazyImage 对象
    if (img1 is None or isinstance(img1, LazyImage)) and \
       (img2 is None or isinstance(img2, LazyImage)):
        if img1 and not img2:
            return img1
        if not img1 and img2:
            return img2
        if not img1 and not img2:
            return None
        return LazyImage.merge(img1, img2)

    # 确保 PIL Image 格式
    img1 = ensure_pil_image(img1) or img1
    img2 = ensure_pil_image(img2) or img2

    # 处理特殊情况
    if img1 and not img2:
        return img1
    if not img1 and img2:
        return img2
    if not img1 and not img2:
        return None

    # 检查是否为同一图片
    if img1 is img2:
        return img1

    # 检查像素数据是否相同
    if isinstance(img1, Image.Image) and isinstance(img2, Image.Image):
        pixel_data1 = img1.tobytes()
        pixel_data2 = img2.tobytes()
        if pixel_data1 == pixel_data2:
            return img1

    # 计算新图片尺寸
    width1, height1 = img1.size
    width2, height2 = img2.size

    new_width = max(width1, width2)
    new_height = height1 + height2
    new_image = Image.new('RGB', (new_width, new_height))

    # 粘贴两张图片
    new_image.paste(img1, (0, 0))
    new_image.paste(img2, (0, height1))
    return new_image

def _build_cks(sections, delimiter):
    """
    构建 DOCX 文档的分块结构

    该函数用于处理 DOCX 文档的 sections（包含文本、图片、表格），
    将其转换为统一的分块结构，并识别自定义分隔符。

    Args:
        sections: 文档段落列表，每个元素为 (text, image, table) 元组
        delimiter: 分隔符字符串（可能包含反引号包裹的自定义分隔符）

    Returns:
        tuple: (cks, tables, images, has_custom)
            - cks: 分块列表，每个元素为字典格式
            - tables: 表格块的索引列表
            - images: 图片块的索引列表
            - has_custom: 是否包含自定义分隔符

    Chunk Dictionary Format:
        {
            "text": "文本内容",
            "image": PIL Image 或 None,
            "ck_type": "text" | "image" | "table",
            "tk_nums": token 数量
        }

    Processing Steps:
        1. 提取自定义分隔符（反引号包裹的格式）
        2. 构建正则表达式模式
        3. 遍历所有 sections：
           - 表格：创建 table 类型的块
           - 图片：创建 image 类型的块
           - 文本：按分隔符切分，创建 text 类型的块
        4. 返回分块列表和索引

    Custom Delimiters:
        - 支持使用反引号包裹自定义分隔符：`##`, `---` 等
        - 自定义分隔符会被保留并用于切分文本
        - 按长度降序排序，避免短分隔符误匹配

    Note:
        - DOCX 文档专用函数
        - 被 naive_merge_docx 调用
    """
    cks = []
    tables = []
    images = []

    # 提取自定义分隔符（反引号包裹的格式）
    custom_delimiters = [m.group(1) for m in re.finditer(r"`([^`]+)`", delimiter)]
    has_custom = bool(custom_delimiters)

    if has_custom:
        # 转义分隔符并构建交替模式，最长的在前
        custom_pattern = "|".join(
            re.escape(t) for t in sorted(set(custom_delimiters), key=len, reverse=True)
        )
        # 捕获分隔符，使其在 re.split 结果中出现
        pattern = r"(%s)" % custom_pattern

    seg = ""
    for text, image, table in sections:
        # 规范化文本：确保是字符串并添加换行符以保持连续性
        if not text:
            text = ""
        else:
            text = "\n" + str(text)

        if table:
            # 表格块
            ck_text = text + str(table)
            idx = len(cks)
            cks.append({
                "text": ck_text,
                "image": image,
                "ck_type": "table",
                "tk_nums": num_tokens_from_string(ck_text),
            })
            tables.append(idx)
            continue

        if image:
            # 图片块（文本保持原样作为上下文）
            idx = len(cks)
            cks.append({
                "text": text,
                "image": image,
                "ck_type": "image",
                "tk_nums": num_tokens_from_string(text),
            })
            images.append(idx)
            continue

        # 纯文本块（一个或多个）
        if has_custom:
            split_sec = re.split(pattern, text)
            for sub_sec in split_sec:
                # ① 空白或仅空白的段 → 刷新当前缓冲区
                if not sub_sec or not sub_sec.strip():
                    if seg and seg.strip():
                        s = seg.strip()
                        cks.append({
                            "text": s,
                            "image": None,
                            "ck_type": "text",
                            "tk_nums": num_tokens_from_string(s),
                        })
                    seg = ""
                    continue

                # ② 匹配的自定义分隔符（允许周围空白）
                if re.fullmatch(custom_pattern, sub_sec.strip()):
                    if seg and seg.strip():
                        s = seg.strip()
                        cks.append({
                            "text": s,
                            "image": None,
                            "ck_type": "text",
                            "tk_nums": num_tokens_from_string(s),
                        })
                    seg = ""
                    continue

                # ③ 正常文本内容 → 累积
                seg += sub_sec
        else:
            if text and text.strip():
                t = text.strip()
                cks.append({
                    "text": t,
                    "image": None,
                    "ck_type": "text",
                    "tk_nums": num_tokens_from_string(t),
                })

    # 循环后的最终刷新（仅在使用自定义分隔符时）
    if has_custom and seg and seg.strip():
        s = seg.strip()
        cks.append({
            "text": s,
            "image": None,
            "ck_type": "text",
            "tk_nums": num_tokens_from_string(s),
        })

    return cks, tables, images, has_custom


def _add_context(cks, idx, context_size):
    """
    为表格或图片块添加上下文信息

    该函数为表格或图片块添加周围文本作为上下文，提高检索质量。

    Args:
        cks: 分块列表（会被原地修改）
        idx: 目标块的索引
        context_size: 上下文大小（token 数）

    Adds to cks[idx]:
        - context_above: 上方文本上下文
        - context_below: 下方文本上下文

    Processing Steps:
        1. 检查目标块是否为图片或表格
        2. 向上查找文本块，累积上方上下文
        3. 向下查找文本块，累积下方上下文
        4. 按句子切分，避免截断语义

    Sentence Splitting:
        - 使用正则表达式按句子切分：`([。!?？；！\n]|\. )`
        - 保留标点符号
        - 避免在句子中间截断

    Note:
        - 仅对 image 和 table 类型的块生效
        - 文本块按句子单位添加，保持语义完整
    """
    if cks[idx]["ck_type"] not in ("image", "table"):
        return

    prev = idx - 1
    after = idx + 1
    remain_above = context_size
    remain_below = context_size

    cks[idx]["context_above"] = ""
    cks[idx]["context_below"] = ""

    # 句子切分正则表达式
    split_pat = r"([。!?？；！\n]|\. )"

    picked_above = []
    picked_below = []

    def take_sentences_from_end(cnt, need_tokens):
        """
        从文本末尾提取句子

        Args:
            cnt: 文本内容
            need_tokens: 需要的 token 数量

        Returns:
            str: 从末尾提取的句子
        """
        txts = re.split(split_pat, cnt, flags=re.DOTALL)
        sents = []
        for j in range(0, len(txts), 2):
            sents.append(txts[j] + (txts[j + 1] if j + 1 < len(txts) else ""))
        acc = ""
        for s in reversed(sents):
            acc = s + acc
            if num_tokens_from_string(acc) >= need_tokens:
                break
        return acc

    def take_sentences_from_start(cnt, need_tokens):
        """
        从文本开头提取句子

        Args:
            cnt: 文本内容
            need_tokens: 需要的 token 数量

        Returns:
            str: 从开头提取的句子
        """
        txts = re.split(split_pat, cnt, flags=re.DOTALL)
        acc = ""
        for j in range(0, len(txts), 2):
            acc += txts[j] + (txts[j + 1] if j + 1 < len(txts) else "")
            if num_tokens_from_string(acc) >= need_tokens:
                break
        return acc

    # 向上查找文本块
    parts_above = []
    while prev >= 0 and remain_above > 0:
        if cks[prev]["ck_type"] == "text":
            tk = cks[prev]["tk_nums"]
            if tk >= remain_above:
                piece = take_sentences_from_end(cks[prev]["text"], remain_above)
                parts_above.insert(0, piece)
                picked_above.append((prev, "tail", remain_above, tk, piece[:80]))
                remain_above = 0
                break
            else:
                parts_above.insert(0, cks[prev]["text"])
                picked_above.append((prev, "full", remain_above, tk, (cks[prev]["text"] or "")[:80]))
                remain_above -= tk
        prev -= 1

    # 向下查找文本块
    parts_below = []
    while after < len(cks) and remain_below > 0:
        if cks[after]["ck_type"] == "text":
            tk = cks[after]["tk_nums"]
            if tk >= remain_below:
                piece = take_sentences_from_start(cks[after]["text"], remain_below)
                parts_below.append(piece)
                picked_below.append((after, "head", remain_below, tk, piece[:80]))
                remain_below = 0
                break
            else:
                parts_below.append(cks[after]["text"])
                picked_below.append((after, "full", remain_below, tk, (cks[after]["text"] or "")[:80]))
                remain_below -= tk
        after += 1

    cks[idx]["context_above"] = "".join(parts_above) if parts_above else ""
    cks[idx]["context_below"] = "".join(parts_below) if parts_below else ""


def _merge_cks(cks, chunk_token_num, has_custom):
    """
    合并文本块以满足 token 限制

    该函数将多个小的文本块合并为不超过 token 限制的大块，
    用于 DOCX 文档的分块处理。

    Args:
        cks: 分块列表（会被原地修改）
        chunk_token_num: 单个块的最大 token 数
        has_custom: 是否使用了自定义分隔符

    Returns:
        tuple: (merged, image_idxs)
            - merged: 合并后的分块列表
            - image_idxs: 包含图片的块的索引列表

    Processing Steps:
        1. 遍历所有分块
        2. 对于非文本块（图片/表格），直接添加到结果
        3. 对于文本块：
           - 如果使用了自定义分隔符，直接添加
           - 否则合并到上一个文本块（如果未超过限制）
        4. 记录包含图片的块索引

    Merge Strategy:
        - 图片/表格块：永不合并，保持独立
        - 文本块：
          - 自定义分隔符：不合并
          - 否则：合并到上一个块（如果未超过 token 限制）

    Note:
        - DOCX 文档专用函数
        - 被 naive_merge_docx 调用
    """
    merged = []
    image_idxs = []
    prev_text_ck = -1

    for i in range(len(cks)):
        ck_type = cks[i]["ck_type"]

        if ck_type != "text":
            # 图片/表格块：直接添加
            merged.append(cks[i])
            if ck_type == "image":
                image_idxs.append(len(merged) - 1)
            continue

        # 文本块：考虑合并
        if prev_text_ck<0 or merged[prev_text_ck]["tk_nums"] >= chunk_token_num or has_custom:
            # 创建新块
            merged.append(cks[i])
            prev_text_ck = len(merged) - 1
            continue

        # 合并到上一个块
        merged[prev_text_ck]["text"] = (merged[prev_text_ck].get("text") or "") + (cks[i].get("text") or "")
        merged[prev_text_ck]["tk_nums"] = merged[prev_text_ck].get("tk_nums", 0) + cks[i].get("tk_nums", 0)

    return merged, image_idxs


def naive_merge_docx(
    sections,
    chunk_token_num = 128,
    delimiter="\n。；！？",
    table_context_size=0,
    image_context_size=0,):
    """
    DOCX 文档专用分块算法

    该函数专门用于处理 DOCX 文档的分块，支持文本、图片、表格三种类型，
    并可为图片和表格添加上下文信息。

    Args:
        sections: 文档段落列表，每个元素为 (text, image, table) 元组
        chunk_token_num: 单个文本块的最大 token 数（默认 128）
        delimiter: 分隔符字符串（默认 "\n。；！？"）
        table_context_size: 表格上下文大小（默认 0）
        image_context_size: 图片上下文大小（默认 0）

    Returns:
        tuple: (merged_cks, merged_image_idx)
            - merged_cks: 分块后的列表，每个元素为字典格式
            - merged_image_idx: 包含图片的块的索引列表

    Chunk Dictionary Format:
        {
            "text": "文本内容",
            "image": PIL Image 或 None,
            "ck_type": "text" | "image" | "table",
            "tk_nums": token 数量,
            "context_above": "上方上下文"（可选）,
            "context_below": "下方上下文"（可选）
        }

    Processing Steps:
        1. 构建分块结构（_build_cks）
        2. 为表格添加上下文（如果 table_context_size > 0）
        3. 为图片添加上下文（如果 image_context_size > 0）
        4. 合并文本块（_merge_cks）
        5. 返回最终的分块列表

    Context Handling:
        - 上下文按句子单位添加，避免截断语义
        - 上下文包含在 text 字段中（前后追加）
        - 原始 text 保存在 context_above/below 中

    Examples:
        >>> sections = [("段落1", None, None), ("段落2", img, None)]
        >>> naive_merge_docx(sections, chunk_token_num=128, image_context_size=50)
        (["段落1", {"text": "段落1\n段落2", "image": img, "ck_type": "image", ...}], [1])

    Note:
        - DOCX 文档专用函数
        - 被 naive.py 中的 chunk() 函数调用（.docx 分支）
        - 支持 Markdown、表格、图片等多种元素
    """
    if not sections:
        return [], []

    # 步骤1：构建分块结构
    cks, tables, images, has_custom = _build_cks(sections, delimiter)

    # 步骤2：为表格添加上下文
    if table_context_size > 0:
        for i in tables:
            _add_context(cks, i, table_context_size)

    # 步骤3：为图片添加上下文
    if image_context_size > 0:
        for i in images:
            _add_context(cks, i, image_context_size)

    # 步骤4：合并文本块
    merged_cks, merged_image_idx = _merge_cks(cks, chunk_token_num, has_custom)

    return merged_cks, merged_image_idx


def extract_between(text: str, start_tag: str, end_tag: str) -> list[str]:
    pattern = re.escape(start_tag) + r"(.*?)" + re.escape(end_tag)
    return re.findall(pattern, text, flags=re.DOTALL)


def get_delimiters(delimiters: str):
    dels = []
    s = 0
    for m in re.finditer(r"`([^`]+)`", delimiters, re.I):
        f, t = m.span()
        dels.append(m.group(1))
        dels.extend(list(delimiters[s: f]))
        s = t
    if s < len(delimiters):
        dels.extend(list(delimiters[s:]))

    dels.sort(key=lambda x: -len(x))
    dels = [re.escape(d) for d in dels if d]
    dels = [d for d in dels if d]
    dels_pattern = "|".join(dels)

    return dels_pattern


class Node:
    """
    树节点类（用于层级文档结构）

    该类用于构建文档的层级结构树，支持按标题层级组织文档内容。
    主要用于 hierarchical_merge 和 tree_merge 函数。

    Attributes:
        level: 节点层级（0 为根节点，1 为一级标题，以此类推）
        depth: 目标深度（-1 表示无限制）
        texts: 节点包含的文本列表
        children: 子节点列表

    Usage:
        # 构建树
        root = Node(level=0, depth=2)
        root.build_tree([(1, "第一章"), (2, "1.1节"), (2, "1.2节")])

        # 获取树的内容
        chunks = root.get_tree()
    """

    def __init__(self, level, depth=-1, texts=None):
        """
        初始化节点

        Args:
            level: 节点层级
            depth: 目标深度（-1 表示无限制）
            texts: 节点包含的文本列表（可选）
        """
        self.level = level
        self.depth = depth
        self.texts = texts or []
        self.children = []

    def add_child(self, child_node):
        """
        添加子节点

        Args:
            child_node: 子节点对象
        """
        self.children.append(child_node)

    def get_children(self):
        """
        获取子节点列表

        Returns:
            list: 子节点列表
        """
        return self.children

    def get_level(self):
        """
        获取节点层级

        Returns:
            int: 节点层级
        """
        return self.level

    def get_texts(self):
        """
        获取节点文本列表

        Returns:
            list: 文本列表
        """
        return self.texts

    def set_texts(self, texts):
        """
        设置节点文本

        Args:
            texts: 文本列表
        """
        self.texts = texts

    def add_text(self, text):
        """
        添加文本到节点

        Args:
            text: 待添加的文本
        """
        self.texts.append(text)

    def clear_text(self):
        """
        清空节点文本
        """
        self.texts = []

    def __repr__(self):
        """
        节点的字符串表示

        Returns:
            str: 节点信息字符串
        """
        return f"Node(level={self.level}, texts={self.texts}, children={len(self.children)})"

    def build_tree(self, lines):
        """
        构建树结构

        该函数根据层级信息构建文档树，用于组织文档的层级结构。

        Args:
            lines: 层级行列表，每个元素为 (level, text) 元组

        Processing Steps:
            1. 遍历所有行
            2. 对于每行，根据层级找到合适的父节点
            3. 创建子节点并添加到父节点
            4. 维护栈以跟踪当前路径

        Depth Handling:
            - 如果 level > depth，将内容合并到当前叶子节点
            - 否则创建新的子节点

        Note:
            - 被 tree_merge 函数调用
            - 使用栈结构维护当前路径
        """
        stack = [self]
        for level, text in lines:
            if self.depth != -1 and level > self.depth:
                # 超过目标深度：将内容合并到当前叶子节点
                stack[-1].add_text(text)
                continue

            # 向上查找，直到找到层级严格小于当前节点的父节点
            while len(stack) > 1 and level <= stack[-1].get_level():
                stack.pop()

            # 创建新节点并添加到父节点
            node = Node(level=level, texts=[text])
            # 附加为当前父节点的子节点并下降
            stack[-1].add_child(node)
            stack.append(node)

        return self

    def get_tree(self):
        """
        获取树的内容列表

        该函数将树结构展平为文本块列表，每个块包含完整的层级路径。

        Returns:
            list: 文本块列表

        Output Format:
            - 根节点文本：直接添加
            - 深度内的标题：累积标题路径
            - 超出深度的正文：附加当前标题路径
            - 叶子标题：仅输出标题路径

        Note:
            - 使用深度优先搜索遍历树
        """
        tree_list = []
        self._dfs(self, tree_list, [])
        return tree_list

    def _dfs(self, node, tree_list, titles):
        """
        深度优先搜索遍历树

        Args:
            node: 当前节点
            tree_list: 结果列表（会被原地修改）
            titles: 当前标题路径

        Processing Logic:
            1. 根节点（level=0）：直接添加文本
            2. 深度内的标题：累积到标题路径
            3. 超出深度的正文：附加标题路径后添加
            4. 叶子标题：仅输出标题路径
            5. 递归处理子节点

        Examples:
            假设树结构为：
            - 第一章
              - 1.1节
                - 正文内容
              - 1.2节

            输出：
            [
                "第一章",
                "第一章\n1.1节",
                "第一章\n1.1节\n正文内容",
                "第一章\n1.2节"
            ]
        """
        level = node.get_level()
        texts = node.get_texts()
        child = node.get_children()

        # 根节点：直接添加文本
        if level == 0 and texts:
            tree_list.append("\n".join(titles + texts))

        # 深度内的标题：累积到当前路径
        if 1 <= level <= self.depth:
            path_titles = titles + texts
        else:
            path_titles = titles

        # 超出深度限制的正文：在当前标题路径下创建独立块
        if level > self.depth and texts:
            tree_list.append("\n".join(path_titles + texts))

        # 深度内的叶子标题：输出标题路径作为独立块
        elif not child and (1 <= level <= self.depth):
            tree_list.append("\n".join(path_titles))

        # 递归处理子节点
        for c in child:
            self._dfs(c, tree_list, path_titles)
