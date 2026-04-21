categorize.py
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
import asyncio
import logging
import os
import re
from abc import ABC

from common.constants import LLMType
from api.db.services.llm_service import LLMBundle
from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
from agent.component.llm import LLMParam, LLM
from common.connection_utils import timeout
from rag.llm.chat_model import ERROR_PREFIX


class CategorizeParam(LLMParam):

    """
    Categorize组件参数定义类
    定义分类组件的各种参数配置
    """
    def __init__(self):
        super().__init__()
        # 分类描述字典，包含分类名称和对应的描述信息
        self.category_description = {}
        # 查询字段，默认为"sys.query"
        self.query = "sys.query"
        # 消息历史窗口大小
        self.message_history_window_size = 1
        # 更新提示词
        self.update_prompt()

    def check(self):
        """
        检查参数的有效性
        """
        # 检查消息历史窗口大小是否为正整数
        self.check_positive_integer(self.message_history_window_size, "[Categorize] Message window size > 0")
        # 检查分类描述是否为空
        self.check_empty(self.category_description, "[Categorize] Category examples")
        # 检查每个分类的配置
        for k, v in self.category_description.items():
            if not k:
                raise ValueError("[Categorize] Category name can not be empty!")
            if not v.get("to"):
                raise ValueError(f"[Categorize] 'To' of category {k} can not be empty!")

    def get_input_form(self) -> dict[str, dict]:
        """
        获取输入表单定义
        返回组件输入字段的配置信息
        """
        return {
            "query": {
                "type": "line",  # 输入类型为单行文本
                "name": "Query"  # 显示名称为Query
            }
        }

    def update_prompt(self):
        """
        更新系统提示词
        根据分类描述生成用于LLM的分类提示词
        """
        # 构建分类示例行
        cate_lines = []
        for c, desc in self.category_description.items():
            for line in desc.get("examples", []):
                if not line:
                    continue
                # 格式化示例：将换行符替换为四个空格，并添加分类标签
                cate_lines.append("USER: \"" + re.sub(r"\n", "    ", line, flags=re.DOTALL) + "\" → "+c)

        # 构建分类描述
        descriptions = []
        for c, desc in self.category_description.items():
            if desc.get("description"):
                descriptions.append(
                    "\n------\nCategory: {}\nDescription: {}".format(c, desc["description"]))

        # 构建系统提示词
        self.sys_prompt = """
You are an advanced classification system that categorizes user questions into specific types. Analyze the input question and classify it into ONE of the following categories:
{}

Here's description of each category:
 - {}

---- Instructions ----
 - Consider both explicit mentions and implied context
 - Prioritize the most specific applicable category
 - Return only the category name without explanations
 - Use "Other" only when no other category fits

 """.format(
            "\n - ".join(list(self.category_description.keys())),  # 所有分类名称
            "\n".join(descriptions)  # 所有分类描述
        )

        # 如果有示例，则添加到提示词中
        if cate_lines:
            self.sys_prompt += """
---- Examples ----
{}
""".format("\n".join(cate_lines))


class Categorize(LLM, ABC):
    """
    Categorize组件实现类
    继承自LLM类，实现具体的分类逻辑
    """
    component_name = "Categorize"

    def get_input_elements(self) -> dict[str, dict]:
        """
        获取输入元素
        从查询字段中提取变量引用信息
        """
        query_key = self._param.query or "sys.query"
        elements = self.get_input_elements_from_text(f"{{{query_key}}}")
        if not elements:
            logging.warning(f"[Categorize] input element not detected for query key: {query_key}")
        return elements

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10*60)))
    async def _invoke_async(self, **kwargs):
        """
        异步执行分类组件的主要逻辑
        使用LLM对输入的查询进行分类
        """
        if self.check_if_canceled("Categorize processing"):
            return

        # 获取历史消息
        msg = self._canvas.get_history(self._param.message_history_window_size)
        if not msg:
            msg = [{"role": "user", "content": ""}]  # 如果没有历史消息，创建默认消息
        
        # 获取查询键和值
        query_key = self._param.query or "sys.query"
        if query_key in kwargs:
            query_value = kwargs[query_key]
        else:
            query_value = self._canvas.get_variable_value(query_key)
        if query_value is None:
            query_value = ""
        
        # 更新消息内容
        msg[-1]["content"] = query_value
        self.set_input_value(query_key, msg[-1]["content"])
        
        # 更新提示词
        self._param.update_prompt()
        
        # 获取聊天模型配置
        chat_model_config = get_model_config_by_type_and_name(self._canvas.get_tenant_id(), LLMType.CHAT, self._param.llm_id)
        chat_mdl = LLMBundle(self._canvas.get_tenant_id(), chat_model_config)

        # 构建用户提示词
        user_prompt = """
---- Real Data ----
{} →
""".format(" | ".join(["{}: \"{}\"".format(c["role"].upper(), re.sub(r"\n", "", c["content"], flags=re.DOTALL)) for c in msg]))

        if self.check_if_canceled("Categorize processing"):
            return

        # 调用LLM进行分类
        ans = await chat_mdl.async_chat(self._param.sys_prompt, [{"role": "user", "content": user_prompt}], self._param.gen_conf())
        logging.info(f"input: {user_prompt}, answer: {str(ans)}")
        if ERROR_PREFIX in ans:
            raise Exception(ans)

        if self.check_if_canceled("Categorize processing"):
            return

        # 统计答案中每个分类出现的次数
        category_counts = {}
        for c in self._param.category_description.keys():
            count = ans.lower().count(c.lower())
            category_counts[c] = count

        # 获取默认分类（最后一个分类）
        cpn_ids = list(self._param.category_description.items())[-1][1]["to"]
        max_category = list(self._param.category_description.keys())[-1]
        if any(category_counts.values()):
            # 找到出现次数最多的分类
            max_category = max(category_counts.items(), key=lambda x: x[1])[0]
            cpn_ids = self._param.category_description[max_category]["to"]

        # 设置输出
        self.set_output("category_name", max_category)
        self.set_output("_next", cpn_ids)

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10*60)))
    def _invoke(self, **kwargs):
        """
        同步执行方法
        调用异步方法并等待结果
        """
        return asyncio.run(self._invoke_async(**kwargs))

    def thoughts(self) -> str:
        """
        返回组件的思考过程
        用于展示分类组件的思考过程
        """
        return "Which should it falls into {}? ...".format(",".join([f"`{c}`" for c, _ in self._param.category_description.items()]))