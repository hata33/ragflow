"""
Part 2 of db_models.py - Database Models Definition (Lines 1084-2159)
数据库模型定义第二部分 - 从User模型到迁移函数

This file contains all the database model classes used in RAGFlow.
包含RAGFlow中使用的所有数据库模型类。
"""

# ==============================================================================
# User Model - 用户模型
# ==============================================================================

class User(DataBaseModel, AuthUser):  # Line 1084
    """
    用户模型 | User Model

    存储系统用户信息，包括认证信息、个人偏好设置等。
    Stores system user information including authentication details and personal preferences.

    Attributes:
        id: 用户唯一标识 | Unique user identifier (32 chars, primary key)
        access_token: 访问令牌，用于API认证 | Access token for API authentication (255 chars, indexed)
        nickname: 用户昵称 | User's display nickname (100 chars, required, indexed)
        password: 密码（加密存储） | Password (encrypted storage) (255 chars, indexed)
        email: 邮箱地址（唯一） | Email address (unique, 255 chars, required)
        avatar: 头像（Base64 编码） | Profile avatar (Base64 encoded string, text field)
        language: 语言偏好（English/Chinese） | Language preference (32 chars, defaults based on system locale)
        color_schema: 颜色主题（Bright/Dark） | Color theme (32 chars, defaults to "Bright")
        timezone: 时区设置 | Timezone setting (64 chars, defaults to "UTC+8\tAsia/Shanghai")
        last_login_time: 最后登录时间 | Last login timestamp (datetime, indexed)
        is_authenticated: 是否已认证 | Authentication status (1 char, defaults to "1")
        is_active: 是否激活 | Account active status (1 char, defaults to "1")
        is_anonymous: 是否匿名用户 | Anonymous user flag (1 char, defaults to "0")
        login_channel: 登录渠道 | Login channel/source (indexed)
        status: 状态（0=无效，1=有效） | Status (0=invalid, 1=valid, defaults to "1")
        is_superuser: 是否超级用户 | Superuser flag (boolean, defaults to False)

    Inherited from AuthUser for authentication framework integration.
    继承自AuthUser以集成认证框架。
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1108
    access_token = CharField(max_length=255, null=True, index=True)
    nickname = CharField(max_length=100, null=False, help_text="nicky name", index=True)
    password = CharField(max_length=255, null=True, help_text="password", index=True)
    email = CharField(max_length=255, null=False, help_text="email", unique=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    # 根据系统语言环境设置默认语言 | Sets default language based on system locale
    language = CharField(max_length=32, null=True, help_text="English|Chinese", default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", index=True)
    color_schema = CharField(max_length=32, null=True, help_text="Bright|Dark", default="Bright", index=True)
    timezone = CharField(max_length=64, null=True, help_text="Timezone", default="UTC+8\tAsia/Shanghai", index=True)
    last_login_time = DateTimeField(null=True, index=True)
    is_authenticated = CharField(max_length=1, null=False, default="1", index=True)
    is_active = CharField(max_length=1, null=False, default="1", index=True)
    is_anonymous = CharField(max_length=1, null=False, default="0", index=True)
    login_channel = CharField(null=True, help_text="from which user login", index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)
    is_superuser = BooleanField(null=True, help_text="is root", default=False, index=True)

    def __str__(self):  # Line 1125
        """返回用户的邮箱地址作为字符串表示 | Returns user email as string representation"""
        return self.email

    def get_id(self):  # Line 1129
        """
        获取用户 ID 的 JWT 令牌表示 | Get JWT token representation of user ID

        使用 itsdangerous 库将 access_token 序列化为带时间戳的 JWT 令牌。
        Uses itsdangerous library to serialize access_token as a timestamped JWT token.

        Returns:
            str: JWT 令牌字符串 | JWT token string

        Note:
            This method is required by the authentication framework for session management.
            此方法是认证框架进行会话管理所必需的。
        """
        jwt = Serializer(secret_key=settings.SECRET_KEY)  # 使用系统密钥初始化JWT序列化器
        return jwt.dumps(str(self.access_token))  # 将access_token序列化为JWT令牌

    class Meta:  # Line 1140
        db_table = "user"  # 数据库表名 | Database table name


# ==============================================================================
# Tenant Model - 租户模型
# ==============================================================================

class Tenant(DataBaseModel):  # Line 1144
    """
    租户模型 | Tenant Model

    多租户系统的租户信息，每个租户可以有自己的配置和资源配额。
    Tenant information for multi-tenant system, each tenant can have own configuration and resource quota.

    Attributes:
        id: 租户唯一标识 | Unique tenant identifier (32 chars, primary key)
        name: 租户名称 | Tenant display name (100 chars, indexed)
        public_key: 公钥 | Public key for encryption (255 chars, indexed)
        llm_id: 默认 LLM 模型 ID | Default LLM model ID (128 chars, required, indexed)
        tenant_llm_id: 租户 LLM 配置 ID | Tenant's LLM configuration ID in tenant_llm table (integer, indexed)
        embd_id: 默认嵌入模型 ID | Default embedding model ID (128 chars, required, indexed)
        tenant_embd_id: 租户嵌入模型配置 ID | Tenant's embedding model configuration ID (integer, indexed)
        asr_id: 默认 ASR 模型 ID | Default ASR (Automatic Speech Recognition) model ID (128 chars, required)
        tenant_asr_id: 租户 ASR 模型配置 ID | Tenant's ASR model configuration ID (integer, indexed)
        img2txt_id: 默认图像转文本模型 ID | Default image-to-text model ID (128 chars, required)
        tenant_img2txt_id: 租户图像转文本模型配置 ID | Tenant's image-to-text configuration ID (integer, indexed)
        rerank_id: 默认重排序模型 ID | Default rerank model ID (128 chars, required, indexed)
        tenant_rerank_id: 租户重排序模型配置 ID | Tenant's rerank model configuration ID (integer, indexed)
        tts_id: 默认 TTS 模型 ID | Default TTS (Text-to-Speech) model ID (256 chars, nullable)
        tenant_tts_id: 租户 TTS 模型配置 ID | Tenant's TTS model configuration ID (integer, indexed)
        parser_ids: 文档解析器列表 | Document parser IDs list (256 chars, required, indexed)
        credit: 租户配额 | Tenant quota/credits (integer, defaults to 512, indexed)
        status: 状态（0=无效，1=有效） | Status (0=invalid, 1=valid, defaults to "1")

    This model enables multi-tenancy by isolating resources and configurations per tenant.
    此模型通过为每个租户隔离资源和配置来实现多租户功能。
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1170
    name = CharField(max_length=100, null=True, help_text="Tenant name", index=True)
    public_key = CharField(max_length=255, null=True, index=True)
    llm_id = CharField(max_length=128, null=False, help_text="default llm ID", index=True)
    tenant_llm_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    embd_id = CharField(max_length=128, null=False, help_text="default embedding model ID", index=True)
    tenant_embd_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    asr_id = CharField(max_length=128, null=False, help_text="default ASR model ID", index=True)
    tenant_asr_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    img2txt_id = CharField(max_length=128, null=False, help_text="default image to text model ID", index=True)
    tenant_img2txt_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    rerank_id = CharField(max_length=128, null=False, help_text="default rerank model ID", index=True)
    tenant_rerank_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    tts_id = CharField(max_length=256, null=True, help_text="default tts model ID", index=True)
    tenant_tts_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    parser_ids = CharField(max_length=256, null=False, help_text="document processors", index=True)
    credit = IntegerField(default=512, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:  # Line 1189
        db_table = "tenant"  # 数据库表名 | Database table name


# ==============================================================================
# UserTenant Model - 用户-租户关联模型
# ==============================================================================

class UserTenant(DataBaseModel):  # Line 1193
    """
    用户-租户关联模型 | User-Tenant Association Model

    多对多关系表，记录用户与租户的关联关系及用户角色。
    Many-to-many relationship table recording user-tenant associations and user roles.

    Attributes:
        id: 关联记录唯一标识 | Unique association record identifier (32 chars, primary key)
        user_id: 用户 ID | User identifier (32 chars, required, indexed)
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed)
        role: 用户角色（UserTenantRole） | User role (32 chars, required, indexed)
        invited_by: 邀请人 ID | ID of user who sent the invitation (32 chars, required, indexed)
        status: 状态（0=无效，1=有效） | Status (0=invalid, 1=valid, defaults to "1")

    This table enables users to belong to multiple tenants with different roles.
    此表允许用户以不同角色属于多个租户。
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1207
    user_id = CharField(max_length=32, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    role = CharField(max_length=32, null=False, help_text="UserTenantRole", index=True)
    invited_by = CharField(max_length=32, null=False, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:  # Line 1214
        db_table = "user_tenant"  # 数据库表名 | Database table name


# ==============================================================================
# InvitationCode Model - 邀请码模型
# ==============================================================================

class InvitationCode(DataBaseModel):  # Line 1218
    """
    邀请码模型 | Invitation Code Model

    用于管理用户加入租户的邀请码。
    Manages invitation codes for users to join tenants.

    Attributes:
        id: 邀请码唯一标识 | Unique invitation code identifier (32 chars, primary key)
        code: 邀请码 | The invitation code string (32 chars, required, indexed)
        visit_time: 访问时间 | When the code was visited/used (datetime, indexed)
        user_id: 使用邀请码的用户 ID | ID of user who used the code (32 chars, nullable, indexed)
        tenant_id: 目标租户 ID | Target tenant ID for the invitation (32 chars, nullable, indexed)
        status: 状态（0=无效，1=有效） | Status (0=invalid, 1=valid, defaults to "1")

    Invitation codes provide a controlled way to onboard users to specific tenants.
    邀请码提供了一种受控的方式将用户加入特定租户。
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1232
    code = CharField(max_length=32, null=False, index=True)
    visit_time = DateTimeField(null=True, index=True)
    user_id = CharField(max_length=32, null=True, index=True)
    tenant_id = CharField(max_length=32, null=True, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:  # Line 1239
        db_table = "invitation_code"  # 数据库表名 | Database table name


# ==============================================================================
# LLMFactories Model - LLM 厂商模型
# ==============================================================================

class LLMFactories(DataBaseModel):  # Line 1243
    """
    LLM 厂商模型 | LLM Factory/Provider Model

    存储支持的大语言模型厂商信息。
    Stores information about supported LLM providers/factories.

    Attributes:
        name: 厂商名称（主键） | Factory/provider name (128 chars, primary key)
        logo: 厂商 Logo（Base64 编码） | Provider logo (Base64 encoded text)
        tags: 标签（LLM, Text Embedding, Image2Text, ASR） | Service tags (255 chars, required, indexed)
        rank: 排序权重 | Display order rank (integer, defaults to 0)
        status: 状态（0=无效，1=有效） | Status (0=invalid, 1=valid, defaults to "1", indexed)

    Examples of factories include OpenAI, Anthropic, Azure, etc.
    厂商示例包括 OpenAI、Anthropic、Azure 等。
    """
    name = CharField(max_length=128, null=False, help_text="LLM factory name", primary_key=True)  # Line 1256
    logo = TextField(null=True, help_text="llm logo base64")
    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    rank = IntegerField(default=0, index=False)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):  # Line 1262
        """返回厂商名称 | Returns factory name"""
        return self.name

    class Meta:  # Line 1266
        db_table = "llm_factories"  # 数据库表名 | Database table name


# ==============================================================================
# LLM Model - 大语言模型模型
# ==============================================================================

class LLM(DataBaseModel):  # Line 1270
    """
    大语言模型模型 | Large Language Model Model

    存储系统支持的各类 AI 模型信息。
    Stores information about various AI models supported by the system.

    Attributes:
        llm_name: 模型名称 | Model name (128 chars, required, indexed)
        model_type: 模型类型（LLM, Text Embedding, Image2Text, ASR） | Model category (128 chars, required, indexed)
        fid: 厂商 ID | Factory/provider ID (128 chars, required, indexed)
        max_tokens: 最大 token 数 | Maximum token context length (integer, defaults to 0)
        tags: 标签 | Additional tags (255 chars, required, indexed)
        is_tools: 是否支持工具调用 | Whether model supports function calling (boolean, defaults to False)
        status: 状态（0=无效，1=有效） | Status (0=invalid, 1=valid, defaults to "1", indexed)

    Uses composite primary key of factory + model name for uniqueness.
    使用厂商+模型名称的复合主键确保唯一性。
    """
    # LLMs dictionary | LLM 字典
    llm_name = CharField(max_length=128, null=False, help_text="LLM name", index=True)  # Line 1286
    model_type = CharField(max_length=128, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    fid = CharField(max_length=128, null=False, help_text="LLM factory id", index=True)
    max_tokens = IntegerField(default=0)

    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, Chat, 32k...", index=True)
    is_tools = BooleanField(null=False, help_text="support tools", default=False)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):  # Line 1295
        """返回模型名称 | Returns model name"""
        return self.llm_name

    class Meta:  # Line 1299
        # 复合主键：厂商 + 模型名称 | Composite primary key: factory + model name
        primary_key = CompositeKey("fid", "llm_name")
        db_table = "llm"  # 数据库表名 | Database table name


# ==============================================================================
# TenantLLM Model - 租户 LLM 配置模型
# ==============================================================================

class TenantLLM(DataBaseModel):  # Line 1305
    """
    租户 LLM 配置模型 | Tenant LLM Configuration Model

    存储租户自定义的 LLM 配置，包括 API 密钥等。
    Stores tenant-specific LLM configurations including API keys.

    Attributes:
        id: 配置记录 ID（主键，自增） | Configuration record ID (auto-increment primary key)
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed)
        llm_factory: LLM 厂商名称 | LLM factory name (128 chars, required, indexed)
        model_type: 模型类型 | Model type (128 chars, nullable, indexed)
        llm_name: 模型名称 | Model name (128 chars, defaults to empty string, indexed)
        api_key: API 密钥 | API key for the model service (text field, nullable)
        api_base: API 基础 URL | API base URL (255 chars, nullable)
        max_tokens: 最大上下文 token 数 | Maximum context tokens (integer, defaults to 8192, indexed)
        used_tokens: 已使用 token 数 | Token usage counter (integer, defaults to 0, indexed)
        status: 状态（0=无效，1=有效） | Status (0=invalid, 1=valid, defaults to "1", indexed)

    Has a unique constraint on (tenant_id, llm_factory, llm_name) to prevent duplicates.
    对 (tenant_id, llm_factory, llm_name) 有唯一约束以防止重复。
    """
    id = PrimaryKeyField()  # Line 1323
    tenant_id = CharField(max_length=32, null=False, index=True)
    llm_factory = CharField(max_length=128, null=False, help_text="LLM factory name", index=True)
    model_type = CharField(max_length=128, null=True, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    llm_name = CharField(max_length=128, null=True, help_text="LLM name", default="", index=True)
    api_key = TextField(null=True, help_text="API KEY")
    api_base = CharField(max_length=255, null=True, help_text="API Base")
    max_tokens = IntegerField(default=8192, help_text="Max context token num", index=True)
    used_tokens = IntegerField(default=0, help_text="Used token num", index=True)
    status = CharField(max_length=1, null=False, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):  # Line 1334
        """返回模型名称 | Returns model name"""
        return self.llm_name

    class Meta:  # Line 1338
        db_table = "tenant_llm"  # 数据库表名 | Database table name
        # 唯一索引：租户 + 厂商 + 模型名称 | Unique index: tenant + factory + model name
        indexes = (
            (("tenant_id", "llm_factory", "llm_name"), True),
        )


# ==============================================================================
# TenantLangfuse Model - 租户 Langfuse 配置模型
# ==============================================================================

class TenantLangfuse(DataBaseModel):  # Line 1346
    """
    租户 Langfuse 配置模型 | Tenant Langfuse Configuration Model

    存储 Langfuse 可观测性平台的配置信息。
    Stores Langfuse observability platform configuration.

    Attributes:
        tenant_id: 租户 ID（主键） | Tenant identifier (32 chars, primary key)
        secret_key: 密钥 | Secret key for Langfuse (2048 chars, required, indexed)
        public_key: 公钥 | Public key for Langfuse (2048 chars, required, indexed)
        host: 主机地址 | Langfuse server host (128 chars, required, indexed)
    """
    tenant_id = CharField(max_length=32, null=False, primary_key=True)  # Line 1347
    secret_key = CharField(max_length=2048, null=False, help_text="SECRET KEY", index=True)
    public_key = CharField(max_length=2048, null=False, help_text="PUBLIC KEY", index=True)
    host = CharField(max_length=128, null=False, help_text="HOST", index=True)

    def __str__(self):  # Line 1352
        """返回 Langfuse 主机信息 | Returns Langfuse host information"""
        return "Langfuse host" + self.host

    class Meta:  # Line 1355
        db_table = "tenant_langfuse"  # 数据库表名 | Database table name


# ==============================================================================
# Knowledgebase Model - 知识库模型
# ==============================================================================

class Knowledgebase(DataBaseModel):  # Line 1359
    """
    知识库模型 | Knowledge Base Model

    RAG系统的核心模型，存储知识库的配置和元数据。
    Core model of RAG system, storing knowledge base configuration and metadata.

    Attributes:
        id: 知识库唯一标识 | Unique KB identifier (32 chars, primary key)
        avatar: 头像 | KB avatar (Base64 encoded, text field, nullable)
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed)
        name: 知识库名称 | KB display name (128 chars, required, indexed)
        language: 语言 | Language setting (32 chars, defaults based on system locale, indexed)
        description: 描述 | KB description (text field, nullable)
        embd_id: 默认嵌入模型 ID | Default embedding model ID (128 chars, required, indexed)
        tenant_embd_id: 租户嵌入模型配置 ID | Tenant's embedding model config ID (integer, indexed)
        permission: 权限 | Permission level (16 chars, "me" or "team", defaults to "me", indexed)
        created_by: 创建者 ID | Creator user ID (32 chars, required, indexed)
        doc_num: 文档数量 | Number of documents (integer, defaults to 0, indexed)
        token_num: token 数量 | Total token count (integer, defaults to 0, indexed)
        chunk_num: 切片数量 | Number of chunks (integer, defaults to 0, indexed)
        similarity_threshold: 相似度阈值 | Similarity threshold for retrieval (float, defaults to 0.2, indexed)
        vector_similarity_weight: 向量相似度权重 | Vector similarity weight in hybrid search (float, defaults to 0.3, indexed)
        parser_id: 默认解析器 ID | Default parser ID (32 chars, required, defaults to NAIVE, indexed)
        pipeline_id: 处理流水线 ID | Processing pipeline ID (32 chars, nullable, indexed)
        parser_config: 解析器配置 | Parser configuration JSON (JSON field, defaults to page settings)
        pagerank: PageRank 值 | PageRank score for graph-based ranking (integer, defaults to 0)
        graphrag_task_id: Graph RAG 任务 ID | Graph RAG task ID (32 chars, nullable, indexed)
        graphrag_task_finish_at: Graph RAG 完成时间 | Graph RAG completion timestamp (datetime, nullable)
        raptor_task_id: RAPTOR 任务 ID | RAPTOR hierarchical summarization task ID (32 chars, nullable, indexed)
        raptor_task_finish_at: RAPTOR 完成时间 | RAPTOR completion timestamp (datetime, nullable)
        mindmap_task_id: 思维导图任务 ID | Mindmap generation task ID (32 chars, nullable, indexed)
        mindmap_task_finish_at: 思维导图完成时间 | Mindmap completion timestamp (datetime, nullable)
        status: 状态 | Status (0=invalid, 1=valid, defaults to "1", indexed)

    This is the main entity for RAG operations, defining how documents are stored and retrieved.
    这是RAG操作的主要实体，定义文档的存储和检索方式。
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1360
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="KB name", index=True)
    language = CharField(max_length=32, null=True, default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", help_text="English|Chinese", index=True)
    description = TextField(null=True, help_text="KB description")
    embd_id = CharField(max_length=128, null=False, help_text="default embedding model ID", index=True)
    tenant_embd_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    permission = CharField(max_length=16, null=False, help_text="me|team", default="me", index=True)
    created_by = CharField(max_length=32, null=False, index=True)
    doc_num = IntegerField(default=0, index=True)
    token_num = IntegerField(default=0, index=True)
    chunk_num = IntegerField(default=0, index=True)
    similarity_threshold = FloatField(default=0.2, index=True)
    vector_similarity_weight = FloatField(default=0.3, index=True)

    parser_id = CharField(max_length=32, null=False, help_text="default parser ID", default=ParserType.NAIVE.value, index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="Pipeline ID", index=True)
    parser_config = JSONField(null=False, default={"pages": [[1, 1000000]], "table_context_size": 0, "image_context_size": 0})
    pagerank = IntegerField(default=0, index=False)

    graphrag_task_id = CharField(max_length=32, null=True, help_text="Graph RAG task ID", index=True)
    graphrag_task_finish_at = DateTimeField(null=True)
    raptor_task_id = CharField(max_length=32, null=True, help_text="RAPTOR task ID", index=True)
    raptor_task_finish_at = DateTimeField(null=True)
    mindmap_task_id = CharField(max_length=32, null=True, help_text="Mindmap task ID", index=True)
    mindmap_task_finish_at = DateTimeField(null=True)

    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):  # Line 1390
        """返回知识库名称 | Returns KB name"""
        return self.name

    class Meta:  # Line 1393
        db_table = "knowledgebase"  # 数据库表名 | Database table name


# ==============================================================================
# Document Model - 文档模型
# ==============================================================================

class Document(DataBaseModel):  # Line 1397
    """
    文档模型 | Document Model

    存储知识库中的文档信息和处理状态。
    Stores document information and processing status in knowledge bases.

    Attributes:
        id: 文档唯一标识 | Unique document identifier (32 chars, primary key)
        thumbnail: 缩略图 | Document thumbnail (Base64 encoded, text field, nullable)
        kb_id: 知识库 ID | Knowledge base ID (256 chars, required, indexed)
        parser_id: 解析器 ID | Parser ID to use (32 chars, required, indexed)
        pipeline_id: 处理流水线 ID | Processing pipeline ID (32 chars, nullable, indexed)
        parser_config: 解析器配置 | Parser configuration JSON (JSON field, defaults to page settings)
        source_type: 来源类型 | Document source (128 chars, defaults to "local", indexed)
        type: 文件类型 | File extension/type (32 chars, required, indexed)
        created_by: 创建者 ID | Creator user ID (32 chars, required, indexed)
        name: 文件名 | File name (255 chars, nullable, indexed)
        location: 存储位置 | Storage location (255 chars, nullable, indexed)
        size: 文件大小 | File size in bytes (integer, defaults to 0, indexed)
        token_num: token 数量 | Token count (integer, defaults to 0, indexed)
        chunk_num: 切片数量 | Number of chunks (integer, defaults to 0, indexed)
        progress: 处理进度 | Processing progress 0-1 (float, defaults to 0, indexed)
        progress_msg: 进度消息 | Progress message (text field, defaults to empty string)
        process_begin_at: 处理开始时间 | Processing start timestamp (datetime, nullable, indexed)
        process_duration: 处理耗时 | Processing duration in seconds (float, defaults to 0)
        suffix: 文件后缀 | Real file extension (32 chars, required, indexed)
        content_hash: 内容哈希 | xxhash128 for change detection (32 chars, defaults to empty string, indexed)
        run: 运行标志 | Run/cancel flag (0=none, 1=run, 2=cancel, defaults to "0", indexed)
        status: 状态 | Status (0=invalid, 1=valid, defaults to "1", indexed)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1398
    thumbnail = TextField(null=True, help_text="thumbnail base64 string")
    kb_id = CharField(max_length=256, null=False, index=True)
    parser_id = CharField(max_length=32, null=False, help_text="default parser ID", index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="pipeline ID", index=True)
    parser_config = JSONField(null=False, default={"pages": [[1, 1000000]], "table_context_size": 0, "image_context_size": 0})
    source_type = CharField(max_length=128, null=False, default="local", help_text="where dose this document come from", index=True)
    type = CharField(max_length=32, null=False, help_text="file extension", index=True)
    created_by = CharField(max_length=32, null=False, help_text="who created it", index=True)
    name = CharField(max_length=255, null=True, help_text="file name", index=True)
    location = CharField(max_length=255, null=True, help_text="where dose it store", index=True)
    size = IntegerField(default=0, index=True)
    token_num = IntegerField(default=0, index=True)
    chunk_num = IntegerField(default=0, index=True)
    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    process_begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)
    suffix = CharField(max_length=32, null=False, help_text="The real file extension suffix", index=True)

    content_hash = CharField(max_length=32, null=True, help_text="xxhash128 of document content for change detection", default="", index=True)

    run = CharField(max_length=1, null=True, help_text="start to run processing or cancel.(1: run it; 2: cancel)", default="0", index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:  # Line 1423
        db_table = "document"  # 数据库表名 | Database table name


# ==============================================================================
# File Model - 文件模型
# ==============================================================================

class File(DataBaseModel):  # Line 1427
    """
    文件模型 | File Model

    文件管理系统中的文件或文件夹记录。
    File or folder records in the file management system.

    Attributes:
        id: 文件唯一标识 | Unique file identifier (32 chars, primary key)
        parent_id: 父文件夹 ID | Parent folder ID (32 chars, required, indexed)
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed)
        created_by: 创建者 ID | Creator user ID (32 chars, required, indexed)
        name: 文件名或文件夹名 | File or folder name (255 chars, required, indexed)
        location: 存储位置 | Storage location path (255 chars, nullable, indexed)
        size: 文件大小 | File size in bytes (integer, defaults to 0, indexed)
        type: 文件扩展名 | File extension type (32 chars, required)
        source_type: 来源类型 | Document source (128 chars, defaults to empty string, indexed)

    Supports hierarchical file structure through parent_id references.
    通过 parent_id 引用支持分层文件结构。
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1428
    parent_id = CharField(max_length=32, null=False, help_text="parent folder id", index=True)
    tenant_id = CharField(max_length=32, null=False, help_text="tenant id", index=True)
    created_by = CharField(max_length=32, null=False, help_text="who created it", index=True)
    name = CharField(max_length=255, null=False, help_text="file name or folder name", index=True)
    location = CharField(max_length=255, null=True, help_text="where dose it store", index=True)
    size = IntegerField(default=0, index=True)
    type = CharField(max_length=32, null=False, help_text="file extension")
    source_type = CharField(max_length=128, null=False, default="", help_text="where dose this document come from", index=True)

    class Meta:  # Line 1438
        db_table = "file"  # 数据库表名 | Database table name


# ==============================================================================
# File2Document Model - 文件-文档关联模型
# ==============================================================================

class File2Document(DataBaseModel):  # Line 1442
    """
    文件-文档关联模型 | File-Document Association Model

    关联文件记录和文档记录的多对多关系表。
    Many-to-many relationship table linking file records to document records.

    Attributes:
        id: 关联记录唯一标识 | Unique association record identifier (32 chars, primary key)
        file_id: 文件 ID | File identifier (32 chars, nullable, indexed)
        document_id: 文档 ID | Document identifier (32 chars, nullable, indexed)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1443
    file_id = CharField(max_length=32, null=True, help_text="file id", index=True)
    document_id = CharField(max_length=32, null=True, help_text="document id", index=True)

    class Meta:  # Line 1447
        db_table = "file2document"  # 数据库表名 | Database table name


# ==============================================================================
# Task Model - 任务模型
# ==============================================================================

class Task(DataBaseModel):  # Line 1451
    """
    任务模型 | Task Model

    文档解析任务的队列记录。
    Queue records for document parsing tasks.

    Attributes:
        id: 任务唯一标识 | Unique task identifier (32 chars, primary key)
        doc_id: 文档 ID | Document identifier (32 chars, required, indexed)
        from_page: 起始页 | Start page number (integer, defaults to 0)
        to_page: 结束页 | End page number (integer, defaults to 100000000)
        task_type: 任务类型 | Task type/category (32 chars, required, defaults to empty string)
        priority: 优先级 | Task priority (integer, defaults to 0)
        begin_at: 开始时间 | Task start timestamp (datetime, nullable, indexed)
        process_duration: 处理耗时 | Processing duration in seconds (float, defaults to 0)
        progress: 进度 | Task progress 0-1 (float, defaults to 0, indexed)
        progress_msg: 进度消息 | Progress message (text field, defaults to empty string)
        retry_count: 重试次数 | Number of retries (integer, defaults to 0)
        digest: 任务摘要 | Task digest information (text field, defaults to empty string)
        chunk_ids: 切片 ID 列表 | List of chunk IDs (long text field, defaults to empty string)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1452
    doc_id = CharField(max_length=32, null=False, index=True)
    from_page = IntegerField(default=0)
    to_page = IntegerField(default=100000000)
    task_type = CharField(max_length=32, null=False, default="")
    priority = IntegerField(default=0)

    begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)

    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    retry_count = IntegerField(default=0)
    digest = TextField(null=True, help_text="task digest", default="")
    chunk_ids = LongTextField(null=True, help_text="chunk ids", default="")


# ==============================================================================
# Dialog Model - 对话应用模型
# ==============================================================================

class Dialog(DataBaseModel):  # Line 1469
    """
    对话应用模型 | Dialog Application Model

    RAG对话应用的配置，定义了检索和生成的参数。
    RAG dialog application configuration defining retrieval and generation parameters.

    Attributes:
        id: 对话唯一标识 | Unique dialog identifier (32 chars, primary key)
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed)
        name: 对话应用名称 | Dialog application name (255 chars, nullable, indexed)
        description: 描述 | Dialog description (text field, nullable)
        icon: 图标 | Dialog icon (Base64 encoded, text field, nullable)
        language: 语言 | Language setting (32 chars, defaults based on system locale, indexed)
        llm_id: 默认 LLM 模型 ID | Default LLM model ID (128 chars, required)
        tenant_llm_id: 租户 LLM 配置 ID | Tenant's LLM configuration ID (integer, indexed)
        llm_setting: LLM 设置 | LLM parameters JSON (temperature, top_p, penalties, max_tokens)
        prompt_type: 提示词类型 | Prompt type (16 chars, "simple" or "advanced", defaults to "simple", indexed)
        prompt_config: 提示词配置 | Prompt configuration JSON (system, prologue, parameters, empty_response)
        meta_data_filter: 元数据过滤器 | Metadata filter configuration (JSON field, defaults to empty dict)
        similarity_threshold: 相似度阈值 | Similarity threshold for retrieval (float, defaults to 0.2)
        vector_similarity_weight: 向量相似度权重 | Vector similarity weight (float, defaults to 0.3)
        top_n: Top N | Top N results to retrieve (integer, defaults to 6)
        top_k: Top K | Top K candidates for reranking (integer, defaults to 1024)
        do_refer: 是否引用 | Whether to insert reference indexes into answer (1 char, defaults to "1")
        rerank_id: 重排序模型 ID | Rerank model ID (128 chars, required)
        tenant_rerank_id: 租户重排序模型配置 ID | Tenant's rerank model configuration ID (integer, indexed)
        kb_ids: 知识库 ID 列表 | List of knowledge base IDs (JSON field, defaults to empty list)
        status: 状态 | Status (0=invalid, 1=valid, defaults to "1", indexed)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1470
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=True, help_text="dialog application name", index=True)
    description = TextField(null=True, help_text="Dialog description")
    icon = TextField(null=True, help_text="icon base64 string")
    language = CharField(max_length=32, null=True, default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", help_text="English|Chinese", index=True)
    llm_id = CharField(max_length=128, null=False, help_text="default llm ID")
    tenant_llm_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)

    llm_setting = JSONField(null=False, default={"temperature": 0.1, "top_p": 0.3, "frequency_penalty": 0.7, "presence_penalty": 0.4, "max_tokens": 512})
    prompt_type = CharField(max_length=16, null=False, default="simple", help_text="simple|advanced", index=True)
    prompt_config = JSONField(
        null=False,
        default={"system": "", "prologue": "Hi! I'm your assistant. What can I do for you?", "parameters": [], "empty_response": "Sorry! No relevant content was found in the knowledge base!"},
    )
    meta_data_filter = JSONField(null=True, default={})

    similarity_threshold = FloatField(default=0.2)
    vector_similarity_weight = FloatField(default=0.3)

    top_n = IntegerField(default=6)

    top_k = IntegerField(default=1024)

    do_refer = CharField(max_length=1, null=False, default="1", help_text="it needs to insert reference index into answer or not")

    rerank_id = CharField(max_length=128, null=False, help_text="default rerank model ID")
    tenant_rerank_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    kb_ids = JSONField(null=False, default=[])
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:  # Line 1501
        db_table = "dialog"  # 数据库表名 | Database table name


# ==============================================================================
# Conversation Model - 对话记录模型
# ==============================================================================

class Conversation(DataBaseModel):  # Line 1505
    """
    对话记录模型 | Conversation Record Model

    存储对话历史记录，包括消息和引用的文档。
    Stores conversation history including messages and referenced documents.

    Attributes:
        id: 对话记录唯一标识 | Unique conversation record identifier (32 chars, primary key)
        dialog_id: 对话应用 ID | Dialog application ID (32 chars, required, indexed)
        name: 对话名称 | Conversation name (255 chars, nullable, indexed)
        message: 消息 | Message history JSON (JSON field, nullable)
        reference: 引用 | Referenced documents/ chunks (JSON field, defaults to empty list)
        user_id: 用户 ID | User identifier (255 chars, nullable, indexed)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1506
    dialog_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=True, help_text="conversation name", index=True)
    message = JSONField(null=True)
    reference = JSONField(null=True, default=[])
    user_id = CharField(max_length=255, null=True, help_text="user_id", index=True)

    class Meta:  # Line 1513
        db_table = "conversation"  # 数据库表名 | Database table name


# ==============================================================================
# APIToken Model - API令牌模型
# ==============================================================================

class APIToken(DataBaseModel):  # Line 1517
    """
    API令牌模型 | API Token Model

    用于API认证的令牌管理。
    Manages tokens for API authentication.

    Attributes:
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed, part of composite PK)
        token: 令牌 | Token string (255 chars, required, indexed, part of composite PK)
        dialog_id: 对话应用 ID | Dialog application ID (32 chars, nullable, indexed)
        source: 来源 | Token source (16 chars, nullable: "none|agent|dialog", indexed)
        beta: Beta 标识 | Beta identifier (255 chars, nullable, indexed)

    Uses composite primary key on (tenant_id, token) for uniqueness.
    使用 (tenant_id, token) 的复合主键确保唯一性。
    """
    tenant_id = CharField(max_length=32, null=False, index=True)  # Line 1518
    token = CharField(max_length=255, null=False, index=True)
    dialog_id = CharField(max_length=32, null=True, index=True)
    source = CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True)
    beta = CharField(max_length=255, null=True, index=True)

    class Meta:  # Line 1524
        db_table = "api_token"  # 数据库表名 | Database table name
        primary_key = CompositeKey("tenant_id", "token")  # 复合主键 | Composite primary key


# ==============================================================================
# API4Conversation Model - API对话记录模型
# ==============================================================================

class API4Conversation(DataBaseModel):  # Line 1529
    """
    API对话记录模型 | API Conversation Model

    通过API调用的对话记录，支持agent和dialog两种模式。
    Conversation records via API calls, supporting both agent and dialog modes.

    Attributes:
        id: 对话记录唯一标识 | Unique conversation identifier (32 chars, primary key)
        name: 对话名称 | Conversation name (255 chars, nullable)
        dialog_id: 对话应用 ID | Dialog application ID (32 chars, required, indexed)
        user_id: 用户 ID | User identifier (255 chars, required, indexed)
        exp_user_id: 实验用户 ID | Experimental user ID (255 chars, nullable, indexed)
        message: 消息 | Message history JSON (JSON field, nullable)
        reference: 引用 | Referenced documents/chunks (JSON field, defaults to empty list)
        tokens: token 数量 | Token usage count (integer, defaults to 0)
        source: 来源 | Source type (16 chars, nullable: "none|agent|dialog", indexed)
        dsl: DSL 配置 | Domain Specific Language configuration (JSON field, defaults to empty dict)
        duration: 持续时间 | Conversation duration in seconds (float, defaults to 0, indexed)
        round: 轮次 | Conversation round number (integer, defaults to 0, indexed)
        thumb_up: 点赞数 | Thumbs up count (integer, defaults to 0, indexed)
        errors: 错误信息 | Error messages (text field, nullable)
        version_title: 版本标题 | Canvas version title when session created (255 chars, nullable)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1530
    name = CharField(max_length=255, null=True, help_text="conversation name", index=False)
    dialog_id = CharField(max_length=32, null=False, index=True)
    user_id = CharField(max_length=255, null=False, help_text="user_id", index=True)
    exp_user_id = CharField(max_length=255, null=True, help_text="exp_user_id", index=True)
    message = JSONField(null=True)
    reference = JSONField(null=True, default=[])
    tokens = IntegerField(default=0)
    source = CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True)
    dsl = JSONField(null=True, default={})
    duration = FloatField(default=0, index=True)
    round = IntegerField(default=0, index=True)
    thumb_up = IntegerField(default=0, index=True)
    errors = TextField(null=True, help_text="errors")
    version_title = CharField(max_length=255, null=True, help_text="canvas version title when session created", index=False)

    class Meta:  # Line 1546
        db_table = "api_4_conversation"  # 数据库表名 | Database table name


# ==============================================================================
# UserCanvas Model - 用户画布模型
# ==============================================================================

class UserCanvas(DataBaseModel):  # Line 1550
    """
    用户画布模型 | User Canvas Model

    用户创建的Agent工作流画布。
    Agent workflow canvas created by users.

    Attributes:
        id: 画布唯一标识 | Unique canvas identifier (32 chars, primary key)
        avatar: 头像 | Canvas avatar (Base64 encoded, text field, nullable)
        user_id: 用户 ID | User identifier (255 chars, required, indexed)
        title: 标题 | Canvas title (255 chars, nullable)
        permission: 权限 | Permission level (16 chars, "me" or "team", defaults to "me", indexed)
        release: 发布状态 | Release status (boolean, defaults to False, indexed)
        description: 描述 | Canvas description (text field, nullable)
        canvas_type: 画布类型 | Canvas type (32 chars, nullable, indexed)
        canvas_category: 画布分类 | Canvas category (32 chars, defaults to "agent_canvas", indexed)
        dsl: DSL 配置 | Domain Specific Language configuration (JSON field, defaults to empty dict)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1551
    avatar = TextField(null=True, help_text="avatar base64 string")
    user_id = CharField(max_length=255, null=False, help_text="user_id", index=True)
    title = CharField(max_length=255, null=True, help_text="Canvas title")

    permission = CharField(max_length=16, null=False, help_text="me|team", default="me", index=True)
    release = BooleanField(null=False, help_text="is released", default=False, index=True)
    description = TextField(null=True, help_text="Canvas description")
    canvas_type = CharField(max_length=32, null=True, help_text="Canvas type", index=True)
    canvas_category = CharField(max_length=32, null=False, default="agent_canvas", help_text="Canvas category: agent_canvas|dataflow_canvas", index=True)
    dsl = JSONField(null=True, default={})

    class Meta:  # Line 1563
        db_table = "user_canvas"  # 数据库表名 | Database table name


# ==============================================================================
# CanvasTemplate Model - 画布模板模型
# ==============================================================================

class CanvasTemplate(DataBaseModel):  # Line 1567
    """
    画布模板模型 | Canvas Template Model

    系统预定义的画布模板。
    Predefined canvas templates provided by the system.

    Attributes:
        id: 模板唯一标识 | Unique template identifier (32 chars, primary key)
        avatar: 头像 | Template avatar (Base64 encoded, text field, nullable)
        title: 标题 | Template title as JSON for i18n (JSON field, defaults to dict)
        description: 描述 | Template description as JSON for i18n (JSON field, defaults to dict)
        canvas_type: 画布类型 | Canvas type (32 chars, nullable, indexed)
        canvas_category: 画布分类 | Canvas category (32 chars, defaults to "agent_canvas", indexed)
        dsl: DSL 配置 | Domain Specific Language configuration (JSON field, defaults to empty dict)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1568
    avatar = TextField(null=True, help_text="avatar base64 string")
    title = JSONField(null=True, default=dict, help_text="Canvas title")
    description = JSONField(null=True, default=dict, help_text="Canvas description")
    canvas_type = CharField(max_length=32, null=True, help_text="Canvas type", index=True)
    canvas_category = CharField(max_length=32, null=False, default="agent_canvas", help_text="Canvas category: agent_canvas|dataflow_canvas", index=True)
    dsl = JSONField(null=True, default={})

    class Meta:  # Line 1576
        db_table = "canvas_template"  # 数据库表名 | Database table name


# ==============================================================================
# UserCanvasVersion Model - 用户画布版本模型
# ==============================================================================

class UserCanvasVersion(DataBaseModel):  # Line 1580
    """
    用户画布版本模型 | User Canvas Version Model

    用户画布的版本历史记录。
    Version history records for user canvases.

    Attributes:
        id: 版本记录唯一标识 | Unique version record identifier (32 chars, primary key)
        user_canvas_id: 画布 ID | User canvas identifier (255 chars, required, indexed)
        title: 标题 | Canvas title (255 chars, nullable)
        description: 描述 | Canvas description (text field, nullable)
        release: 发布状态 | Release status (boolean, defaults to False, indexed)
        dsl: DSL 配置 | Domain Specific Language configuration (JSON field, defaults to empty dict)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1581
    user_canvas_id = CharField(max_length=255, null=False, help_text="user_canvas_id", index=True)

    title = CharField(max_length=255, null=True, help_text="Canvas title")
    description = TextField(null=True, help_text="Canvas description")
    release = BooleanField(null=False, help_text="is released", default=False, index=True)
    dsl = JSONField(null=True, default={})

    class Meta:  # Line 1589
        db_table = "user_canvas_version"  # 数据库表名 | Database table name


# ==============================================================================
# MCPServer Model - MCP服务器模型
# ==============================================================================

class MCPServer(DataBaseModel):  # Line 1593
    """
    MCP服务器模型 | Model Context Protocol Server Model

    配置的外部MCP服务器连接信息。
    External MCP (Model Context Protocol) server connection configuration.

    Attributes:
        id: 服务器唯一标识 | Unique server identifier (32 chars, primary key)
        name: 服务器名称 | Server name (255 chars, required)
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed)
        url: 服务器 URL | Server URL (2048 chars, required)
        server_type: 服务器类型 | Server type (32 chars, required)
        description: 描述 | Server description (text field, nullable)
        variables: 变量 | Server variables configuration (JSON field, defaults to dict)
        headers: 请求头 | Additional request headers (JSON field, defaults to dict)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1594
    name = CharField(max_length=255, null=False, help_text="MCP Server name")
    tenant_id = CharField(max_length=32, null=False, index=True)
    url = CharField(max_length=2048, null=False, help_text="MCP Server URL")
    server_type = CharField(max_length=32, null=False, help_text="MCP Server type")
    description = TextField(null=True, help_text="MCP Server description")
    variables = JSONField(null=True, default=dict, help_text="MCP Server variables")
    headers = JSONField(null=True, default=dict, help_text="MCP Server additional request headers")

    class Meta:  # Line 1603
        db_table = "mcp_server"  # 数据库表名 | Database table name


# ==============================================================================
# Search Model - 搜索配置模型
# ==============================================================================

class Search(DataBaseModel):  # Line 1607
    """
    搜索配置模型 | Search Configuration Model

    知识库搜索的高级配置，支持多种检索模式。
    Advanced knowledge base search configuration supporting multiple retrieval modes.

    Attributes:
        id: 搜索配置唯一标识 | Unique search configuration identifier (32 chars, primary key)
        avatar: 头像 | Search avatar (Base64 encoded, text field, nullable)
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed)
        name: 搜索名称 | Search name (128 chars, required, indexed)
        description: 描述 | Search description (text field, nullable)
        created_by: 创建者 ID | Creator user ID (32 chars, required, indexed)
        search_config: 搜索配置 | Comprehensive search configuration JSON including:
            - kb_ids: 知识库ID列表 | Knowledge base ID list
            - doc_ids: 文档ID列表 | Document ID list
            - similarity_threshold: 相似度阈值 | Similarity threshold
            - vector_similarity_weight: 向量相似度权重 | Vector similarity weight
            - use_kg: 是否使用知识图谱 | Whether to use knowledge graph
            - rerank_id: 重排序模型ID | Rerank model ID
            - top_k: Top K | Top K candidates
            - summary: 是否摘要 | Whether to generate summary
            - chat_id: 聊天ID | Chat ID for summary
            - llm_setting: LLM设置 | LLM settings
            - cross_languages: 跨语言列表 | Cross-language list
            - highlight: 高亮 | Whether to highlight
            - keyword: 关键词 | Whether to use keyword search
            - web_search: 网络搜索 | Whether to use web search
            - related_search: 相关搜索 | Whether to use related search
            - query_mindmap: 查询思维导图 | Whether to generate query mindmap
        status: 状态 | Status (0=invalid, 1=valid, defaults to "1", indexed)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1608
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="Search name", index=True)
    description = TextField(null=True, help_text="KB description")
    created_by = CharField(max_length=32, null=False, index=True)
    search_config = JSONField(  # Line 1614
        null=False,
        default={
            "kb_ids": [],
            "doc_ids": [],
            "similarity_threshold": 0.2,
            "vector_similarity_weight": 0.3,
            "use_kg": False,
            # rerank settings | 重排序设置
            "rerank_id": "",
            "top_k": 1024,
            # chat settings | 聊天设置
            "summary": False,
            "chat_id": "",
            # Leave it here for reference, don't need to set default values
            "llm_setting": {
                # "temperature": 0.1,
                # "top_p": 0.3,
                # "frequency_penalty": 0.7,
                # "presence_penalty": 0.4,
            },
            "chat_settingcross_languages": [],
            "highlight": False,
            "keyword": False,
            "web_search": False,
            "related_search": False,
            "query_mindmap": False,
        },
    )
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):  # Line 1645
        """返回搜索名称 | Returns search name"""
        return self.name

    class Meta:  # Line 1648
        db_table = "search"  # 数据库表名 | Database table name


# ==============================================================================
# PipelineOperationLog Model - 流水线操作日志模型
# ==============================================================================

class PipelineOperationLog(DataBaseModel):  # Line 1652
    """
    流水线操作日志模型 | Pipeline Operation Log Model

    记录文档处理流水线的操作日志。
    Records operation logs for document processing pipelines.

    Attributes:
        id: 日志唯一标识 | Unique log identifier (32 chars, primary key)
        document_id: 文档 ID | Document identifier (32 chars, indexed)
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed)
        kb_id: 知识库 ID | Knowledge base identifier (32 chars, required, indexed)
        pipeline_id: 流水线 ID | Pipeline ID (32 chars, nullable, indexed)
        pipeline_title: 流水线标题 | Pipeline title (32 chars, nullable, indexed)
        parser_id: 解析器 ID | Parser ID (32 chars, required, indexed)
        document_name: 文档名称 | Document file name (255 chars, required, indexed)
        document_suffix: 文档后缀 | Document file suffix (255 chars, required, indexed)
        document_type: 文档类型 | Document type (255 chars, required, indexed)
        source_from: 来源 | Source origin (255 chars, required, indexed)
        progress: 进度 | Processing progress 0-1 (float, defaults to 0, indexed)
        progress_msg: 进度消息 | Progress message (text field, defaults to empty string)
        process_begin_at: 处理开始时间 | Processing start timestamp (datetime, nullable, indexed)
        process_duration: 处理耗时 | Processing duration in seconds (float, defaults to 0)
        dsl: DSL 配置 | Domain Specific Language configuration (JSON field, defaults to dict)
        task_type: 任务类型 | Task type (32 chars, required, defaults to empty string)
        operation_status: 操作状态 | Operation status (32 chars, required, indexed)
        avatar: 头像 | Avatar (Base64 encoded, text field, nullable)
        status: 状态 | Status (0=invalid, 1=valid, defaults to "1", indexed)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1653
    document_id = CharField(max_length=32, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="Pipeline ID", index=True)
    pipeline_title = CharField(max_length=32, null=True, help_text="Pipeline title", index=True)
    parser_id = CharField(max_length=32, null=False, help_text="Parser ID", index=True)
    document_name = CharField(max_length=255, null=False, help_text="File name")
    document_suffix = CharField(max_length=255, null=False, help_text="File suffix")
    document_type = CharField(max_length=255, null=False, help_text="Document type")
    source_from = CharField(max_length=255, null=False, help_text="Source")
    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    process_begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)
    dsl = JSONField(null=True, default=dict)
    task_type = CharField(max_length=32, null=False, default="")
    operation_status = CharField(max_length=32, null=False, help_text="Operation status")
    avatar = TextField(null=True, help_text="avatar base64 string")
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:  # Line 1674
        db_table = "pipeline_operation_log"  # 数据库表名 | Database table name


# ==============================================================================
# Connector Model - 连接器模型
# ==============================================================================

class Connector(DataBaseModel):  # Line 1678
    """
    连接器模型 | Connector Model

    外部数据源连接器配置，用于从外部系统同步数据。
    External data source connector configuration for syncing data from external systems.

    Attributes:
        id: 连接器唯一标识 | Unique connector identifier (32 chars, primary key)
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed)
        name: 连接器名称 | Connector name (128 chars, required, indexed)
        source: 数据源 | Data source type (128 chars, required, indexed)
        input_type: 输入类型 | Input type (128 chars, required: "poll/event/..", indexed)
        config: 配置 | Connector configuration (JSON field, defaults to empty dict)
        refresh_freq: 刷新频率 | Refresh frequency in seconds (integer, defaults to 0)
        prune_freq: 清理频率 | Prune frequency in seconds (integer, defaults to 0)
        timeout_secs: 超时秒数 | Timeout in seconds (integer, defaults to 3600)
        indexing_start: 索引开始时间 | Indexing start timestamp (datetime, nullable, indexed)
        status: 状态 | Status (16 chars, defaults to "schedule", indexed)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1679
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="Search name", index=False)
    source = CharField(max_length=128, null=False, help_text="Data source", index=True)
    input_type = CharField(max_length=128, null=False, help_text="poll/event/..", index=True)
    config = JSONField(null=False, default={})
    refresh_freq = IntegerField(default=0, index=False)
    prune_freq = IntegerField(default=0, index=False)
    timeout_secs = IntegerField(default=3600, index=False)
    indexing_start = DateTimeField(null=True, index=True)
    status = CharField(max_length=16, null=True, help_text="schedule", default="schedule", index=True)

    def __str__(self):  # Line 1691
        """返回连接器名称 | Returns connector name"""
        return self.name

    class Meta:  # Line 1694
        db_table = "connector"  # 数据库表名 | Database table name


# ==============================================================================
# Connector2Kb Model - 连接器-知识库关联模型
# ==============================================================================

class Connector2Kb(DataBaseModel):  # Line 1698
    """
    连接器-知识库关联模型 | Connector-Knowledgebase Association Model

    关联连接器与知识库的多对多关系表。
    Many-to-many relationship table linking connectors to knowledge bases.

    Attributes:
        id: 关联记录唯一标识 | Unique association record identifier (32 chars, primary key)
        connector_id: 连接器 ID | Connector identifier (32 chars, required, indexed)
        kb_id: 知识库 ID | Knowledge base identifier (32 chars, required, indexed)
        auto_parse: 自动解析 | Whether to automatically parse synced documents (1 char, defaults to "1")
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1699
    connector_id = CharField(max_length=32, null=False, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)
    auto_parse = CharField(max_length=1, null=False, default="1", index=False)

    class Meta:  # Line 1704
        db_table = "connector2kb"  # 数据库表名 | Database table name


# ==============================================================================
# DateTimeTzField - 时区感知日期时间字段
# ==============================================================================

class DateTimeTzField(CharField):  # Line 1708
    """
    时区感知日期时间字段 | Timezone-aware DateTime Field

    自定义字段类型，处理带时区的日期时间存储和转换。
    Custom field type for handling timezone-aware datetime storage and conversion.

    Stores datetime as ISO format string with timezone information.
    将日期时间存储为带时区信息的ISO格式字符串。

    Attributes:
        field_type: 字段类型 | Field type (VARCHAR)
    """
    field_type = 'VARCHAR'  # Line 1709

    def db_value(self, value: datetime|None) -> str|None:  # Line 1711
        """
        将 Python datetime 对象转换为数据库存储的 ISO 字符串
        Converts Python datetime object to ISO string for database storage

        Args:
            value: datetime 对象，可能为 None | datetime object, can be None

        Returns:
            str|None: ISO 格式的日期时间字符串 | ISO format datetime string, or None

        Note:
            如果 datetime 对象没有时区信息，会自动添加 UTC 时区。
            If datetime object has no timezone info, UTC timezone is automatically added.
        """
        if value is not None:
            if value.tzinfo is not None:
                return value.isoformat()  # 有时区信息，直接转换 | Has timezone, convert directly
            else:
                return value.replace(tzinfo=timezone.utc).isoformat()  # 添加 UTC 时区 | Add UTC timezone
        return value

    def python_value(self, value: str|None) -> datetime|None:  # Line 1719
        """
        将数据库中的 ISO 字符串转换为 Python datetime 对象
        Converts ISO string from database to Python datetime object

        Args:
            value: ISO 格式的日期时间字符串 | ISO format datetime string

        Returns:
            datetime|None: datetime 对象，或 None | datetime object, or None

        Note:
            如果字符串没有时区信息，会自动添加 UTC 时区。
            If string has no timezone info, UTC timezone is automatically added.
        """
        if value is not None:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                import pytz
                return dt.replace(tzinfo=pytz.UTC)  # 添加 UTC 时区 | Add UTC timezone
            return dt
        return value


# ==============================================================================
# SyncLogs Model - 同步日志模型
# ==============================================================================

class SyncLogs(DataBaseModel):  # Line 1729
    """
    同步日志模型 | Sync Logs Model

    记录连接器数据同步的执行日志。
    Records execution logs for connector data synchronization.

    Attributes:
        id: 日志唯一标识 | Unique log identifier (32 chars, primary key)
        connector_id: 连接器 ID | Connector identifier (32 chars, indexed)
        status: 状态 | Processing status (128 chars, required, indexed)
        from_beginning: 从头开始 | Whether to sync from beginning (1 char, defaults to "0")
        new_docs_indexed: 新建文档索引数 | Number of new documents indexed (integer, defaults to 0)
        total_docs_indexed: 总文档索引数 | Total number of documents indexed (integer, defaults to 0)
        docs_removed_from_index: 从索引移除的文档数 | Number of documents removed from index (integer, defaults to 0)
        error_msg: 错误消息 | Error message (text field, defaults to empty string)
        error_count: 错误计数 | Error count (integer, defaults to 0)
        full_exception_trace: 完整异常追踪 | Full exception trace (text field, defaults to empty string)
        time_started: 开始时间 | Sync start timestamp (datetime, nullable, indexed)
        poll_range_start: 轮询范围开始 | Poll range start (DateTimeTzField, nullable, indexed)
        poll_range_end: 轮询范围结束 | Poll range end (DateTimeTzField, nullable, indexed)
        kb_id: 知识库 ID | Knowledge base identifier (32 chars, required, indexed)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1730
    connector_id = CharField(max_length=32, index=True)
    status = CharField(max_length=128, null=False, help_text="Processing status", index=True)
    from_beginning = CharField(max_length=1, null=True, help_text="", default="0", index=False)
    new_docs_indexed = IntegerField(default=0, index=False)
    total_docs_indexed = IntegerField(default=0, index=False)
    docs_removed_from_index = IntegerField(default=0, index=False)
    error_msg = TextField(null=False, help_text="process message", default="")
    error_count = IntegerField(default=0, index=False)
    full_exception_trace = TextField(null=True, help_text="process message", default="")
    time_started = DateTimeField(null=True, index=True)
    poll_range_start = DateTimeTzField(max_length=255, null=True, index=True)
    poll_range_end = DateTimeTzField(max_length=255, null=True, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)

    class Meta:  # Line 1745
        db_table = "sync_logs"  # 数据库表名 | Database table name


# ==============================================================================
# EvaluationDataset Model - 评估数据集模型
# ==============================================================================

class EvaluationDataset(DataBaseModel):  # Line 1749
    """
    RAG评估的数据集模型 | RAG Evaluation Dataset Model

    用于RAG系统评估的ground truth数据集。
    Ground truth dataset for RAG system evaluation.

    Attributes:
        id: 数据集唯一标识 | Unique dataset identifier (32 chars, primary key)
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed)
        name: 数据集名称 | Dataset name (255 chars, required, indexed)
        description: 描述 | Dataset description (text field, nullable)
        kb_ids: 知识库 ID 列表 | Knowledge base IDs to evaluate against (JSON field, required)
        created_by: 创建者用户 ID | Creator user ID (32 chars, required, indexed)
        create_time: 创建时间 | Creation timestamp (bigint, required, indexed)
        update_time: 更新时间 | Last update timestamp (bigint, required)
        status: 状态 | Status (1=valid, 0=invalid, integer, defaults to 1)
    """
    """Ground truth dataset for RAG evaluation"""
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True, help_text="tenant ID")
    name = CharField(max_length=255, null=False, index=True, help_text="dataset name")
    description = TextField(null=True, help_text="dataset description")
    kb_ids = JSONField(null=False, help_text="knowledge base IDs to evaluate against")
    created_by = CharField(max_length=32, null=False, index=True, help_text="creator user ID")
    create_time = BigIntegerField(null=False, index=True, help_text="creation timestamp")
    update_time = BigIntegerField(null=False, help_text="last update timestamp")
    status = IntegerField(null=False, default=1, help_text="1=valid, 0=invalid")

    class Meta:  # Line 1761
        db_table = "evaluation_datasets"  # 数据库表名 | Database table name


# ==============================================================================
# EvaluationCase Model - 评估用例模型
# ==============================================================================

class EvaluationCase(DataBaseModel):  # Line 1765
    """
    RAG评估的单个测试用例模型 | RAG Evaluation Test Case Model

    评估数据集中的单个测试用例。
    Individual test case in an evaluation dataset.

    Attributes:
        id: 用例唯一标识 | Unique case identifier (32 chars, primary key)
        dataset_id: 数据集 ID | Dataset ID foreign key (32 chars, required, indexed)
        question: 问题 | Test question (text field, required)
        reference_answer: 参考答案 | Optional ground truth answer (text field, nullable)
        relevant_doc_ids: 相关文档 ID 列表 | Expected relevant document IDs (JSON field, nullable)
        relevant_chunk_ids: 相关切片 ID 列表 | Expected relevant chunk IDs (JSON field, nullable)
        metadata: 元数据 | Additional context/tags (JSON field, nullable)
        create_time: 创建时间 | Creation timestamp (bigint, required)
    """
    """Individual test case in an evaluation dataset"""
    id = CharField(max_length=32, primary_key=True)
    dataset_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_datasets")
    question = TextField(null=False, help_text="test question")
    reference_answer = TextField(null=True, help_text="optional ground truth answer")
    relevant_doc_ids = JSONField(null=True, help_text="expected relevant document IDs")
    relevant_chunk_ids = JSONField(null=True, help_text="expected relevant chunk IDs")
    metadata = JSONField(null=True, help_text="additional context/tags")
    create_time = BigIntegerField(null=False, help_text="creation timestamp")

    class Meta:  # Line 1776
        db_table = "evaluation_cases"  # 数据库表名 | Database table name


# ==============================================================================
# EvaluationRun Model - 评估运行模型
# ==============================================================================

class EvaluationRun(DataBaseModel):  # Line 1780
    """
    RAG评估运行记录模型 | RAG Evaluation Run Model

    一次RAG评估运行的记录。
    A single RAG evaluation run record.

    Attributes:
        id: 运行记录唯一标识 | Unique run identifier (32 chars, primary key)
        dataset_id: 数据集 ID | Dataset ID foreign key (32 chars, required, indexed)
        dialog_id: 对话应用 ID | Dialog configuration being evaluated (32 chars, required, indexed)
        name: 运行名称 | Run name (255 chars, required)
        config_snapshot: 配置快照 | Dialog config at time of evaluation (JSON field, required)
        metrics_summary: 指标摘要 | Aggregated metrics (JSON field, nullable)
        status: 状态 | Status (32 chars, defaults to "PENDING": PENDING/RUNNING/COMPLETED/FAILED)
        created_by: 创建者 | User who started the run (32 chars, required, indexed)
        create_time: 创建时间 | Creation timestamp (bigint, required, indexed)
        complete_time: 完成时间 | Completion timestamp (bigint, nullable)
    """
    """A single evaluation run"""
    id = CharField(max_length=32, primary_key=True)
    dataset_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_datasets")
    dialog_id = CharField(max_length=32, null=False, index=True, help_text="dialog configuration being evaluated")
    name = CharField(max_length=255, null=False, help_text="run name")
    config_snapshot = JSONField(null=False, help_text="dialog config at time of evaluation")
    metrics_summary = JSONField(null=True, help_text="aggregated metrics")
    status = CharField(max_length=32, null=False, default="PENDING", help_text="PENDING/RUNNING/COMPLETED/FAILED")
    created_by = CharField(max_length=32, null=False, index=True, help_text="user who started the run")
    create_time = BigIntegerField(null=False, index=True, help_text="creation timestamp")
    complete_time = BigIntegerField(null=True, help_text="completion timestamp")

    class Meta:  # Line 1793
        db_table = "evaluation_runs"  # 数据库表名 | Database table name


# ==============================================================================
# EvaluationResult Model - 评估结果模型
# ==============================================================================

class EvaluationResult(DataBaseModel):  # Line 1797
    """
    RAG评估结果模型 | RAG Evaluation Result Model

    评估运行中单个测试用例的结果。
    Result for a single test case in an evaluation run.

    Attributes:
        id: 结果唯一标识 | Unique result identifier (32 chars, primary key)
        run_id: 运行 ID | Run ID foreign key (32 chars, required, indexed)
        case_id: 用例 ID | Case ID foreign key (32 chars, required, indexed)
        generated_answer: 生成答案 | Generated answer (text field, required)
        retrieved_chunks: 检索切片 | Chunks that were retrieved (JSON field, required)
        metrics: 指标 | All computed metrics (JSON field, required)
        execution_time: 执行时间 | Response time in seconds (float, required)
        token_usage: token 使用 | Prompt/completion tokens (JSON field, nullable)
        create_time: 创建时间 | Creation timestamp (bigint, required)
    """
    """Result for a single test case in an evaluation run"""
    id = CharField(max_length=32, primary_key=True)
    run_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_runs")
    case_id = CharField(max_length=32, null=False, index=True, help_text="FK to evaluation_cases")
    generated_answer = TextField(null=False, help_text="generated answer")
    retrieved_chunks = JSONField(null=False, help_text="chunks that were retrieved")
    metrics = JSONField(null=False, help_text="all computed metrics")
    execution_time = FloatField(null=False, help_text="response time in seconds")
    token_usage = JSONField(null=True, help_text="prompt/completion tokens")
    create_time = BigIntegerField(null=False, help_text="creation timestamp")

    class Meta:  # Line 1809
        db_table = "evaluation_results"  # 数据库表名 | Database table name


# ==============================================================================
# Memory Model - 记忆模型
# ==============================================================================

class Memory(DataBaseModel):  # Line 1813
    """
    记忆模型 | Memory Model

    Agent的记忆存储系统，支持多种记忆类型。
    Agent memory storage system supporting multiple memory types.

    Attributes:
        id: 记忆唯一标识 | Unique memory identifier (32 chars, primary key)
        name: 记忆名称 | Memory name (128 chars, required)
        avatar: 头像 | Memory avatar (Base64 encoded, text field, nullable)
        tenant_id: 租户 ID | Tenant identifier (32 chars, required, indexed)
        memory_type: 记忆类型 | Bit flags for memory type (integer, defaults to 1, indexed):
            - 1 (bit 0): raw memory 原始记忆
            - 2 (bit 1): semantic memory 语义记忆
            - 4 (bit 2): episodic memory 情景记忆
            - 8 (bit 3): procedural memory 程序记忆
            例如 5 表示启用 raw + episodic
        storage_type: 存储类型 | Storage type (32 chars, defaults to "table": "table|graph", indexed)
        embd_id: 嵌入模型 ID | Embedding model ID (128 chars, required, indexed)
        tenant_embd_id: 租户嵌入模型配置 ID | Tenant's embedding model config ID (integer, indexed)
        llm_id: 聊天模型 ID | Chat model ID (128 chars, required, indexed)
        tenant_llm_id: 租户 LLM 配置 ID | Tenant's LLM config ID (integer, indexed)
        permissions: 权限 | Permission level (16 chars, defaults to "me": "me|team", indexed)
        description: 描述 | Memory description (text field, nullable)
        memory_size: 记忆大小 | Memory size limit in bytes (integer, defaults to 5242880)
        forgetting_policy: 遗忘策略 | Forgetting policy (32 chars, defaults to "FIFO": "LRU|FIFO", indexed)
        temperature: 温度 | Temperature for memory operations (float, defaults to 0.5)
        system_prompt: 系统提示词 | System prompt (text field, nullable)
        user_prompt: 用户提示词 | User prompt (text field, nullable)
    """
    id = CharField(max_length=32, primary_key=True)  # Line 1814
    name = CharField(max_length=128, null=False, index=False, help_text="Memory name")
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    memory_type = IntegerField(null=False, default=1, index=True, help_text="Bit flags (LSB->MSB): 1=raw, 2=semantic, 4=episodic, 8=procedural. E.g., 5 enables raw + episodic.")
    storage_type = CharField(max_length=32, default='table', null=False, index=True, help_text="table|graph")
    embd_id = CharField(max_length=128, null=False, index=False, help_text="embedding model ID")
    tenant_embd_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    llm_id = CharField(max_length=128, null=False, index=False, help_text="chat model ID")
    tenant_llm_id = IntegerField(null=True, help_text="id in tenant_llm", index=True)
    permissions = CharField(max_length=16, null=False, index=True, help_text="me|team", default="me")
    description = TextField(null=True, help_text="description")
    memory_size = IntegerField(default=5242880, null=False, index=False)
    forgetting_policy = CharField(max_length=32, null=False, default="FIFO", index=False, help_text="LRU|FIFO")
    temperature = FloatField(default=0.5, index=False)
    system_prompt = TextField(null=True, help_text="system prompt", index=False)
    user_prompt = TextField(null=True, help_text="user prompt", index=False)

    class Meta:  # Line 1832
        db_table = "memory"  # 数据库表名 | Database table name


# ==============================================================================
# SystemSettings Model - 系统设置模型
# ==============================================================================

class SystemSettings(DataBaseModel):  # Line 1835
    """
    系统设置模型 | System Settings Model

    存储系统级别的配置信息。
    Stores system-level configuration information.

    Attributes:
        name: 设置名称（主键） | Setting name (primary key, 128 chars)
        source: 来源 | Setting source (32 chars, required)
        data_type: 数据类型 | Data type (32 chars, required)
        value: 值 | Configuration value (text field, required, can be JSON, string, etc.)
    """
    name = CharField(max_length=128, primary_key=True)  # Line 1836
    source = CharField(max_length=32, null=False, index=False)
    data_type = CharField(max_length=32, null=False, index=False)
    value = TextField(null=False, help_text="Configuration value (JSON, string, etc.)")
    class Meta:  # Line 1840
        db_table = "system_settings"  # 数据库表名 | Database table name


# ==============================================================================
# Database Migration Functions - 数据库迁移函数
# ==============================================================================

def alter_db_add_column(migrator, table_name, column_name, column_type):  # Line 1843
    """
    添加数据库列（带错误处理）| Add database column with error handling

    尝试向表添加新列，如果列已存在则忽略特定错误。
    Attempts to add a new column to a table, ignores specific errors if column already exists.

    Args:
        migrator: 数据库迁移器 | Database migrator instance
        table_name: 表名 | Table name to modify
        column_name: 列名 | Column name to add
        column_type: 列类型 | Column type definition

    Note:
        忽略MySQL错误码1060（Duplicate column name）。
        Ignores MySQL error code 1060 (Duplicate column name).
    """
    try:
        migrate(migrator.add_column(table_name, column_name, column_type))  # 执行添加列操作 | Execute add column operation
    except OperationalError as ex:  # 捕获操作错误 | Catch operational errors
        error_codes = [1060]  # MySQL错误码：列已存在 | MySQL error code: column already exists
        error_messages = ['Duplicate column name']  # 错误消息 | Error message

        # 检查是否应该跳过错误 | Check if error should be skipped
        should_skip_error = (
                (hasattr(ex, 'args') and ex.args and ex.args[0] in error_codes) or
                (str(ex) in error_messages)
        )

        if not should_skip_error:
            logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, operation error: {ex}")

    except Exception as ex:  # 捕获其他异常 | Catch other exceptions
        logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, error: {ex}")
        pass


def alter_db_column_type(migrator, table_name, column_name, new_column_type):  # Line 1862
    """
    修改数据库列类型（带错误处理）| Alter database column type with error handling

    尝试修改列的数据类型。
    Attempts to change the data type of a column.

    Args:
        migrator: 数据库迁移器 | Database migrator instance
        table_name: 表名 | Table name to modify
        column_name: 列名 | Column name to modify
        new_column_type: 新列类型 | New column type definition
    """
    try:
        migrate(migrator.alter_column_type(table_name, column_name, new_column_type))  # 执行类型修改 | Execute type alteration
    except Exception as ex:  # 捕获异常 | Catch exceptions
        logging.critical(f"Failed to alter {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name} type, error: {ex}")
        pass


def alter_db_rename_column(migrator, table_name, old_column_name, new_column_name):  # Line 1869
    """
    重命名数据库列（带错误处理）| Rename database column with error handling

    尝试重命名列。
    Attempts to rename a column.

    Args:
        migrator: 数据库迁移器 | Database migrator instance
        table_name: 表名 | Table name to modify
        old_column_name: 旧列名 | Old column name
        new_column_name: 新列名 | New column name

    Note:
        重命名失败可能导致奇怪的错误，因此被静默处理。
        Rename failures can lead to weird errors, so they're silently handled.
    """
    try:
        migrate(migrator.rename_column(table_name, old_column_name, new_column_name))  # 执行重命名 | Execute rename
    except Exception:  # 捕获异常（静默处理）| Catch exceptions (silent handling)
        # rename fail will lead to a weired error.
        # logging.critical(f"Failed to rename {settings.DATABASE_TYPE.upper()}.{table_name} column {old_column_name} to {new_column_name}, error: {ex}")
        pass


def migrate_add_unique_email(migrator):  # Line 1877
    """
    为用户表的邮箱列添加唯一约束（幂等操作）
    Add UNIQUE constraint to user.email column (idempotent operation)

    此函数确保用户表的邮箱列具有唯一约束，处理重复邮箱并添加唯一索引。
    This function ensures the user table's email column has a UNIQUE constraint,
    handling duplicate emails and adding the unique index.

    Steps:
        1. 检查现有索引状态 | Check existing index state
        2. 重命名重复行 | Rename duplicate rows
        3. 添加唯一索引 | Add UNIQUE index

    Note:
        此操作是幂等的，可以安全地多次运行。
        This operation is idempotent and safe to run multiple times.
    """
    # step 0: check existing index state on user.email and prepare for unique constraint
    # 步骤0：检查user.email上的现有索引状态并为唯一约束做准备
    try:
        if settings.DATABASE_TYPE.upper() == "POSTGRES":  # PostgreSQL 特定处理 | PostgreSQL-specific handling
            cursor = DB.execute_sql("""
                SELECT COUNT(*)
                FROM pg_indexes
                WHERE tablename = 'user'
                  AND indexname = 'user_email'
            """)  # 检查是否已存在唯一索引 | Check if unique index already exists
            result = cursor.fetchone()
            if result and result[0] > 0:
                logging.info("UNIQUE index on user.email already exists, skipping migration")  # 已存在唯一索引，跳过 | Unique index exists, skip
                return
        else:  # MySQL 特定处理 | MySQL-specific handling
            # Fetch the first index on email: tells us both the name and whether it's unique.
            # 获取email上的第一个索引：告诉我们索引名称和是否唯一。
            # non_unique=0 means unique, non_unique=1 means non-unique.
            # non_unique=0 表示唯一，non_unique=1 表示非唯一。
            cursor = DB.execute_sql("""
                SELECT index_name, non_unique
                FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = 'user'
                  AND column_name = 'email'
                LIMIT 1
            """)  # 查询email列的索引信息 | Query index info for email column
            row = cursor.fetchone()
            if row:
                index_name, non_unique = row
                if non_unique == 0:
                    logging.info("UNIQUE index on user.email already exists, skipping migration")  # 已存在唯一索引 | Unique index exists
                    return
                # Non-unique index exists (e.g. from old peewee index=True); drop it so
                # the upcoming ADD UNIQUE INDEX does not hit MySQL error 1061 "Duplicate key name".
                # 非唯一索引存在（例如来自旧的peewee index=True）；删除它以便
                # 即将到来的 ADD UNIQUE INDEX 不会遇到 MySQL 错误 1061 "Duplicate key name"。
                DB.execute_sql(f"ALTER TABLE `user` DROP INDEX `{index_name}`")  # 删除非唯一索引 | Drop non-unique index
                logging.info(f"Dropped non-unique index '{index_name}' on user.email before adding unique index")  # 记录日志 | Log
    except Exception as ex:
        logging.warning(f"Failed to check/prepare email index on user table: {ex}, continuing with migration")  # 警告日志 | Warning log

    # step 1: rename duplicate rows so the UNIQUE constraint can be applied
    # 步骤1：重命名重复行以便可以应用UNIQUE约束
    try:
        # 查找所有重复的邮箱地址 | Find all duplicate email addresses
        duplicates = User.select(User.email).group_by(User.email).having(fn.COUNT(User.id) > 1).tuples()
        for (dup_email,) in duplicates:
            # Keep the superuser row, or the oldest row if there is no superuser
            # 保留超级用户行，如果没有超级用户则保留最旧的行
            rows = list(
                User
                    .select(User.id)
                    .where(User.email == dup_email)
                    .order_by(User.is_superuser.desc(), User.create_time.asc())  # 优先保留超级用户，其次保留最早创建的 | Prioritize superuser, then oldest creation
                    .tuples()
            )
            for (uid,) in rows[1:]:  # 跳过第一行（保留的行），重命名其余行 | Skip first row (kept), rename the rest
                new_email = f"{dup_email}_DUPLICATE_{uid[:8]}"  # 生成新邮箱地址 | Generate new email address
                User.update(email=new_email).where(User.id == uid).execute()  # 更新邮箱 | Update email
                logging.warning("Renamed duplicate user %s email to %s during migration", uid, new_email)  # 警告日志 | Warning log
    except Exception as ex:
        logging.critical("Failed to deduplicate user.email before adding UNIQUE constraint: %s", ex)  # 严重错误日志 | Critical error log
        return

    # step 2: add UNIQUE index via migrator
    # 步骤2：通过迁移器添加唯一索引
    try:
        migrate(migrator.add_index("user", ("email",), unique=True))  # 添加唯一索引 | Add unique index
    except (OperationalError, ProgrammingError) as ex:  # 捕获操作错误和编程错误 | Catch operational and programming errors
        msg = str(ex)
        # MySQL 1061 "Duplicate key name" or PostgreSQL "already exists" -> already migrated
        # MySQL 1061 "Duplicate key name" 或 PostgreSQL "already exists" -> 已迁移
        if "1061" in msg or "Duplicate key name" in msg or "already exists" in msg.lower():
            pass  # 已存在唯一约束，忽略错误 | Unique constraint exists, ignore error
        else:
            logging.critical("Failed to add UNIQUE constraint on user.email: %s", ex)  # 严重错误日志 | Critical error log
    except Exception as ex:
        logging.critical("Failed to add UNIQUE constraint on user.email: %s", ex)  # 严重错误日志 | Critical error log


def update_tenant_llm_to_id_primary_key():  # Line 1951
    """
    更新 tenant_llm 表，添加自增ID主键（分步进行）
    Add ID and set to primary key step by step for tenant_llm table.

    此函数将 tenant_llm 表的复合主键（tenant_id, llm_factory, llm_name）
    更改为新的自增ID主键，同时保留原有字段的唯一约束。
    This function changes the tenant_llm table's composite primary key
    (tenant_id, llm_factory, llm_name) to a new auto-increment ID primary key,
    while preserving the unique constraint on the original fields.

    Note:
        根据数据库类型（MySQL或PostgreSQL）使用不同的实现。
        Uses different implementations based on database type (MySQL or PostgreSQL).
    """
    if settings.DATABASE_TYPE.upper() == "POSTGRES":
        _update_tenant_llm_to_id_primary_key_postgres()  # PostgreSQL 实现 | PostgreSQL implementation
    else:
        _update_tenant_llm_to_id_primary_key_mysql()  # MySQL 实现 | MySQL implementation


def _update_tenant_llm_to_id_primary_key_mysql():  # Line 1959
    """
    MySQL实现：添加ID列并设置为AUTO_INCREMENT主键
    MySQL implementation: Add ID column and set as AUTO_INCREMENT primary key

    MySQL特定的实现，使用用户变量分配行号。
    MySQL-specific implementation using user variables to assign row numbers.
    """
    try:
        with DB.atomic():  # 在事务中执行 | Execute within transaction
            # 0. Check if 'id' column already exists
            # 0. 检查'id'列是否已存在
            cursor = DB.execute_sql("""
                            SELECT COLUMN_NAME
                            FROM INFORMATION_SCHEMA.COLUMNS
                            WHERE TABLE_SCHEMA = DATABASE()
                            AND TABLE_NAME = 'tenant_llm'
                            AND COLUMN_NAME = 'id'
                        """)
            if cursor.rowcount > 0:  # 列已存在，跳过 | Column exists, skip
                return

            # 1. Add nullable column
            # 1. 添加可空列
            DB.execute_sql("ALTER TABLE tenant_llm ADD COLUMN temp_id INT NULL")

            # 2. Set ID using MySQL user variables
            # 2. 使用MySQL用户变量设置ID
            DB.execute_sql("SET @row = 0;")  # 初始化行变量 | Initialize row variable
            DB.execute_sql("UPDATE tenant_llm SET temp_id = (@row := @row + 1) ORDER BY tenant_id, llm_factory, llm_name;")  # 按顺序分配行号 | Assign row numbers in order

            # 3. Drop old primary key
            # 3. 删除旧主键
            DB.execute_sql("ALTER TABLE tenant_llm DROP PRIMARY KEY")

            # 4. Update ID column to primary key with AUTO_INCREMENT
            # 4. 将ID列更新为带AUTO_INCREMENT的主键
            DB.execute_sql("""
            ALTER TABLE tenant_llm
            MODIFY COLUMN temp_id INT NOT NULL AUTO_INCREMENT PRIMARY KEY
            """)

            # 5. Add unique key
            # 5. 添加唯一键
            DB.execute_sql("""
                ALTER TABLE tenant_llm
                ADD CONSTRAINT uk_tenant_llm UNIQUE (tenant_id, llm_factory, llm_name)
            """)

            # 6. rename
            # 6. 重命名
            DB.execute_sql("ALTER TABLE tenant_llm RENAME COLUMN temp_id TO id")

            logging.info("Successfully updated tenant_llm to id primary key.")  # 成功日志 | Success log

    except Exception as e:  # 捕获异常 | Catch exceptions
        logging.error(str(e))  # 错误日志 | Error log
        cursor = DB.execute_sql("""
                                    SELECT COLUMN_NAME
                                    FROM INFORMATION_SCHEMA.COLUMNS
                                    WHERE TABLE_SCHEMA = DATABASE()
                                    AND TABLE_NAME = 'tenant_llm'
                                    AND COLUMN_NAME = 'temp_id'
                                """)
        if cursor.rowcount > 0:  # 清理失败的迁移 | Clean up failed migration
            DB.execute_sql("ALTER TABLE tenant_llm DROP COLUMN temp_id")  # 删除临时列 | Drop temporary column


def _update_tenant_llm_to_id_primary_key_postgres():  # Line 2014
    """
    PostgreSQL实现：添加SERIAL主键列到tenant_llm
    PostgreSQL implementation: Add SERIAL primary key column to tenant_llm

    PostgreSQL特定的实现，使用序列和ROW_NUMBER()窗口函数。
    PostgreSQL-specific implementation using sequences and ROW_NUMBER() window function.
    """
    try:
        with DB.atomic():  # 在事务中执行 | Execute within transaction
            # 0. Check if 'id' column already exists
            # 0. 检查'id'列是否已存在
            cursor = DB.execute_sql("""
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_catalog = current_database()
                            AND table_name = 'tenant_llm'
                            AND column_name = 'id'
                        """)
            if cursor.rowcount > 0:  # 列已存在，跳过 | Column exists, skip
                return

            # 1. Add nullable integer column
            # 1. 添加可空整数列
            DB.execute_sql("ALTER TABLE tenant_llm ADD COLUMN temp_id INTEGER NULL")

            # 2. Assign sequential row numbers ordered consistently
            # 2. 按一致顺序分配连续行号
            DB.execute_sql("""
                UPDATE tenant_llm
                SET temp_id = subq.rn
                FROM (
                    SELECT ctid,
                           ROW_NUMBER() OVER (ORDER BY tenant_id, llm_factory, llm_name) AS rn
                    FROM tenant_llm
                ) AS subq
                WHERE tenant_llm.ctid = subq.ctid
            """)  # 使用窗口函数分配行号 | Use window function to assign row numbers

            # 3. Drop old composite primary key constraint
            # 3. 删除旧的复合主键约束
            cursor = DB.execute_sql("""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_catalog = current_database()
                  AND table_name = 'tenant_llm'
                  AND constraint_type = 'PRIMARY KEY'
            """)
            row = cursor.fetchone()
            if row:
                DB.execute_sql(f'ALTER TABLE tenant_llm DROP CONSTRAINT "{row[0]}"')  # 删除主键约束 | Drop primary key constraint

            # 4. Make temp_id NOT NULL and create a sequence for it
            # 4. 将temp_id设置为NOT NULL并为其创建序列
            DB.execute_sql("ALTER TABLE tenant_llm ALTER COLUMN temp_id SET NOT NULL")  # 设置为非空 | Set to NOT NULL
            DB.execute_sql("CREATE SEQUENCE IF NOT EXISTS tenant_llm_id_seq")  # 创建序列 | Create sequence
            DB.execute_sql("""
                SELECT setval('tenant_llm_id_seq', COALESCE((SELECT MAX(temp_id) FROM tenant_llm), 0))
            """)  # 设置序列起始值 | Set sequence start value
            DB.execute_sql("ALTER TABLE tenant_llm ALTER COLUMN temp_id SET DEFAULT nextval('tenant_llm_id_seq')")  # 设置默认值 | Set default value
            DB.execute_sql("ALTER SEQUENCE tenant_llm_id_seq OWNED BY tenant_llm.temp_id")  # 关联序列 | Associate sequence
            DB.execute_sql("ALTER TABLE tenant_llm ADD PRIMARY KEY (temp_id)")  # 添加主键 | Add primary key

            # 5. Add unique constraint
            # 5. 添加唯一约束
            DB.execute_sql("""
                ALTER TABLE tenant_llm
                ADD CONSTRAINT uk_tenant_llm UNIQUE (tenant_id, llm_factory, llm_name)
            """)

            # 6. Rename temp_id to id
            # 6. 将temp_id重命名为id
            DB.execute_sql("ALTER TABLE tenant_llm RENAME COLUMN temp_id TO id")

            logging.info("Successfully updated tenant_llm to id primary key (PostgreSQL).")  # 成功日志 | Success log

    except Exception as e:  # 捕获异常 | Catch exceptions
        logging.error(str(e))  # 错误日志 | Error log
        cursor = DB.execute_sql("""
                                    SELECT column_name
                                    FROM information_schema.columns
                                    WHERE table_catalog = current_database()
                                    AND table_name = 'tenant_llm'
                                    AND COLUMN_NAME = 'temp_id'
                                """)
        if cursor.rowcount > 0:  # 清理失败的迁移 | Clean up failed migration
            DB.execute_sql("ALTER TABLE tenant_llm DROP COLUMN temp_id")  # 删除临时列 | Drop temporary column


def migrate_db():  # Line 2090
    """
    执行数据库迁移 | Execute database migrations

    主迁移函数，按顺序执行所有数据库架构更新。
    Main migration function that executes all database schema updates in sequence.

    Note:
        在执行期间禁用错误日志以减少噪音。
        Disables error logging during execution to reduce noise.
    """
    logging.disable(logging.ERROR)  # 禁用错误日志 | Disable error logging
    migrator = DatabaseMigrator[settings.DATABASE_TYPE.upper()].value(DB)  # 获取迁移器 | Get migrator

    # File表迁移 | File table migrations
    alter_db_add_column(migrator, "file", "source_type", CharField(max_length=128, null=False, default="", help_text="where dose this document come from", index=True))
    # Tenant表迁移 | Tenant table migrations
    alter_db_add_column(migrator, "tenant", "rerank_id", CharField(max_length=128, null=False, default="BAAI/bge-reranker-v2-m3", help_text="default rerank model ID"))
    alter_db_add_column(migrator, "dialog", "rerank_id", CharField(max_length=128, null=False, default="", help_text="default rerank model ID"))
    alter_db_column_type(migrator, "dialog", "top_k", IntegerField(default=1024))
    alter_db_add_column(migrator, "tenant_llm", "api_key", CharField(max_length=2048, null=True, help_text="API KEY", index=True))
    alter_db_add_column(migrator, "api_token", "source", CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True))
    alter_db_add_column(migrator, "tenant", "tts_id", CharField(max_length=256, null=True, help_text="default tts model ID", index=True))
    alter_db_add_column(migrator, "api_4_conversation", "source", CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True))
    alter_db_add_column(migrator, "task", "retry_count", IntegerField(default=0))
    alter_db_column_type(migrator, "api_token", "dialog_id", CharField(max_length=32, null=True, index=True))
    alter_db_add_column(migrator, "tenant_llm", "max_tokens", IntegerField(default=8192, index=True))
    alter_db_add_column(migrator, "api_4_conversation", "dsl", JSONField(null=True, default={}))
    alter_db_add_column(migrator, "knowledgebase", "pagerank", IntegerField(default=0, index=False))
    alter_db_add_column(migrator, "api_token", "beta", CharField(max_length=255, null=True, index=True))
    alter_db_add_column(migrator, "task", "digest", TextField(null=True, help_text="task digest", default=""))
    alter_db_add_column(migrator, "task", "chunk_ids", LongTextField(null=True, help_text="chunk ids", default=""))
    alter_db_add_column(migrator, "conversation", "user_id", CharField(max_length=255, null=True, help_text="user_id", index=True))
    alter_db_add_column(migrator, "task", "task_type", CharField(max_length=32, null=False, default=""))
    alter_db_add_column(migrator, "task", "priority", IntegerField(default=0))
    alter_db_add_column(migrator, "user_canvas", "permission", CharField(max_length=16, null=False, help_text="me|team", default="me", index=True))
    alter_db_add_column(migrator, "user_canvas", "release", BooleanField(null=False, help_text="is released", default=False, index=True))
    alter_db_add_column(migrator, "llm", "is_tools", BooleanField(null=False, help_text="support tools", default=False))
    alter_db_add_column(migrator, "mcp_server", "variables", JSONField(null=True, help_text="MCP Server variables", default=dict))
    alter_db_rename_column(migrator, "task", "process_duation", "process_duration")  # 修复拼写错误 | Fix typo
    alter_db_rename_column(migrator, "document", "process_duation", "process_duration")  # 修复拼写错误 | Fix typo
    alter_db_add_column(migrator, "document", "suffix", CharField(max_length=32, null=False, default="", help_text="The real file extension suffix", index=True))
    alter_db_add_column(migrator, "api_4_conversation", "errors", TextField(null=True, help_text="errors"))
    alter_db_add_column(migrator, "dialog", "meta_data_filter", JSONField(null=True, default={}))
    alter_db_column_type(migrator, "canvas_template", "title", JSONField(null=True, default=dict, help_text="Canvas title"))
    alter_db_column_type(migrator, "canvas_template", "description", JSONField(null=True, default=dict, help_text="Canvas description"))
    alter_db_add_column(migrator, "user_canvas", "canvas_category", CharField(max_length=32, null=False, default="agent_canvas", help_text="agent_canvas|dataflow_canvas", index=True))
    alter_db_add_column(migrator, "canvas_template", "canvas_category", CharField(max_length=32, null=False, default="agent_canvas", help_text="agent_canvas|dataflow_canvas", index=True))
    alter_db_add_column(migrator, "knowledgebase", "pipeline_id", CharField(max_length=32, null=True, help_text="Pipeline ID", index=True))
    alter_db_add_column(migrator, "document", "pipeline_id", CharField(max_length=32, null=True, help_text="Pipeline ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "graphrag_task_id", CharField(max_length=32, null=True, help_text="Gragh RAG task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "raptor_task_id", CharField(max_length=32, null=True, help_text="RAPTOR task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "graphrag_task_finish_at", DateTimeField(null=True))
    alter_db_add_column(migrator, "knowledgebase", "raptor_task_finish_at", CharField(null=True))
    alter_db_add_column(migrator, "knowledgebase", "mindmap_task_id", CharField(max_length=32, null=True, help_text="Mindmap task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "mindmap_task_finish_at", CharField(null=True))
    alter_db_column_type(migrator, "tenant_llm", "api_key", TextField(null=True, help_text="API KEY"))
    alter_db_add_column(migrator, "tenant_llm", "status", CharField(max_length=1, null=False, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True))
    alter_db_add_column(migrator, "connector2kb", "auto_parse", CharField(max_length=1, null=False, default="1", index=False))
    alter_db_add_column(migrator, "llm_factories", "rank", IntegerField(default=0, index=False))
    alter_db_add_column(migrator, "api_4_conversation", "name", CharField(max_length=255, null=True, help_text="conversation name", index=False))
    alter_db_add_column(migrator, "api_4_conversation", "exp_user_id", CharField(max_length=255, null=True, help_text="exp_user_id", index=True))
    # Migrate system_settings.value from CharField to TextField for longer sandbox configs
    # 将 system_settings.value 从 CharField 迁移到 TextField 以支持更长的沙箱配置
    alter_db_column_type(migrator, "system_settings", "value", TextField(null=False, help_text="Configuration value (JSON, string, etc.)"))
    alter_db_add_column(migrator, "document", "content_hash", CharField(max_length=32, null=True, help_text="xxhash128 of document content for change detection", default="", index=True))
    update_tenant_llm_to_id_primary_key()  # 更新tenant_llm主键 | Update tenant_llm primary key
    alter_db_add_column(migrator, "tenant", "tenant_llm_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_embd_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_asr_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_img2txt_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_rerank_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "tenant", "tenant_tts_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "knowledgebase", "tenant_embd_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "dialog", "tenant_llm_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "dialog", "tenant_rerank_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "memory", "tenant_embd_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "memory", "tenant_llm_id", IntegerField(null=True, help_text="id in tenant_llm", index=True))
    alter_db_add_column(migrator, "user_canvas_version", "release", BooleanField(null=False, help_text="is released", default=False, index=True))
    alter_db_add_column(migrator, "api_4_conversation", "version_title", CharField(max_length=255, null=True, help_text="canvas version title when session created", index=False))
    logging.disable(logging.NOTSET)  # 重新启用日志 | Re-enable logging
    # this is after re-enabling logging to allow logging changed user emails
    # 这是在重新启用日志之后，以允许记录更改的用户邮箱
    migrate_add_unique_email(migrator)  # 添加邮箱唯一约束 | Add email unique constraint

# End of Part 2 - Database Models Definition
# 第二部分结束 - 数据库模型定义
