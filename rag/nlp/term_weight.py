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
词项权重计算模块

本模块提供了词项权重计算功能，用于评估词项在检索中的重要程度。
主要功能：
- 预处理文本（去除停用词、特殊字符）
- 词元合并（处理多字词、专有名词等）
- 权重计算（基于 NER、词频、词性等多维度）
- IDF 计算（逆文档频率）

使用场景：
- 全文检索时的关键词权重计算
- 查询扩展和优化
- 相关性评分
"""

import logging
import math
import json
import re
import os
import numpy as np
from rag.nlp import rag_tokenizer
from common.file_utils import get_project_base_directory


class Dealer:
    """
    词项权重计算器

    提供词项权重计算功能，综合考虑多种因素：
    - 停用词过滤
    - NER（命名实体识别）权重
    - 词频统计
    - 词性标注
    - IDF（逆文档频率）

    Attributes:
        stop_words: 停用词集合
        ne: 命名实体识别字典
        df: 词频统计字典
    """

    def __init__(self):
        """
        初始化词项权重计算器

        Note:
            - 加载 NER 词典（rag/res/ner.json）
            - 加载词频统计（rag/res/term.freq）
            - 初始化停用词列表
        """
        # 定义中文停用词列表
        self.stop_words = set([
            "请问", "您", "你", "我", "他", "是", "的", "就", "有", "于", "及",
            "即", "在", "为", "最", "有", "从", "以", "了", "将", "与", "吗", "吧",
            "中", "#", "什么", "怎么", "哪个", "哪些", "啥", "相关"
        ])

        def load_dict(fnm):
            """
            加载词典文件

            Args:
                fnm: 词典文件路径

            Returns:
                dict or set: 加载的词典
            """
            res = {}
            with open(fnm, "r") as f:
                while True:
                    line = f.readline()
                    if not line:
                        break
                    arr = line.replace("\n", "").split("\t")
                    if len(arr) < 2:
                        res[arr[0]] = 0
                    else:
                        res[arr[0]] = int(arr[1])

            # 如果所有值都是 0，返回键集合
            c = 0
            for _, v in res.items():
                c += v
            if c == 0:
                return set(res.keys())
            return res

        # 加载资源文件
        fnm = os.path.join(get_project_base_directory(), "rag/res")
        self.ne, self.df = {}, {}
        try:
            with open(os.path.join(fnm, "ner.json"), "r") as f:
                self.ne = json.load(f)
        except Exception:
            logging.warning("Load ner.json FAIL!")
        try:
            self.df = load_dict(os.path.join(fnm, "term.freq"))
        except Exception:
            logging.warning("Load term.freq FAIL!")

    def pretoken(self, txt, num=False, stpwd=True):
        """
        预处理文本，提取有效词元

        Args:
            txt: 输入文本
            num: 是否保留数字（默认 False）
            stpwd: 是否过滤停用词（默认 True）

        Returns:
            list: 有效的词元列表

        Note:
            - 移除停用词
            - 移除纯数字（除非 num=True）
            - 移除特殊符号
        """
        # 定义需要移除的特殊字符模式
        patt = [
            r"[~—\t @#%!<>,\.\?\":;'\{\}\[\]_=\(\)\|，。？》•●○↓《；''

：【】…￥！、·（）×`&\\/「」\\]"
        ]

        rewt = []
        for p, r in rewt:
            txt = re.sub(p, r, txt)

        res = []
        # 对分词结果进行过滤
        for t in rag_tokenizer.tokenize(txt).split():
            tk = t
            # 过滤停用词
            if (stpwd and tk in self.stop_words) or (
                    re.match(r"[0-9]$", tk) and not num):
                continue
            # 过滤特殊字符
            for p in patt:
                if re.match(p, t):
                    tk = "#"
                    break
            if tk != "#" and tk:
                res.append(tk)
        return res

    def token_merge(self, tks):
        """
        词元合并

        将相邻的词元合并为更大的词项，用于识别多字词和专有名词。

        Args:
            tks: 词元列表

        Returns:
            list: 合并后的词项列表

        Note:
            - 处理双字专有名词（如："人 工"、"多 工位"）
            - 单字符词元不合并
            - 停用词不参与合并
        """
        def one_term(t):
            """判断是否为单字词元"""
            return len(t) == 1 or re.match(r"[0-9a-z]{1,2}$", t)

        res, i = [], 0
        while i < len(tks):
            j = i
            # 处理双字专有名词
            if i == 0 and one_term(tks[i]) and len(
                    tks) > 1 and (len(tks[i + 1]) > 1 and not re.match(r"[0-9a-zA-Z]", tks[i + 1])):  # 多字符
                res.append(" ".join(tks[0:2]))
                i = 2
                continue

            # 收集连续的非停用词词元
            while j < len(
                    tks) and tks[j] and tks[j] not in self.stop_words and one_term(tks[j]):
                j += 1

            # 决定合并策略
            if j - i > 1:
                if j - i < 5:  # 短词组直接合并
                    res.append(" ".join(tks[i:j]))
                    i = j
                else:  # 长词组只合并前两个
                    res.append(" ".join(tks[i:i + 2]))
                    i = i + 2
            else:
                if len(tks[i]) > 0:
                    res.append(tks[i])
                i += 1
        return [t for t in res if t]

    def ner(self, t):
        """
        获取词项的 NER 类型权重

        Args:
            t: 词项

        Returns:
            float: NER 类型权重，如果未找到则返回 0

        Note:
            NER 类型权重：
            - toxic: 2（有害信息）
            - func: 1（函数）
            - corp: 3（公司名）
            - loca: 3（地名）
            - sch: 3（学校）
            - stock: 3（股票）
            - firstnm: 1（人名）
        """
        if not self.ne:
            return ""
        res = self.ne.get(t, "")
        if res:
            return res

    def split(self, txt):
        """
        拆分文本，处理英文单词连接

        将连续的英文单词拆分为独立词项。

        Args:
            txt: 输入文本

        Returns:
            list: 拆分后的词项列表

        Note:
            处理形如 "machinelearning" 的情况，拆分为 "machine learning"
        """
        tks = []
        for t in re.sub(r"[ \t]+", " ", txt).split():
            # 检测是否需要拆分英文单词
            if tks and re.match(r".*[a-zA-Z]$", tks[-1]) and \
                    re.match(r".*[a-zA-Z]$", t) and tks and \
                    self.ne.get(t, "") != "func" and self.ne.get(tks[-1], "") != "func":
                tks[-1] = tks[-1] + " " + t
            else:
                tks.append(t)
        return tks

    def weights(self, tks, preprocess=True):
        """
        计算词项权重

        综合考虑多种因素计算词项权重：
        - NER 类型权重
        - 词性标注权重
        - 词频统计
        - IDF 值

        Args:
            tks: 词元列表
            preprocess: 是否进行预处理（默认 True）

        Returns:
            list: [(词项, 权重)] 列表，按权重降序排列

        Note:
            权重计算公式：
            - IDF1: 基于词频的 IDF
            - IDF2: 基于文档频率的 IDF
            - NER: 命名实体权重
            - POSTAG: 词性权重
            - 最终权重 = 0.3*IDF1 + 0.7*IDF2 * NER * POSTAG
        """
        # 定义匹配模式
        num_pattern = re.compile(r"[0-9,.]{2,}$")           # 数字模式
        short_letter_pattern = re.compile(r"[a-z]{1,2}$")  # 短字母模式
        num_space_pattern = re.compile(r"[0-9. -]{2,}$")   # 数字空格模式
        letter_pattern = re.compile(r"[a-z. -]+$")      # 字母模式

        def ner(t):
            """NER 类型权重计算"""
            if num_pattern.match(t):
                return 2
            if short_letter_pattern.match(t):
                return 0.01
            if not self.ne or t not in self.ne:
                return 1
            # NER 类型权重映射
            m = {"toxic": 2, "func": 1, "corp": 3, "loca": 3, "sch": 3, "stock": 3,
                 "firstnm": 1}
            return m[self.ne[t]]

        def postag(t):
            """词性权重计算"""
            t = rag_tokenizer.tag(t)
            if t in set(["r", "c", "d"]):  # 代词、连词、数词
                return 0.3
            if t in set(["ns", "nt"]):      # 名词复数、专有名词
                return 3
            if t in set(["n"]):          # 名词单数
                return 2
            if re.match(r"[0-9-]+", t):   # 数字
                return 2
            return 1  # 默认权重

        def freq(t):
            """词频权重计算"""
            if num_space_pattern.match(t):
                return 3
            s = rag_tokenizer.freq(t)
            if not s and letter_pattern.match(t):
                return 300
            if not s:
                s = 0

            # 对长词进行细粒度分词后的词频
            if not s and len(t) >= 4:
                s = [tt for tt in rag_tokenizer.fine_grained_tokenize(t).split() if len(tt) > 1]
                if len(s) > 1:
                    s = np.min([freq(tt) for tt in s]) / 6.
                else:
                    s = 0

            return max(s, 10)

        def df(t):
            """文档频率权重计算"""
            if num_space_pattern.match(t):
                return 5
            if t in self.df:
                return self.df[t] + 3
            elif letter_pattern.match(t):
                return 300
            elif len(t) >= 4:
                s = [tt for tt in rag_tokenizer.fine_grained_tokenize(t).split() if len(tt) > 1]
                if len(s) > 1:
                    return max(3, np.min([df(tt) for tt in s]) / 6.)

            return 3

        def idf(s, N):
            """
            计算逆文档频率 (IDF)

            Args:
                s: 词频
                N: 文档总数

            Returns:
                float: IDF 值

            Note:
                使用平滑的 IDF 公式：log10(10 + (N - s + 0.5) / (s + 0.5))
            """
            return math.log10(10 + ((N - s + 0.5) / (s + 0.5)))

        tw = []
        if not preprocess:
            # 不预处理模式：直接计算权重
            idf1 = np.array([idf(freq(t), 10000000) for t in tks])
            idf2 = np.array([idf(df(t), 1000000000) for t in tks])
            wts = (0.3 * idf1 + 0.7 * idf2) * \
                  np.array([ner(t) * postag(t) for t in tks])
            wts = [s for s in wts]
            tw = list(zip(tks, wts))
        else:
            # 预处理模式：先合并词元再计算权重
            for tk in tks:
                tt = self.token_merge(self.pretoken(tk, True))
                idf1 = np.array([idf(freq(t), 10000000) for t in tt])
                idf2 = np.array([idf(df(t), 1000000000) for t in tt])
                wts = (0.3 * idf1 + 0.7 * idf2) * \
                      np.array([ner(t) * postag(t) for t in tt])
                wts = [s for s in wts]
                tw.extend(zip(tt, wts))

        # 归一化权重
        S = np.sum([s for _, s in tw])
        return [(t, s / S) for t, s in tw]
