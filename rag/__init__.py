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
RAG 模块包初始化文件

本模块是 RAGFlow 的核心 RAG (Retrieval-Augmented Generation) 引擎，
提供检索增强生成的完整实现。

主要子模块：
- app: 各种文档类型的应用模板
- flow: 文档处理流水线（分块、解析、提取、标记化）
- llm: 大语言模型集成（对话、嵌入、重排序等）
- nlp: 自然语言处理工具
- graphrag: 图谱检索增强生成
- prompts: 提示词生成和模板
- svr: 后台服务（缓存、同步、任务执行）
- utils: 工具类（存储连接、加密等）
- advanced_rag: 高级 RAG 技术
"""

# from beartype.claw import beartype_this_package
# beartype_this_package()
