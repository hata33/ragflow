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
全文查询模块

本模块提供了全文检索查询的功能，支持中英文混合查询、
关键词提取、同义词扩展、相似度计算等核心功能。
主要功能：
- 构建全文检索查询表达式
- 关键词权重计算
- 同义词扩展
- 向量相似度与词元相似度混合计算
"""

import logging
import json
import re
from collections import defaultdict

from common.query_base import QueryBase
from common.doc_store.doc_store_base import MatchTextExpr
from rag.nlp import rag_tokenizer, term_weight, synonym


class FulltextQueryer(QueryBase):
    """
    全文查询器

    提供全文检索查询功能，支持关键词提取、权重计算、
    同义词扩展等，用于构建高质量的检索查询。

    Attributes:
        tw: 词项权重计算器
        syn: 同义词查找器
        query_fields: 查询字段列表，定义各字段的权重

    Note:
        查询字段格式：字段名^权重
        - title_tks^10: 标题分词，权重 10
        - title_sm_tks^5: 标题小写分词，权重 5
        - important_kwd^30: 重要关键词，权重 30
        - question_tks^20: 问题分词，权重 20
        - content_ltks^2: 内容小写分词，权重 2
    """

    def __init__(self):
        """初始化全文查询器"""
        self.tw = term_weight.Dealer()      # 词项权重计算器
        self.syn = synonym.Dealer()           # 同义词查找器
        # 定义查询字段及其权重
        self.query_fields = [
            "title_tks^10",                   # 标题分词（权重 10）
            "title_sm_tks^5",                 # 标题小写分词（权重 5）
            "important_kwd^30",               # 重要关键词（权重 30）
            "important_tks^20",               # 重要分词（权重 20）
            "question_tks^20",                # 问题分词（权重 20）
            "content_ltks^2",                 # 内容小写分词（权重 2）
            "content_sm_ltks",                # 内容小写分词（默认权重）
        ]

    def question(self, txt, tbl="qa", min_match: float = 0.6):
        """
        构建问题查询

        将用户的问题文本转换为全文检索查询表达式，
        包括关键词提取、权重计算、同义词扩展等。

        Args:
            txt: 用户问题文本
            tbl: 表名（默认 "qa"）
            min_match: 最小匹配阈值（默认 0.6）

        Returns:
            tuple: (MatchTextExpr, keywords)
                - MatchTextExpr: 匹配文本表达式
                - keywords: 提取的关键词列表
                - 如果无法处理则返回 (None, keywords)

        Note:
            - 中英文使用不同的处理逻辑
            - 中文使用分词和词权重
            - 英文使用词权重和同义词扩展
            - 支持 Infinity 特殊字符的转义处理
        """
        original_query = txt
        # 在中英文之间添加空格
        txt = self.add_space_between_eng_zh(txt)

        # 移除 Infinity 可转义字符，避免解析错误
        # Infinity 的转义字符包括：[ :|\r\n\t,，。？?/`!！&^%%()\[\]{}<>*~'\"\\]
        txt = re.sub(
            r"[ :|\r\n\t,，。？?/`!！&^%%()\[\]{}<>*~'\"\\]+",
            " ",
            rag_tokenizer.tradi2simp(rag_tokenizer.strQ2B(txt.lower())),
        ).strip()
        otxt = txt
        txt = self.rmWWW(txt)  # 移除 WWW 内容

        # 非中文查询处理（主要是英文）
        if not self.is_chinese(txt):
            txt = self.rmWWW(txt)
            tks = rag_tokenizer.tokenize(txt).split()
            keywords = [t for t in tks if t]
            tks_w = self.tw.weights(tks, preprocess=False)

            # 清理词项中的特殊字符
            tks_w = [(re.sub(r"[ \\\"'^]", "", tk), w) for tk, w in tks_w]
            tks_w = [(re.sub(r"^[\+-]", "", tk), w) for tk, w in tks_w if tk]
            tks_w = [(tk.strip(), w) for tk, w in tks_w if tk.strip()]

            # 处理同义词
            syns = []
            for tk, w in tks_w[:256]:  # 限制词项数量
                # 从同义词中移除单引号，避免 Infinity 解析错误
                syn = [rag_tokenizer.tokenize(s).replace("'", "") for s in self.syn.lookup(tk)]
                keywords.extend(syn)
                # 为同义词添加权重
                syn = ["\"{}\"^{:.4f}".format(s, w / 4.) for s in syn if s.strip()]
                syns.append(" ".join(syn))

            # 构建查询表达式
            q = ["({}^{:.4f}".format(tk, w) + " {})".format(syn) for (tk, w), syn in zip(tks_w, syns) if
                 tk and not re.match(r"[.^+\(\)-]", tk)]

            # 添加词组查询
            for i in range(1, len(tks_w)):
                left, right = tks_w[i - 1][0].strip(), tks_w[i][0].strip()
                if not left or not right:
                    continue
                q.append(
                    '"%s %s"^%.4f'
                    % (
                        tks_w[i - 1][0],
                        tks_w[i][0],
                        max(tks_w[i - 1][1], tks_w[i][1]) * 2,
                    )
                )

            if not q:
                q.append(txt)

            query = " ".join(q)
            return MatchTextExpr(
                self.query_fields, query, 100, {"original_query": original_query}
            ), keywords

        # 判断是否需要细粒度分词
        def need_fine_grained_tokenize(tk):
            """
            判断词项是否需要细粒度分词

            Args:
                tk: 词项

            Returns:
                bool: 是否需要细粒度分词
            """
            if len(tk) < 3:
                return False
            if re.match(r"[0-9a-z\.\+#_\*-]+$", tk):
                return False
            return True

        txt = self.rmWWW(txt)
        qs, keywords = [], []

        # 处理每个分词项
        for tt in self.tw.split(txt)[:256]:  # 限制分词数量
            if not tt:
                continue
            keywords.append(tt)
            twts = self.tw.weights([tt])
            syns = self.syn.lookup(tt)

            # 如果有同义词且关键词数量较少，则添加同义词
            if syns and len(keywords) < 32:
                keywords.extend(syns)

            logging.debug(json.dumps(twts, ensure_ascii=False))
            tms = []

            # 对词项按权重排序
            for tk, w in sorted(twts, key=lambda x: x[1] * -1):
                # 细粒度分词
                sm = (
                    rag_tokenizer.fine_grained_tokenize(tk).split()
                    if need_fine_grained_tokenize(tk)
                    else []
                )
                # 清理分词结果中的特殊字符
                sm = [
                    re.sub(
                        r"[ ,\./;'\[\]\\`~!@#$%\^&\*\(\)=\+_<>\?:\"\{\}\|，。；''【】、！￥……（）——《》？："”-]+",  "",   m,                  )
                    for m in sm
                ]
                sm = [self.sub_special_char(m) for m in sm if len(m) > 1]
                sm = [m for m in sm if len(m) > 1]

                # 添加关键词和分词结果
                if len(keywords) < 32:
                    keywords.append(re.sub(r"[ \\\"']+", "", tk))
                    keywords.extend(sm)

                # 处理同义词
                tk_syns = self.syn.lookup(tk)
                tk_syns = [self.sub_special_char(s) for s in tk_syns]
                if len(keywords) < 32:
                    keywords.extend([s for s in tk_syns if s])

                # 对同义词进行细粒度分词
                tk_syns = [rag_tokenizer.fine_grained_tokenize(s) for s in tk_syns if s]
                tk_syns = [f"\"{s}\"" if s.find(" ") > 0 else s for s in tk_syns]

                if len(keywords) >= 32:
                    break

                # 构建词项查询表达式
                tk = self.sub_special_char(tk)
                if tk.find(" ") > 0:
                    tk = '"%s"' % tk
                if tk_syns:
                    tk = f"({tk} OR (%s)^0.2)" % " ".join(tk_syns)
                if sm:
                    tk = f'{tk} OR "%s" OR ("%s"~2)^0.5' % (" ".join(sm), " ".join(sm))
                if tk.strip():
                    tms.append((tk, w))

            # 构建查询字符串
            tms = " ".join([f"({t})^{w}" for t, w in tms])

            # 添加原始词项的模糊匹配
            if len(twts) > 1:
                tms += ' ("%s"~2)^1.5' % rag_tokenizer.tokenize(tt)

            # 处理同义词查询
            syns = " OR ".join(
                [
                    '"%s"'
                    % rag_tokenizer.tokenize(self.sub_special_char(s))
                    for s in syns
                ]
            )
            if syns and tms:
                tms = f"({tms})^5 OR ({syns})^0.7"

            qs.append(tms)

        # 构建最终查询
        if qs:
            query = " OR ".join([f"({t})" for t in qs if t])
            if not query:
                query = otxt
            return MatchTextExpr(
                self.query_fields, query, 100, {"minimum_should_match": min_match, "original_query": original_query}
            ), keywords
        return None, keywords

    def hybrid_similarity(self, avec, bvecs, atks, btkss, tkweight=0.3, vtweight=0.7):
        """
        混合相似度计算

        结合向量相似度和词元相似度，计算混合相似度分数。

        Args:
            avec: 查询向量
            bvecs: 候选文档向量列表
            atks: 查询词元
            btkss: 候选文档词元列表
            tkweight: 词元相似度权重（默认 0.3）
            vtweight: 向量相似度权重（默认 0.7）

        Returns:
            tuple: (混合相似度数组, 词元相似度数组, 向量相似度数组)

        Note:
            当向量相似度全为 0 时，只使用词元相似度
        """
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np

        # 计算向量相似度（余弦相似度）
        sims = cosine_similarity([avec], bvecs)
        # 计算词元相似度
        tksim = self.token_similarity(atks, btkss)

        # 如果向量相似度全为 0，只使用词元相似度
        if np.sum(sims[0]) == 0:
            return np.array(tksim), tksim, sims[0]

        # 加权混合
        return np.array(sims[0]) * vtweight + np.array(tksim) * tkweight, tksim, sims[0]

    def token_similarity(self, atks, btkss):
        """
        词元相似度计算

        计算查询词元与文档词元之间的相似度。

        Args:
            atks: 查询词元（字符串或字典）
            btkss: 候选文档词元列表

        Returns:
            list: 相似度分数列表

        Note:
            - 考虑词频和位置信息
            - 使用位置权重：当前位置 0.4，下一位置 0.6
        """
        def to_dict(tks):
            """
            将词元转换为字典形式

            Args:
                tks: 词元（字符串或列表）

            Returns:
                dict: 词元到权重的字典
            """
            if isinstance(tks, str):
                tks = tks.split()
            d = defaultdict(int)
            wts = self.tw.weights(tks, preprocess=False)
            for i, (t, c) in enumerate(wts):
                d[t] += c * 0.4  # 当前位置权重
                if i+1 < len(wts):
                    _t, _c = wts[i+1]
                    d[t+_t] += max(c, _c) * 0.6  # 下一位置权重
            return d

        # 转换为字典形式
        atks = to_dict(atks)
        btkss = [to_dict(tks) for tks in btkss]
        return [self.similarity(atks, btks) for btks in btkss]

    def similarity(self, qtwt, dtwt):
        """
        计算两个词元权重集合的相似度

        Args:
            qtwt: 查询词元权重（字符串或字典）
            dtwt: 文档词元权重（字符串或字典）

        Returns:
            float: 相似度分数（0-1 之间）

        Note:
            使用 Jaccard 相似度算法
        """
        # 转换为字典形式
        if isinstance(dtwt, type("")):
            dtwt = {t: w for t, w in self.tw.weights(self.tw.split(dtwt), preprocess=False)}
        if isinstance(qtwt, type("")):
            qtwt = {t: w for t, w in self.tw.weights(self.tw.split(qtwt), preprocess=False)}

        # 计算交集权重
        s = 1e-9
        for k, v in qtwt.items():
            if k in dtwt:
                s += v

        # 计算查询权重
        q = 1e-9
        for k, v in qtwt.items():
            q += v

        # Jaccard 相似度
        return s / q

    def paragraph(self, content_tks: str, keywords: list = [], keywords_topn=30):
        """
        构建段落查询

        基于内容词元和关键词构建段落级别的查询表达式。

        Args:
            content_tks: 内容词元（字符串或列表）
            keywords: 关键词列表
            keywords_topn: 选取前 N 个高频关键词（默认 30）

        Returns:
            MatchTextExpr: 匹配文本表达式

        Note:
            - 对内容词元进行权重排序
            - 为每个词元添加同义词扩展
            - 使用 OR 逻辑组合查询
        """
        if isinstance(content_tks, str):
            content_tks = [c.strip() for c in content_tks.strip() if c.strip()]

        # 计算词元权重
        tks_w = self.tw.weights(content_tks, preprocess=False)

        origin_keywords = keywords.copy()
        keywords = [f'"{k.strip()}"' for k in keywords]

        # 选取高频词元并进行同义词扩展
        for tk, w in sorted(tks_w, key=lambda x: x[1] * -1)[:keywords_topn]:
            tk_syns = self.syn.lookup(tk)
            tk_syns = [self.sub_special_char(s) for s in tk_syns]
            tk_syns = [rag_tokenizer.fine_grained_tokenize(s) for s in tk_syns if s]
            tk_syns = [f"\"{s}\"" if s.find(" ") > 0 else s for s in tk_syns]

            tk = self.sub_special_char(tk)
            if tk.find(" ") > 0:
                tk = '"%s"' % tk
            if tk_syns:
                tk = f"({tk} OR (%s)^0.2)" % " ".join(tk_syns)
            if tk:
                keywords.append(f"{tk}^{w}")

        # 构建查询表达式
        return MatchTextExpr(self.query_fields, " ".join(keywords), 100,
                             {"minimum_should_match": min(3, round(len(keywords) / 10)),
                              "original_query": " ".join(origin_keywords)})
