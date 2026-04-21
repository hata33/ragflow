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
树形查询分解检索模块

本模块实现了树形结构的查询分解和检索算法，
通过递归地分解查询并从多个数据源检索信息，提高检索的全面性。

主要特点：
- 树形查询分解
- 多数据源检索（知识库、网络、知识图谱）
- 自动充分性检查
- 递归深度检索

使用场景：
- 复杂问题回答
- 多步骤信息检索
- 深度研究型查询
"""

import asyncio
import logging
from functools import partial
from api.db.services.llm_service import LLMBundle
from rag.prompts import kb_prompt
from rag.prompts.generator import sufficiency_check, multi_queries_gen
from rag.utils.tavily_conn import Tavily
from timeit import default_timer as timer


class TreeStructuredQueryDecompositionRetrieval:
    """
    树形查询分解检索类

    实现递归查询分解和多源检索功能。

    Attributes:
        chat_mdl: LLM 模型实例
        prompt_config: 提示词配置
        _kb_retrieve: 知识库检索函数
        _kg_retrieve: 知识图谱检索函数
        _lock: 异步锁，用于并发控制

    Example:
        >>> retriever = TreeStructuredQueryDecompositionRetrieval(
        ...     chat_mdl=llm_bundle,
        ...     prompt_config=config,
        ...     kb_retrieve=kb_retrieve_func
        ... )
        >>> await retriever.research(chunk_info, question, query)
    """

    def __init__(self,
                 chat_mdl: LLMBundle,
                 prompt_config: dict,
                 kb_retrieve: partial = None,
                 kg_retrieve: partial = None
                 ):
        """
        初始化树形查询分解检索器

        Args:
            chat_mdl: LLM 模型实例
            prompt_config: 提示词配置，可能包含：
                - tavily_api_key: Tavily API 密钥
                - use_kg: 是否使用知识图谱
            kb_retrieve: 知识库检索函数（可选）
            kg_retrieve: 知识图谱检索函数（可选）
        """
        self.chat_mdl = chat_mdl
        self.prompt_config = prompt_config
        self._kb_retrieve = kb_retrieve
        self._kg_retrieve = kg_retrieve
        self._lock = asyncio.Lock()

    async def _retrieve_information(self, search_query):
        """
        从不同数据源检索信息

        按顺序检索：知识库 → 网络 → 知识图谱

        Args:
            search_query: 搜索查询

        Returns:
            dict: 检索结果，包含 chunks 和 doc_aggs
        """
        """Retrieve information from different sources"""
        # 1. Knowledge base retrieval
        kbinfos = []
        try:
            kbinfos = await self._kb_retrieve(question=search_query) if self._kb_retrieve else {"chunks": [], "doc_aggs": []}
        except Exception as e:
            logging.error(f"Knowledge base retrieval error: {e}")

        # 2. Web retrieval (if Tavily API is configured)
        try:
            if self.prompt_config.get("tavily_api_key"):
                tav = Tavily(self.prompt_config["tavily_api_key"])
                tav_res = tav.retrieve_chunks(search_query)
                kbinfos["chunks"].extend(tav_res["chunks"])
                kbinfos["doc_aggs"].extend(tav_res["doc_aggs"])
        except Exception as e:
            logging.error(f"Web retrieval error: {e}")

        # 3. Knowledge graph retrieval (if configured)
        try:
            if self.prompt_config.get("use_kg") and self._kg_retrieve:
                ck = await self._kg_retrieve(question=search_query)
                if ck["content_with_weight"]:
                    kbinfos["chunks"].insert(0, ck)
        except Exception as e:
            logging.error(f"Knowledge graph retrieval error: {e}")

        return kbinfos

    async def _async_update_chunk_info(self, chunk_info, kbinfos):
        """
        异步更新块信息

        合并新检索的信息到现有块信息中，避免重复。

        Args:
            chunk_info: 现有块信息
            kbinfos: 新检索的信息

        Note:
            - 如果是第一次检索，直接使用检索结果
            - 否则合并新检索的信息，根据 chunk_id 和 doc_id 去重
        """
        async with self._lock:
            """Update chunk information for citations"""
            if not chunk_info["chunks"]:
                # If this is the first retrieval, use the retrieval results directly
                for k in chunk_info.keys():
                    chunk_info[k] = kbinfos[k]
            else:
                # Merge newly retrieved information, avoiding duplicates
                cids = [c["chunk_id"] for c in chunk_info["chunks"]]
                for c in kbinfos["chunks"]:
                    if c["chunk_id"] not in cids:
                        chunk_info["chunks"].append(c)

                dids = [d["doc_id"] for d in chunk_info["doc_aggs"]]
                for d in kbinfos["doc_aggs"]:
                    if d["doc_id"] not in dids:
                        chunk_info["doc_aggs"].append(d)

    async def research(self, chunk_info, question, query, depth=3, callback=None):
        """
        执行深度检索

        公开接口，执行树形查询分解检索。

        Args:
            chunk_info: 块信息存储（会被更新）
            question: 原始问题
            query: 当前查询
            depth: 最大检索深度（默认 3）
            callback: 进度回调函数

        Note:
            - 发送 START_DEEP_RESEARCH 和 END_DEEP_RESEARCH 信号
            - 递归检索直到达到最大深度或信息充分
        """
        if callback:
            await callback("<START_DEEP_RESEARCH>")
        await self._research(chunk_info, question, query, depth, callback)
        if callback:
            await callback("<END_DEEP_RESEARCH>")

    async def _research(self, chunk_info, question, query, depth=3, callback=None):
        """
        执行递归检索（内部实现）

        递归地检索信息，检查充分性，生成下一步查询。

        Args:
            chunk_info: 块信息存储（会被更新）
            question: 原始问题
            query: 当前查询
            depth: 剩余检索深度
            callback: 进度回调函数

        Returns:
            str: 检索结果摘要

        Processing Steps:
            1. 检查深度限制
            2. 执行多源检索
            3. 检查信息充分性
            4. 如果不充分，生成下一步查询并递归检索
        """
        if depth == 0:
            #if callback:
            #    await callback("Reach the max search depth.")
            return ""
        if callback:
            await callback(f"Searching by `{query}`...")
        st = timer()
        ret = await self._retrieve_information(query)
        if callback:
            await callback("Retrieval %d results in %.1fms"%(len(ret["chunks"]), (timer()-st)*1000))
        await self._async_update_chunk_info(chunk_info, ret)
        ret = kb_prompt(ret, self.chat_mdl.max_length*0.5)

        if callback:
            await callback("Checking the sufficiency for retrieved information.")
        suff = await sufficiency_check(self.chat_mdl, question, ret)
        if suff["is_sufficient"]:
            if callback:
                await callback(f"Yes, the retrieved information is sufficient for '{question}'.")
            return ret

        #if callback:
        #    await callback("The retrieved information is not sufficient. Planing next steps...")
        succ_question_info = await multi_queries_gen(self.chat_mdl, question, query, suff["missing_information"], ret)
        if callback:
            await callback("Next step is to search for the following questions:</br> - " + "</br> - ".join(step["question"] for step in succ_question_info["questions"]))
        steps = []
        for step in succ_question_info["questions"]:
            steps.append(asyncio.create_task(self._research(chunk_info, step["question"], step["query"], depth-1, callback)))
        results = await asyncio.gather(*steps, return_exceptions=True)
        return "\n".join([str(r) for r in results])
