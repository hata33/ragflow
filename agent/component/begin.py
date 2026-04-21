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
Begin 组件 - Agent 工作流的入口组件

该组件定义了工作流的起点，负责：
1. 接收用户输入（对话模式、任务模式或 Webhook）
2. 处理文件上传
3. 初始化工作流的输入参数
"""

# 导入父类：UserFillUpParam（参数基类）和 UserFillUp（组件基类）
from agent.component.fillup import UserFillUpParam, UserFillUp
# 导入文件服务，用于处理上传的文件
from api.db.services.file_service import FileService


class BeginParam(UserFillUpParam):
    """
    Begin 组件的参数定义类

    继承自 UserFillUpParam，定义了 Begin 组件的特有参数：
    - mode: 运行模式（对话/任务/Webhook）
    - prologue: 开场白
    - inputs: 用户输入参数定义
    """

    def __init__(self):
        """
        初始化 Begin 组件的默认参数
        """
        super().__init__()
        # 运行模式：conversational（对话模式）、task（任务模式）、Webhook
        self.mode = "conversational"
        # 开场白：对话开始时显示的欢迎消息
        self.prologue = "Hi! I'm your smart assistant. What can I do for you?"

    def check(self):
        """
        验证参数的有效性

        检查 mode 参数是否在允许的值范围内
        """
        # 验证 mode 必须是以下三种之一
        self.check_valid_value(
            self.mode,
            "The 'mode' should be either `conversational` or `task`",
            ["conversational", "task", "Webhook"]
        )

    def get_input_form(self) -> dict[str, dict]:
        """
        获取输入表单定义

        返回用户需要填写的输入字段配置，用于前端渲染输入表单

        Returns:
            dict[str, dict]: 输入字段的配置字典
        """
        return getattr(self, "inputs")


class Begin(UserFillUp):
    """
    Begin 组件的实现类

    这是 Agent 工作流的入口组件，负责：
    1. 接收和处理用户输入
    2. 处理上传的文件
    3. 将处理后的数据传递给下游组件
    """

    component_name = "Begin"  # 组件名称，用于组件注册和识别

    def _invoke(self, **kwargs):
        """
        组件的执行方法

        该方法在工作流执行时被调用，负责：
        1. 检查任务是否被取消
        2. 处理用户输入
        3. 处理文件上传（如有）
        4. 设置输出和输入值

        Args:
            **kwargs: 包含 inputs 等参数的字典
                - inputs: 用户输入的参数字典
        """
        # 检查任务是否已被用户取消，如果取消则直接返回
        if self.check_if_canceled("Begin processing"):
            return

        # 获取布局识别参数，用于解析 PDF 等文件的版面结构
        layout_recognize = self._param.layout_recognize or None

        # 遍历用户输入的所有参数
        for k, v in kwargs.get("inputs", {}).items():
            # 每次循环都检查任务是否被取消
            if self.check_if_canceled("Begin processing"):
                return

            # 判断当前输入是否为文件类型
            # 文件类型的输入是一个字典，包含 type、value、optional 等字段
            if isinstance(v, dict) and v.get("type", "").lower().find("file") >= 0:
                # 处理文件输入
                if v.get("optional") and v.get("value", None) is None:
                    # 如果是可选字段且没有提供值，设为 None
                    v = None
                else:
                    # 获取文件值
                    file_value = v["value"]
                    # 兼容单个文件和多个文件的情况
                    # 向后兼容：如果是单个文件，转换为列表
                    files = file_value if isinstance(file_value, list) else [file_value]
                    # 调用文件服务解析文件内容
                    # layout_recognize 用于识别 PDF 的版面结构
                    v = FileService.get_files(files, layout_recognize=layout_recognize)
            else:
                # 非文件类型，直接获取 value 字段的值
                v = v.get("value")

            # 将处理后的值设置为组件的输出
            # 其他组件可以通过 {begin@参数名} 引用这个输出
            self.set_output(k, v)

            # 同时设置为输入值，用于表单回显
            self.set_input_value(k, v)

    def thoughts(self) -> str:
        """
        返回组件的思考过程

        Begin 组件不需要思考过程，返回空字符串

        Returns:
            str: 空字符串
        """
        return ""