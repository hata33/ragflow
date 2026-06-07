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
RAGFlow 检索性能基准测试模块

本模块提供对 RAGFlow 检索系统进行标准化性能测试的功能。
支持多个公开基准数据集：MS MARCO、TriviaQA、MIRACL。

主要功能：
- 构建测试数据的检索索引
- 执行检索性能评估
- 计算标准指标（nDCG@10、MAP@5、MRR@10）
- 生成详细的评估报告

使用方法：
    python benchmark.py <max_docs> <kb_id> <dataset> <dataset_path> [<miracl_corpus_path>]

支持的数据集：
    - ms_marco_v1.1: Microsoft Machine Reading Comprehension Dataset
    - trivia_qa: Trivia Question Answering Dataset
    - miracl: Multilingual Information Retrieval Across a Continuum of Languages
"""

import asyncio
import json
import os
import sys
import time
import argparse
from collections import defaultdict

from common import settings
from common.constants import LLMType
from api.db.services.llm_service import LLMBundle
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.joint_services.tenant_model_service import get_model_config_from_provider_instance
from common.misc_utils import get_uuid
from rag.nlp import tokenize, search
from ranx import evaluate
from ranx import Qrels, Run
import pandas as pd
from tqdm import tqdm

global max_docs
max_docs = sys.maxsize


class Benchmark:
    """
    RAGFlow 检索性能基准测试类

    提供对检索系统进行标准化性能评估的功能，
    支持多个公开基准数据集的索引构建和性能测试。

    Attributes:
        kb_id: 知识库 ID
        similarity_threshold: 相似度阈值
        vector_similarity_weight: 向量相似度权重
        embd_mdl: 嵌入模型实例
        tenant_id: 租户 ID
        index_name: 索引名称
        initialized_index: 索引是否已初始化

    Example:
        >>> benchmark = Benchmark(kb_id="your_kb_id")
        >>> benchmark("ms_marco_v1.1", "/path/to/dataset")
    """

    def __init__(self, kb_id):
    def __init__(self, kb_id):
        self.kb_id = kb_id
        e, self.kb = KnowledgebaseService.get_by_id(kb_id)
        self.similarity_threshold = self.kb.similarity_threshold
        self.vector_similarity_weight = self.kb.vector_similarity_weight
        embd_model_config = get_model_config_from_provider_instance(self.kb.tenant_id, LLMType.EMBEDDING, self.kb.embd_id)
        self.embd_mdl = LLMBundle(self.kb.tenant_id, embd_model_config, lang=self.kb.language)
        self.tenant_id = ''
        self.index_name = ''
        self.initialized_index = False

    def _get_retrieval(self, qrels):
        """
        执行检索并返回结果

        对给定的查询相关性数据执行检索，并返回检索结果。

        Args:
            qrels: 查询相关性字典，格式为 {query: {doc_id: relevance}}

        Returns:
            dict: 检索结果，格式为 {query: {doc_id: similarity_score}}

        Note:
            会等待 20 秒以确保 ES 和 Infinity 索引准备就绪
        """
        # Need to wait for the ES and Infinity index to be ready
        # Need to wait for the ES and Infinity index to be ready
        time.sleep(20)
        run = defaultdict(dict)
        query_list = list(qrels.keys())
        for query in query_list:
            ranks = asyncio.run(settings.retriever.retrieval(query, self.embd_mdl, self.tenant_id, [self.kb.id], 1, 30,
                                            0.0, self.vector_similarity_weight))
            if len(ranks["chunks"]) == 0:
                print(f"deleted query: {query}")
                del qrels[query]
                continue
            for c in ranks["chunks"]:
                c.pop("vector", None)
                run[query][c["chunk_id"]] = c["similarity"]
        return run

    def embedding(self, docs):
        """
        为文档列表生成向量嵌入

        使用嵌入模型为文档内容生成向量表示。

        Args:
            docs: 文档列表，每个文档包含 content_with_weight 字段

        Returns:
            tuple: (docs, vector_size)
                - docs: 添加了向量字段的文档列表
                - vector_size: 向量维度大小

        Raises:
            AssertionError: 当文档数量与嵌入数量不匹配时
        """
        texts = [d["content_with_weight"] for d in docs]
        texts = [d["content_with_weight"] for d in docs]
        embeddings, _ = self.embd_mdl.encode(texts)
        assert len(docs) == len(embeddings)
        vector_size = 0
        for i, d in enumerate(docs):
            v = embeddings[i]
            vector_size = len(v)
            d["q_%d_vec" % len(v)] = v
        return docs, vector_size

    def init_index(self, vector_size: int):
        """
        初始化文档存储索引

        创建或重新创建文档存储索引。

        Args:
            vector_size: 向量维度大小

        Note:
            如果索引已存在，会先删除再创建
        """
        if self.initialized_index:
        if self.initialized_index:
            return
        if settings.docStoreConn.index_exist(self.index_name, self.kb_id):
            settings.docStoreConn.delete_idx(self.index_name, self.kb_id)
        settings.docStoreConn.create_idx(self.index_name, self.kb_id, vector_size)
        self.initialized_index = True

    def ms_marco_index(self, file_path, index_name):
        """
        构建 MS MARCO v1.1 数据集索引

        从 MS MARCO 数据集文件中读取数据并构建检索索引。

        Args:
            file_path: 数据集文件路径
            index_name: 索引名称

        Returns:
            tuple: (qrels, texts)
                - qrels: 查询相关性字典
                - texts: 文档内容字典 {doc_id: text}

        Note:
            支持读取 parquet 格式的数据文件
        """
        qrels = defaultdict(dict)
        qrels = defaultdict(dict)
        texts = defaultdict(dict)
        docs_count = 0
        docs = []
        filelist = sorted(os.listdir(file_path))

        for fn in filelist:
            if docs_count >= max_docs:
                break
            if not fn.endswith(".parquet"):
                continue
            data = pd.read_parquet(os.path.join(file_path, fn))
            for i in tqdm(range(len(data)), colour="green", desc="Tokenizing:" + fn):
                if docs_count >= max_docs:
                    break
                query = data.iloc[i]['query']
                for rel, text in zip(data.iloc[i]['passages']['is_selected'], data.iloc[i]['passages']['passage_text']):
                    d = {
                        "id": get_uuid(),
                        "kb_id": self.kb.id,
                        "docnm_kwd": "xxxxx",
                        "doc_id": "ksksks"
                    }
                    tokenize(d, text, "english")
                    docs.append(d)
                    texts[d["id"]] = text
                    qrels[query][d["id"]] = int(rel)
                if len(docs) >= 32:
                    docs_count += len(docs)
                    docs, vector_size = self.embedding(docs)
                    self.init_index(vector_size)
                    settings.docStoreConn.insert(docs, self.index_name, self.kb_id)
                    docs = []

        if docs:
            docs, vector_size = self.embedding(docs)
            self.init_index(vector_size)
            settings.docStoreConn.insert(docs, self.index_name, self.kb_id)
        return qrels, texts

    def trivia_qa_index(self, file_path, index_name):
        """
        构建 TriviaQA 数据集索引

        从 TriviaQA 数据集文件中读取数据并构建检索索引。

        Args:
            file_path: 数据集文件路径
            index_name: 索引名称

        Returns:
            tuple: (qrels, texts)
                - qrels: 查询相关性字典
                - texts: 文档内容字典 {doc_id: text}
        """
        qrels = defaultdict(dict)
        qrels = defaultdict(dict)
        texts = defaultdict(dict)
        docs_count = 0
        docs = []
        filelist = sorted(os.listdir(file_path))
        for fn in filelist:
            if docs_count >= max_docs:
                break
            if not fn.endswith(".parquet"):
                continue
            data = pd.read_parquet(os.path.join(file_path, fn))
            for i in tqdm(range(len(data)), colour="green", desc="Indexing:" + fn):
                if docs_count >= max_docs:
                    break
                query = data.iloc[i]['question']
                for rel, text in zip(data.iloc[i]["search_results"]['rank'],
                                     data.iloc[i]["search_results"]['search_context']):
                    d = {
                        "id": get_uuid(),
                        "kb_id": self.kb.id,
                        "docnm_kwd": "xxxxx",
                        "doc_id": "ksksks"
                    }
                    tokenize(d, text, "english")
                    docs.append(d)
                    texts[d["id"]] = text
                    qrels[query][d["id"]] = int(rel)
                if len(docs) >= 32:
                    docs_count += len(docs)
                    docs, vector_size = self.embedding(docs)
                    self.init_index(vector_size)
                    settings.docStoreConn.insert(docs,self.index_name)
                    docs = []

        docs, vector_size = self.embedding(docs)
        self.init_index(vector_size)
        settings.docStoreConn.insert(docs, self.index_name)
        return qrels, texts

    def miracl_index(self, file_path, corpus_path, index_name):
        """
        构建 MIRACL 数据集索引

        从 MIRACL 多语言数据集文件中读取数据并构建检索索引。

        Args:
            file_path: MIRACL 数据集路径
            corpus_path: 语料库路径
            index_name: 索引名称

        Returns:
            tuple: (qrels, texts)
                - qrels: 查询相关性字典
                - texts: 文档内容字典 {doc_id: text}
        """
        corpus_total = {}
        corpus_total = {}
        for corpus_file in os.listdir(corpus_path):
            tmp_data = pd.read_json(os.path.join(corpus_path, corpus_file), lines=True)
            for index, i in tmp_data.iterrows():
                corpus_total[i['docid']] = i['text']

        topics_total = {}
        for topics_file in os.listdir(os.path.join(file_path, 'topics')):
            if 'test' in topics_file:
                continue
            tmp_data = pd.read_csv(os.path.join(file_path, 'topics', topics_file), sep='\t', names=['qid', 'query'])
            for index, i in tmp_data.iterrows():
                topics_total[i['qid']] = i['query']

        qrels = defaultdict(dict)
        texts = defaultdict(dict)
        docs_count = 0
        docs = []
        for qrels_file in os.listdir(os.path.join(file_path, 'qrels')):
            if 'test' in qrels_file:
                continue
            if docs_count >= max_docs:
                break

            tmp_data = pd.read_csv(os.path.join(file_path, 'qrels', qrels_file), sep='\t',
                                   names=['qid', 'Q0', 'docid', 'relevance'])
            for i in tqdm(range(len(tmp_data)), colour="green", desc="Indexing:" + qrels_file):
                if docs_count >= max_docs:
                    break
                query = topics_total[tmp_data.iloc[i]['qid']]
                text = corpus_total[tmp_data.iloc[i]['docid']]
                rel = tmp_data.iloc[i]['relevance']
                d = {
                    "id": get_uuid(),
                    "kb_id": self.kb.id,
                    "docnm_kwd": "xxxxx",
                    "doc_id": "ksksks"
                }
                tokenize(d, text, 'english')
                docs.append(d)
                texts[d["id"]] = text
                qrels[query][d["id"]] = int(rel)
                if len(docs) >= 32:
                    docs_count += len(docs)
                    docs, vector_size = self.embedding(docs)
                    self.init_index(vector_size)
                    settings.docStoreConn.insert(docs, self.index_name)
                    docs = []

        docs, vector_size = self.embedding(docs)
        self.init_index(vector_size)
        settings.docStoreConn.insert(docs, self.index_name)
        return qrels, texts

    def save_results(self, qrels, run, texts, dataset, file_path):
        """
        保存基准测试结果

        将评估结果保存为 JSON 和 Markdown 格式。

        Args:
            qrels: 查询相关性数据
            run: 检索结果数据
            texts: 文档内容字典
            dataset: 数据集名称
            file_path: 保存路径

        Note:
            会生成三个文件：
            - {dataset}.qrels.json: 查询相关性数据
            - {dataset}.run.json: 检索结果数据
            - {dataset}_result.md: 详细评估报告
        """
        keep_result = []
        keep_result = []
        run_keys = list(run.keys())
        for run_i in tqdm(range(len(run_keys)), desc="Calculating ndcg@10 for single query"):
            key = run_keys[run_i]
            keep_result.append({'query': key, 'qrel': qrels[key], 'run': run[key],
                                'ndcg@10': evaluate({key: qrels[key]}, {key: run[key]}, "ndcg@10")})
        keep_result = sorted(keep_result, key=lambda kk: kk['ndcg@10'])
        with open(os.path.join(file_path, dataset + 'result.md'), 'w', encoding='utf-8') as f:
            f.write('## Score For Every Query\n')
            for keep_result_i in keep_result:
                f.write('### query: ' + keep_result_i['query'] + ' ndcg@10:' + str(keep_result_i['ndcg@10']) + '\n')
                scores = [[i[0], i[1]] for i in keep_result_i['run'].items()]
                scores = sorted(scores, key=lambda kk: kk[1])
                for score in scores[:10]:
                    f.write('- text: ' + str(texts[score[0]]) + '\t qrel: ' + str(score[1]) + '\n')
        json.dump(qrels, open(os.path.join(file_path, dataset + '.qrels.json'), "w+", encoding='utf-8'), indent=2)
        json.dump(run, open(os.path.join(file_path, dataset + '.run.json'), "w+", encoding='utf-8'), indent=2)
        print(os.path.join(file_path, dataset + '_result.md'), 'Saved!')

    def __call__(self, dataset, file_path, miracl_corpus=''):
        """
        执行基准测试

        根据指定的数据集类型执行完整的基准测试流程。

        Args:
            dataset: 数据集名称，支持：
                - "ms_marco_v1.1": MS MARCO v1.1 数据集
                - "trivia_qa": TriviaQA 数据集
                - "miracl": MIRACL 多语言数据集
            file_path: 数据集文件路径
            miracl_corpus: MIRACL 语料库路径（仅当 dataset="miracl" 时需要）

        Raises:
            SystemExit: 当参数不正确时

        Example:
            >>> benchmark = Benchmark(kb_id)
            >>> benchmark("ms_marco_v1.1", "/path/to/ms_marco")
        """
        if dataset == "ms_marco_v1.1":
        if dataset == "ms_marco_v1.1":
            self.tenant_id = "benchmark_ms_marco_v11"
            self.index_name = search.index_name(self.tenant_id)
            qrels, texts = self.ms_marco_index(file_path, "benchmark_ms_marco_v1.1")
            run = self._get_retrieval(qrels)
            print(dataset, evaluate(Qrels(qrels), Run(run), ["ndcg@10", "map@5", "mrr@10"]))
            self.save_results(qrels, run, texts, dataset, file_path)
        if dataset == "trivia_qa":
            self.tenant_id = "benchmark_trivia_qa"
            self.index_name = search.index_name(self.tenant_id)
            qrels, texts = self.trivia_qa_index(file_path, "benchmark_trivia_qa")
            run = self._get_retrieval(qrels)
            print(dataset, evaluate(Qrels(qrels), Run(run), ["ndcg@10", "map@5", "mrr@10"]))
            self.save_results(qrels, run, texts, dataset, file_path)
        if dataset == "miracl":
            for lang in ['ar', 'bn', 'de', 'en', 'es', 'fa', 'fi', 'fr', 'hi', 'id', 'ja', 'ko', 'ru', 'sw', 'te', 'th',
                         'yo', 'zh']:
                if not os.path.isdir(os.path.join(file_path, 'miracl-v1.0-' + lang)):
                    print('Directory: ' + os.path.join(file_path, 'miracl-v1.0-' + lang) + ' not found!')
                    continue
                if not os.path.isdir(os.path.join(file_path, 'miracl-v1.0-' + lang, 'qrels')):
                    print('Directory: ' + os.path.join(file_path, 'miracl-v1.0-' + lang, 'qrels') + 'not found!')
                    continue
                if not os.path.isdir(os.path.join(file_path, 'miracl-v1.0-' + lang, 'topics')):
                    print('Directory: ' + os.path.join(file_path, 'miracl-v1.0-' + lang, 'topics') + 'not found!')
                    continue
                if not os.path.isdir(os.path.join(miracl_corpus, 'miracl-corpus-v1.0-' + lang)):
                    print('Directory: ' + os.path.join(miracl_corpus, 'miracl-corpus-v1.0-' + lang) + ' not found!')
                    continue
                self.tenant_id = "benchmark_miracl_" + lang
                self.index_name = search.index_name(self.tenant_id)
                self.initialized_index = False
                qrels, texts = self.miracl_index(os.path.join(file_path, 'miracl-v1.0-' + lang),
                                                 os.path.join(miracl_corpus, 'miracl-corpus-v1.0-' + lang),
                                                 "benchmark_miracl_" + lang)
                run = self._get_retrieval(qrels)
                print(dataset, evaluate(Qrels(qrels), Run(run), ["ndcg@10", "map@5", "mrr@10"]))
                self.save_results(qrels, run, texts, dataset, file_path)


if __name__ == '__main__':
    print('*****************RAGFlow Benchmark*****************')
    parser = argparse.ArgumentParser(usage="benchmark.py <max_docs> <kb_id> <dataset> <dataset_path> [<miracl_corpus_path>])", description='RAGFlow Benchmark')
    parser.add_argument('max_docs', metavar='max_docs', type=int, help='max docs to evaluate')
    parser.add_argument('kb_id', metavar='kb_id', help='dataset id')
    parser.add_argument('dataset', metavar='dataset', help='dataset name, shall be one of ms_marco_v1.1(https://huggingface.co/datasets/microsoft/ms_marco), trivia_qa(https://huggingface.co/datasets/mandarjoshi/trivia_qa>), miracl(https://huggingface.co/datasets/miracl/miracl')
    parser.add_argument('dataset_path', metavar='dataset_path', help='dataset path')
    parser.add_argument('miracl_corpus_path', metavar='miracl_corpus_path', nargs='?', default="", help='miracl corpus path. Only needed when dataset is miracl')

    args = parser.parse_args()
    max_docs = args.max_docs
    kb_id = args.kb_id
    ex = Benchmark(kb_id)

    dataset = args.dataset
    dataset_path = args.dataset_path

    if dataset == "ms_marco_v1.1" or dataset == "trivia_qa":
        ex(dataset, dataset_path)
    elif dataset == "miracl":
        if len(args) < 5:
            print('Please input the correct parameters!')
            exit(1)
        miracl_corpus_path = args[4]
        ex(dataset, dataset_path, miracl_corpus=args.miracl_corpus_path)
    else:
        print("Dataset: ", dataset, "not supported!")
