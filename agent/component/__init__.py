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
import os
import importlib
import inspect
from types import ModuleType
from typing import Dict, Type

_package_path = os.path.dirname(__file__)
__all_classes: Dict[str, Type] = {}

def _import_submodules() -> None:
    for filename in os.listdir(_package_path): # noqa: F821
        if filename.startswith("__") or not filename.endswith(".py") or filename.startswith("base"):
            continue
        module_name = filename[:-3]

        try:
            module = importlib.import_module(f".{module_name}", package=__name__)
            _extract_classes_from_module(module)  # noqa: F821
        except ImportError as e:
            print(f"Warning: Failed to import module {module_name}: {str(e)}")

def _extract_classes_from_module(module: ModuleType) -> None:
    for name, obj in inspect.getmembers(module):
        if (inspect.isclass(obj) and
                obj.__module__ == module.__name__ and not name.startswith("_")):
            __all_classes[name] = obj
            globals()[name] = obj

_import_submodules()

__all__ = list(__all_classes.keys()) + ["__all_classes"]

del _package_path, _import_submodules, _extract_classes_from_module


def component_class(class_name):
    """
    根据类名动态获取组件类

    这是一个组件类查找函数，通过类名从多个模块中查找并返回对应的组件类。
    支持从以下模块中查找（按优先级顺序）：
    1. agent.component - 核心组件（Begin, Message, LLM, Categorize 等）
    2. agent.tools - 工具组件（外部工具集成）
    3. rag.flow - RAG 流程组件

    Args:
        class_name (str): 要查找的组件类名
                         例如: "Begin", "BeginParam", "LLM", "Message"

    Returns:
        type: 找到的组件类（类型对象）

    Raises:
        AssertionError: 如果在所有模块中都找不到该类

    Example:
        >>> # 获取 Begin 组件类
        >>> BeginClass = component_class("Begin")
        >>> begin_instance = BeginClass(canvas, "begin_id", param)
        >>>
        >>> # 获取 BeginParam 参数类
        >>> ParamClass = component_class("BeginParam")
        >>> param = ParamClass()

    Note:
        此函数利用了 Python 的动态导入机制，允许在运行时按需加载组件类，
        而不需要在代码中显式导入每个组件。这种设计使得添加新组件时不需要
        修改核心代码，只需在对应的模块目录中创建新的组件文件即可。
    """
    # 按优先级顺序在各个模块中查找组件类
    for module_name in ["agent.component", "agent.tools", "rag.flow"]:
        try:
            # 动态导入模块并获取指定名称的类
            return getattr(importlib.import_module(module_name), class_name)
        except Exception:
            # 如果在当前模块中找不到，继续尝试下一个模块
            # logging.warning(f"Can't import module: {module_name}, error: {e}")
            pass

    # 如果在所有模块中都找不到该类，抛出断言错误
    assert False, f"Can't import {class_name}"
