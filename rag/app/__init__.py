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
RAG 应用模块包

本模块包含针对不同文档类型的应用模板，每种模板针对特定类型的文档
进行了优化，提供更好的解析和分块效果。

可用的应用模板：
- naive: 通用文档解析（支持 PDF、DOCX、Excel、TXT、Markdown、HTML 等）
- audio: 音频文件解析（语音转文字）
- book: 书籍文档解析（支持层次化结构）
- email: 邮件文件解析（EML 格式）
- laws: 法律文档解析（支持条款结构）
- manual: 手册文档解析（问答格式）
- one: 单文档解析（整个文档作为一个块）
- paper: 论文文档解析（提取标题、作者、摘要）
- picture: 图片解析（OCR + 视觉模型描述）
- presentation: 演示文稿解析（PPT、PPTX、PDF）
- qa: 问答文档解析（Excel、CSV、PDF、Markdown）
- resume: 简历文档解析（结构化信息提取）
- table: 表格文档解析（Excel、CSV）
- tag: 标签文档解析（内容 + 标签）

使用方式：
    from rag.app import naive
    chunks = naive.chunk(filename, binary, lang="Chinese")
"""