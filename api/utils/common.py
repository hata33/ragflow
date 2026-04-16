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
通用工具函数模块

该模块提供系统中常用的通用工具函数。

主要功能：
- 字符串和字节数组之间的转换
- 数据哈希计算
"""

import xxhash


def string_to_bytes(string):
    """
    将字符串转换为字节数组

    参数：
        string: 字符串或字节数组

    返回：
        如果输入是字节数组则直接返回，否则转换为 UTF-8 编码的字节数组
    """
    return string if isinstance(
        string, bytes) else string.encode(encoding="utf-8")


def bytes_to_string(byte):
    """
    将字节数组转换为字符串

    参数：
        byte: 字节数组

    返回：
        UTF-8 解码后的字符串
    """
    return byte.decode(encoding="utf-8")


# 128 bit = 32 character
def hash128(data: str) -> str:
    """
    计算数据的 128 位哈希值

    使用 xxHash 算法计算输入数据的 128 位哈希值。

    参数：
        data: 要计算哈希的字符串

    返回：
        32 个字符的十六进制哈希字符串
    """
    return xxhash.xxh128(data).hexdigest()
