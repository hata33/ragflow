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
同义词查找模块

本模块提供了同义词查找功能，支持自定义词典和 WordNet 回退。
主要功能：
- 从本地 JSON 文件加载自定义同义词词典
- 从 Redis 缓存加载同义词（可选）
- 使用 WordNet 作为回退同义词源
- 支持中英文同义词查找
"""

import logging
import json
import os
import time
import re
from nltk.corpus import wordnet
from common.file_utils import get_project_base_directory

# 强制 NLTK 同步加载语料库，避免并发任务触发的惰性加载竞争条件
try:
    wordnet.ensure_loaded()
except Exception:
    logging.warning("Fail to load wordnet.ensure_loaded()")


class Dealer:
    """
    同义词查找器

    提供同义词查找功能，支持多种数据源：
    1. 本地 JSON 词典文件
    2. Redis 缓存（可选）
    3. WordNet（作为回退）

    Attributes:
        lookup_num: 查询次数计数器
        load_tm: 上次加载时间
        dictionary: 同义词词典
        redis: Redis 连接（可选）

    Note:
        - 词典文件路径：rag/res/synonym.json
        - Redis 缓存键：kevin_synonyms
        - 每小时重新加载 Redis 数据
    """

    def __init__(self, redis=None):
        """
        初始化同义词查找器

        Args:
            redis: Redis 连接对象（可选）

        Note:
            - 如果没有 Redis 连接，实时同义词功能将被禁用
            - 如果词典加载失败，将回退到 WordNet
        """
        self.lookup_num = 100000000  # 查询次数计数器
        self.load_tm = time.time() - 1000000  # 上次加载时间（初始化为很久以前）
        self.dictionary = None

        # 加载本地同义词词典
        path = os.path.join(get_project_base_directory(), "rag/res", "synonym.json")
        try:
            with open(path, 'r') as f:
                self.dictionary = json.load(f)

            # 转换为小写键以支持大小写不敏感查找
            self.dictionary = {
                (k.lower() if isinstance(k, str) else k): v
                for k, v in self.dictionary.items()
            }
        except Exception:
            logging.warning("Missing synonym.json")
            self.dictionary = {}

        # 检查 Redis 连接
        if not redis:
            logging.warning(
                "Realtime synonym is disabled, since no redis connection.")
        if not len(self.dictionary.keys()):
            logging.warning("Fail to load synonym")

        self.redis = redis
        self.load()  # 初始加载

    def load(self):
        """
        从 Redis 加载同义词数据

        Note:
            - 每小时重新加载一次（避免频繁加载）
            - 查询次数 < 100 时不加载（避免不必要的加载）
        """
        if not self.redis:
            return

        # 查询次数不足时跳过
        if self.lookup_num < 100:
            return

        # 检查是否需要重新加载（1小时内不重复加载）
        tm = time.time()
        if tm - self.load_tm < 3600:
            return

        self.load_tm = time.time()
        self.lookup_num = 0

        # 从 Redis 获取同义词数据
        d = self.redis.get("kevin_synonyms")
        if not d:
            return

        try:
            d = json.loads(d)
            self.dictionary = d
        except Exception as e:
            logging.error("Fail to load synonym!" + str(e))

    def lookup(self, tk, topn=8):
        """
        查找词项的同义词

        按优先级查找同义词：
        1. 本地词典
        2. WordNet（仅纯字母词项）

        Args:
            tk: 待查找的词项
            topn: 返回前 N 个同义词（默认 8）

        Returns:
            list: 同义词列表，如果找不到则返回空列表

        Note:
            - 查找是大小写不敏感的
            - 对于纯字母词，会回退到 WordNet
            - WordNet 结果会移除原始词本身
        """
        if not tk or not isinstance(tk, str):
            return []

        # 1) 优先检查自定义词典（键和词项都是小写）
        self.lookup_num += 1
        self.load()  # 检查是否需要重新加载
        key = re.sub(r"[ \t]+", " ", tk.strip())
        res = self.dictionary.get(key, [])

        # 如果是字符串，转换为列表
        if isinstance(res, str):
            res = [res]

        # 找到同义词，直接返回
        if res:
            return res[:topn]

        # 2) 如果是纯字母词，回退到 WordNet
        if re.fullmatch(r"[a-z]+", tk):
            # 从 WordNet 获取同义词集
            wn_set = {
                re.sub("_", " ", syn.name().split(".")[0]
                for syn in wordnet.synsets(tk)
            }
            wn_set.discard(tk)  # 移除原始词本身
            wn_res = [t for t in wn_set if t]
            return wn_res[:topn]

        # 3) 未找到同义词
        return []


# 创建全局实例（用于测试）
if __name__ == '__main__':
    dl = Dealer()
    print(dl.dictionary)
