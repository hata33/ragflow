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

"""
RAGFlow 常量定义模块

该模块定义了系统中使用的各种常量，包括：
- 名称长度限制
- API 版本
- 文件大小限制
- 各种配置限制
"""

# 名称长度限制（1024字符）
NAME_LENGTH_LIMIT = 2**10

IMG_BASE64_PREFIX = "data:image/png;base64,"

API_VERSION = "v1"
RAG_FLOW_SERVICE_NAME = "ragflow"
REQUEST_WAIT_SEC = 2
REQUEST_MAX_WAIT_SEC = 300

DATASET_NAME_LIMIT = 128
FILE_NAME_LEN_LIMIT = 255
MEMORY_NAME_LIMIT = 128
MEMORY_SIZE_LIMIT = 10*1024*1024 # Byte
