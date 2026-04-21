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
工具模块包

本模块提供 RAGFlow 的各种工具类和函数。

主要功能：
- 存储连接：支持多种存储后端（MinIO、S3、OSS、GCS 等）
- 数据库连接：支持 Elasticsearch、OpenSearch、Infinity
- 缓存连接：Redis 缓存管理
- 加密存储：敏感信息加密
- 文件处理：文件操作工具
- 图像处理：Base64 图像处理
- RAPTOR 工具：层次化检索工具
- Tavily 连接：网络搜索 API 连接

支持的存储后端：
- MinIO：本地对象存储
- S3：Amazon S3 兼容存储
- OSS：阿里云对象存储
- GCS：Google Cloud Storage
- OBS：华为云对象存储
- Azure SAS：Azure 存储
- OpenDAL：统一数据访问层
"""

