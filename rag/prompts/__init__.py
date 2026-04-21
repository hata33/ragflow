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
提示词模块包

本模块提供 LLM 提示词模板和生成功能。

主要功能：
- 提示词模板加载和管理
- 消息格式化
- 检索结果格式化
- Token 计数和截断
- 流式输出处理

子模块：
- generator: 提示词生成器
- template: 模板加载器

提示词文件：
- *.md: 各种场景的提示词模板
"""

from . import generator

__all__ = [name for name in dir(generator)
           if not name.startswith('_')]

globals().update({name: getattr(generator, name) for name in __all__})
