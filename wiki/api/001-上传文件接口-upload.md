# 上传文件接口 - `/document/upload` 调用栈分析

> **文件位置**: [`../../api/apps/document_app.py:66`](../../api/apps/document_app.py)
> **路由**: `POST /api/v1/document/upload`
> **功能**: 上传文件到指定知识库

---

## 📍 接口概览

| 项目 | 说明 |
|-----|------|
| **路由** | `POST /api/v1/document/upload` |
| **认证** | 需要 (`@login_required`) |
| **必需参数** | `kb_id` (知识库ID) |
| **文件参数** | `file` (支持多文件上传) |
| **文件名限制** | UTF-8编码后不超过 `FILE_NAME_LEN_LIMIT` 字节 |

---

## 📋 完整调用栈

### 1️⃣ **路由入口层 (Quart/Flask)**

```
HTTP POST /api/v1/document/upload
    ↓
@manager.route("/upload", methods=["POST"])  # line 66
    ↓
Quart 路由分发器处理请求
```

### 2️⃣ **装饰器层 (从下到上执行)**

#### `@validate_request("kb_id")` - 参数验证
**位置**: [`../../api/utils/api_utils.py:153`](../../api/utils/api_utils.py)

```python
def validate_request(*args, **kwargs):
    # Line 186: 调用 _coerce_request_data() 获取请求数据
    input_arguments = await _coerce_request_data()

    # Line 191: 验证必需参数是否存在
    errs = process_args(input_arguments)
    if errs:
        return get_json_result(code=RetCode.ARGUMENT_ERROR, message=errs)
```

**调用链**:
```
validate_request()
  ↓
_coerce_request_data()  # line 62
  ↓
await request.form  # 获取表单数据
  ↓
验证 "kb_id" 是否存在
```

#### `@login_required` - 身份认证
**位置**: [`../../api/apps/__init__.py:167`](../../api/apps/__init__.py)

```python
def login_required(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Line 191: 加载当前用户
        user = current_user  # 触发 _load_user()
        if not user:
            raise QuartAuthUnauthorized()
        return await func(*args, **kwargs)
```

**调用链**:
```
@login_required
  ↓
current_user (LocalProxy)  # line 164
  ↓
_load_user()  # line 95
  ↓
从 Authorization header 解析 JWT/API Token
  ↓
UserService.query(access_token=..., status=VALID)  # 验证用户
```

### 3️⃣ **upload 函数主体执行**

**位置**: `api/apps/document_app.py:69-113`

```python
async def upload():
    # ========== Line 70-73: 获取并验证 kb_id ==========
    form = await request.form
    kb_id = form.get("kb_id")
    if not kb_id:
        return get_json_result(data=False, message='Lack of "KB ID"')

    # ========== Line 74-78: 获取上传的文件 ==========
    files = await request.files
    if "file" not in files:
        return get_json_result(data=False, message="No file part!")
    file_objs = files.getlist("file")

    # ========== Line 90-96: 文件名验证 ==========
    for file_obj in file_objs:
        if file_obj.filename == "":
            return get_json_result(data=False, message="No file selected!")
        if len(file_obj.filename.encode("utf-8")) > FILE_NAME_LEN_LIMIT:
            return get_json_result(data=False, message="File name too long!")

    # ========== Line 98: 获取知识库信息 ==========
    e, kb = KnowledgebaseService.get_by_id(kb_id)  # → 4️⃣
    if not e:
        raise LookupError("Can't find this dataset!")

    # ========== Line 101: 权限检查 ==========
    if not check_kb_team_permission(kb, current_user.id):  # → 5️⃣
        return get_json_result(data=False, message="No authorization.")

    # ========== Line 104-111: 执行文件上传 ==========
    err, files = await thread_pool_exec(
        FileService.upload_document, kb, file_objs, current_user.id
    )  # → 6️⃣

    if err:
        return get_json_result(data=files, message="/n".join(err))
    if not files:
        return get_json_result(data=files, message="File format issue")

    # ========== Line 113: 返回结果 ==========
    return get_json_result(data=files)
```

### 4️⃣ **KnowledgebaseService.get_by_id()**

**位置**: [`../../api/db/services/knowledgebase_service.py`](../../api/db/services/knowledgebase_service.py)

```python
@classmethod
def get_by_id(cls, kb_id):
    """
    从数据库获取知识库信息

    Returns:
        tuple: (是否成功, Knowledgebase对象或None)
    """
    with DB.connection_context():
        kb = cls.model.select().where(cls.model.id == kb_id).first()
        if kb:
            return (True, kb)
        return (False, None)
```

### 5️⃣ **check_kb_team_permission()**

**位置**: [`../../api/common/check_team_permission.py`](../../api/common/check_team_permission.py)

```python
def check_kb_team_permission(kb, user_id):
    """
    检查用户是否有权限访问该知识库

    验证逻辑:
    - 检查 kb.tenant_id 是否属于用户
    - 查询 UserTenantRelation 表验证关联关系
    """
    from api.db.services import UserTenantService

    tenants = UserTenantService.query(user_id=user_id)
    for tenant in tenants:
        if tenant.tenant_id == kb.tenant_id:
            return True
    return False
```

### 6️⃣ **FileService.upload_document()** - 核心逻辑

**位置**: [`../../api/db/services/file_service.py:432-518`](../../api/db/services/file_service.py)

这是整个上传流程的核心方法，处理文件存储和数据库记录。

```python
@classmethod
@DB.connection_context()
def upload_document(self, kb, file_objs, user_id, src="local", parent_path=None):
    """
    上传文档到知识库

    Args:
        kb: 知识库对象
        file_objs: 文件对象列表
        user_id: 用户ID
        src: 来源类型 (默认 "local")
        parent_path: 父路径 (可选)

    Returns:
        tuple: (错误列表, 成功文件列表)
    """

    # ========== 阶段1: 初始化文件夹结构 ==========
    root_folder = self.get_root_folder(user_id)  # → 7️⃣
    pf_id = root_folder["id"]
    self.init_knowledgebase_docs(pf_id, user_id)  # → 8️⃣
    kb_root_folder = self.get_kb_folder(user_id)  # → 9️⃣
    kb_folder = self.new_a_file_from_kb(
        kb.tenant_id, kb.name, kb_root_folder["id"]
    )  # → 10️⃣

    safe_parent_path = sanitize_path(parent_path)
    err, files = [], []

    # ========== 阶段2: 遍历处理每个文件 ==========
    for file in file_objs:
        doc_id = file.id if hasattr(file, "id") else get_uuid()

        # --- 检查文档是否已存在 ---
        e, doc = DocumentService.get_by_id(doc_id)  # → 11️⃣

        if e:
            # ===== 分支A: 文档已存在，执行更新 =====
            try:
                # 验证文档属于同一知识库
                if str(doc.kb_id) != str(kb.id):
                    err.append(f"{file.filename}: Document ID collision")
                    continue

                blob = file.read()
                new_hash = xxhash.xxh128(blob).hexdigest()
                old_hash = doc.content_hash or ""

                # 更新存储
                settings.STORAGE_IMPL.put(kb.id, doc.location, blob, kb.tenant_id)  # → 12️⃣

                # 更新数据库
                doc.size = len(blob)
                doc.content_hash = new_hash
                DocumentService.update_by_id(doc["id"], doc.to_dict())  # → 13️⃣

                if new_hash != old_hash:
                    files.append((doc.to_dict(), blob))
            except Exception as exc:
                err.append(f"{file.filename}: {str(exc)}")
            continue

        # ===== 分支B: 创建新文档 =====
        try:
            # 健康检查
            DocumentService.check_doc_health(kb.tenant_id, file.filename)  # → 14️⃣

            # 处理文件名重复
            filename = duplicate_name(
                DocumentService.query, name=file.filename, kb_id=kb.id
            )

            # 识别文件类型
            filetype = filename_type(filename)
            if filetype == FileType.OTHER.value:
                raise RuntimeError("Unsupported file type!")

            # 确定存储位置
            location = filename if not safe_parent_path else f"{safe_parent_path}/{filename}"
            while settings.STORAGE_IMPL.obj_exist(kb.id, location):  # → 15️⃣
                location += "_"

            # 读取并处理文件内容
            blob = file.read()
            if filetype == FileType.PDF.value:
                blob = read_potential_broken_pdf(blob)  # → 16️⃣

            # 存储文件内容
            settings.STORAGE_IMPL.put(kb.id, location, blob)  # → 17️⃣

            # 生成缩略图
            img = thumbnail_img(filename, blob)  # → 18️⃣
            thumbnail_location = ""
            if img is not None:
                thumbnail_location = f"thumbnail_{doc_id}.png"
                settings.STORAGE_IMPL.put(kb.id, thumbnail_location, img)

            # 确定解析器类型
            parser_id = self.get_parser(filetype, filename, kb.parser_id)  # → 19️⃣

            # 创建文档记录
            doc = {
                "id": doc_id,
                "kb_id": kb.id,
                "parser_id": parser_id,
                "pipeline_id": kb.pipeline_id,
                "parser_config": kb.parser_config,
                "created_by": user_id,
                "type": filetype,
                "name": filename,
                "source_type": src,
                "suffix": Path(filename).suffix.lstrip("."),
                "location": location,
                "size": len(blob),
                "thumbnail": thumbnail_location,
                "content_hash": xxhash.xxh128(blob).hexdigest(),
            }
            DocumentService.insert(doc)  # → 20️⃣

            # 关联到知识库文件夹
            FileService.add_file_from_kb(doc, kb_folder["id"], kb.tenant_id)  # → 21️⃣

            files.append((doc, blob))

        except Exception as e:
            err.append(f"{file.filename}: {str(e)}")

    return err, files
```

---

## 7️⃣-21️⃣ 关键子方法详解

### 7️⃣ get_root_folder()
**位置**: [`../../file_service.py:225`](../../file_service.py)

```python
@classmethod
@DB.connection_context()
def get_root_folder(cls, tenant_id):
    """
    获取或创建用户的根文件夹

    根文件夹特征: parent_id == id (自引用)
    """
    for file in cls.model.select().where(
        (cls.model.tenant_id == tenant_id) &
        (cls.model.parent_id == cls.model.id)
    ):
        return file.to_dict()

    # 不存在则创建
    file_id = get_uuid()
    file = {
        "id": file_id,
        "parent_id": file_id,  # 自引用表示根目录
        "tenant_id": tenant_id,
        "created_by": tenant_id,
        "name": "/",
        "type": FileType.FOLDER.value,
        "size": 0,
        "location": "",
    }
    cls.save(**file)
    return file
```

### 8️⃣ init_knowledgebase_docs()
**位置**: [`../../file_service.py:295`](../../file_service.py)

```python
@classmethod
@DB.connection_context()
def init_knowledgebase_docs(cls, root_id, tenant_id):
    """
    初始化知识库文档文件夹结构

    创建: root_id / KNOWLEDGEBASE_FOLDER_NAME / {kb_name}
    """
    # 检查是否已初始化
    for _ in cls.model.select().where(
        (cls.model.name == KNOWLEDGEBASE_FOLDER_NAME) &
        (cls.model.parent_id == root_id)
    ):
        return

    # 创建知识库根文件夹
    folder = cls.new_a_file_from_kb(tenant_id, KNOWLEDGEBASE_FOLDER_NAME, root_id)

    # 为每个知识库创建子文件夹
    for kb in Knowledgebase.select().where(Knowledgebase.tenant_id == tenant_id):
        kb_folder = cls.new_a_file_from_kb(tenant_id, kb.name, folder["id"])
        for doc in DocumentService.query(kb_id=kb.id):
            FileService.add_file_from_kb(doc.to_dict(), kb_folder["id"], tenant_id)
```

### 9️⃣ get_kb_folder()
**位置**: [`../../file_service.py:250`](../../file_service.py)

```python
@classmethod
@DB.connection_context()
def get_kb_folder(cls, tenant_id):
    """
    获取知识库文件夹

    路径: root / KNOWLEDGEBASE_FOLDER_NAME
    """
    root_folder = cls.get_root_folder(tenant_id)
    root_id = root_folder["id"]

    kb_folder = cls.model.select().where(
        (cls.model.tenant_id == tenant_id) &
        (cls.model.parent_id == root_id) &
        (cls.model.name == KNOWLEDGEBASE_FOLDER_NAME)
    ).first()

    if not kb_folder:
        kb_folder = cls.new_a_file_from_kb(tenant_id, KNOWLEDGEBASE_FOLDER_NAME, root_id)
        return kb_folder

    return kb_folder.to_dict()
```

### 10️⃣ new_a_file_from_kb()
**位置**: [`../../file_service.py:266`](../../file_service.py)

```python
@classmethod
@DB.connection_context()
def new_a_file_from_kb(cls, tenant_id, name, parent_id, ty=FileType.FOLDER.value, size=0, location=""):
    """
    从知识库创建新文件/文件夹

    如果同名文件已存在则返回现有文件，否则创建新文件
    """
    # 检查是否已存在
    for file in cls.query(tenant_id=tenant_id, parent_id=parent_id, name=name):
        return file.to_dict()

    # 创建新文件
    file = {
        "id": get_uuid(),
        "parent_id": parent_id,
        "tenant_id": tenant_id,
        "created_by": tenant_id,
        "name": name,
        "type": ty,
        "size": size,
        "location": location,
        "source_type": FileSource.KNOWLEDGEBASE,
    }
    cls.save(**file)
    return file
```

### 11️⃣ DocumentService.get_by_id()
**位置**: [`../../api/db/services/document_service.py`](../../api/db/services/document_service.py)

```python
@classmethod
def get_by_id(cls, doc_id):
    """
    从数据库获取文档

    Returns:
        tuple: (是否成功, Document对象或None)
    """
    try:
        doc = cls.model.select().where(cls.model.id == doc_id).first()
        if doc:
            return (True, doc)
        return (False, None)
    except Exception as e:
        logging.error(f"Error getting document {doc_id}: {e}")
        return (False, None)
```

### 12️⃣ STORAGE_IMPL.put()
**位置**: [`../../common/storage/`](../../common/storage/) (根据配置实现)

```python
def put(self, bucket, key, blob, tenant_id=None):
    """
    存储文件到对象存储

    Args:
        bucket: 存储桶名称 (通常是 kb_id)
        key: 存储键/路径
        blob: 文件内容 (bytes)
        tenant_id: 租户ID (可选)

    存储实现可能是:
    - MinIO
    - S3
    - Azure Blob Storage
    - 本地文件系统
    """
    # 具体实现取决于 settings.STORAGE_IMPL 的配置
```

### 13️⃣ DocumentService.update_by_id()
**位置**: [`../../api/db/services/document_service.py`](../../api/db/services/document_service.py)

```python
@classmethod
def update_by_id(cls, doc_id, data):
    """
    更新文档记录

    Args:
        doc_id: 文档ID
        data: 更新数据字典

    Returns:
        bool: 更新是否成功
    """
    try:
        num_updated = cls.model.update(**data).where(cls.model.id == doc_id).execute()
        return num_updated > 0
    except Exception as e:
        logging.error(f"Error updating document {doc_id}: {e}")
        return False
```

### 14️⃣ check_doc_health()
**位置**: [`../../api/db/services/document_service.py`](../../api/db/services/document_service.py)

```python
@staticmethod
def check_doc_health(tenant_id, filename):
    """
    检查文档健康状态

    验证:
    - 文件名格式
    - 文件大小限制
    - 租户配额
    """
    # 检查文件名
    if not filename or filename.strip() == "":
        raise ValueError("Filename cannot be empty")

    # 检查文件扩展名
    filetype = filename_type(filename)
    if filetype == FileType.OTHER.value:
        raise ValueError("Unsupported file type")

    return True
```

### 15️⃣ STORAGE_IMPL.obj_exist()
**位置**: `common/storage/`

```python
def obj_exist(self, bucket, key):
    """
    检查对象是否存在

    Args:
        bucket: 存储桶名称
        key: 对象键

    Returns:
        bool: 对象是否存在
    """
    # 具体实现取决于存储后端
```

### 16️⃣ read_potential_broken_pdf()
**位置**: [`../../api/utils/file_utils.py`](../../api/utils/file_utils.py)

```python
def read_potential_broken_pdf(blob):
    """
    修复可能损坏的PDF文件

    尝试多种方法读取PDF:
    1. 直接读取
    2. 使用 pikepdf 修复
    3. 使用 PyPDF2 修复

    Args:
        blob: PDF文件内容

    Returns:
        bytes: 修复后的PDF内容
    """
    try:
        # 尝试直接读取
        import PyPDF2
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(blob))
        if pdf_reader.is_encrypted:
            pdf_reader.decrypt("")
        writer = PyPDF2.PdfWriter()
        for page in pdf_reader.pages:
            writer.add_page(page)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()
    except Exception:
        # 如果失败，尝试其他方法
        return blob
```

### 17️⃣ STORAGE_IMPL.put() (文件内容存储)
同 12️⃣

### 18️⃣ thumbnail_img()
**位置**: [`../../api/utils/file_utils.py`](../../api/utils/file_utils.py)

```python
def thumbnail_img(filename, blob):
    """
    生成文件缩略图

    支持的文件类型:
    - PDF: 第一页
    - 图片: 缩放版本
    - Office文档: 转换后预览

    Args:
        filename: 文件名
        blob: 文件内容

    Returns:
        bytes or None: 缩略图内容
    """
    try:
        filetype = filename_type(filename)

        if filetype == FileType.PDF.value:
            # PDF 第一页
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(blob, first_page_only=True)
            if images:
                from io import BytesIO
                img_buffer = BytesIO()
                images[0].save(img_buffer, format='PNG')
                return img_buffer.getvalue()

        elif filetype == FileType.VISUAL.value:
            # 图片缩放
            from PIL import Image
            img = Image.open(BytesIO(blob))
            img.thumbnail((200, 200))
            img_buffer = BytesIO()
            img.save(img_buffer, format='PNG')
            return img_buffer.getvalue()

        return None
    except Exception:
        return None
```

### 19️⃣ get_parser()
**位置**: [`../../file_service.py:561`](../../file_service.py)

```python
@staticmethod
def get_parser(doc_type, filename, default):
    """
    根据文件类型获取解析器

    解析器类型映射:
    - VISUAL (图片) → ParserType.PICTURE
    - AURAL (音频) → ParserType.AUDIO
    - .ppt/.pptx/.pages → ParserType.PRESENTATION
    - .msg/.eml → ParserType.EMAIL
    - 其他 → default

    Args:
        doc_type: 文档类型 (FileType枚举)
        filename: 文件名
        default: 默认解析器

    Returns:
        str: 解析器类型
    """
    if doc_type == FileType.VISUAL:
        return ParserType.PICTURE.value
    if doc_type == FileType.AURAL:
        return ParserType.AUDIO.value
    if re.search(r"/.(ppt|pptx|pages)$", filename):
        return ParserType.PRESENTATION.value
    if re.search(r"/.(msg|eml)$", filename):
        return ParserType.EMAIL.value
    return default
```

### 20️⃣ DocumentService.insert()
**位置**: [`../../api/db/services/document_service.py`](../../api/db/services/document_service.py)

```python
@classmethod
def insert(cls, doc):
    """
    插入新文档记录到数据库

    Args:
        doc: 文档数据字典

    Returns:
        Document: 创建的文档对象
    """
    doc_id = doc.get("id", get_uuid())
    doc["id"] = doc_id

    with DB.connection_context():
        cls.model.insert(**doc).execute()

    return Document(**doc)
```

### 21️⃣ add_file_from_kb()
**位置**: [`../../file_service.py:404`](../../file_service.py)

```python
@classmethod
@DB.connection_context()
def add_file_from_kb(cls, doc, kb_folder_id, tenant_id):
    """
    将文档添加到知识库文件夹

    操作:
    1. 创建 File 记录
    2. 创建 File2Document 关联记录

    Args:
        doc: 文档字典
        kb_folder_id: 知识库文件夹ID
        tenant_id: 租户ID
    """
    # 检查是否已关联
    for _ in File2DocumentService.get_by_document_id(doc["id"]):
        return

    # 创建文件记录
    file = {
        "id": get_uuid(),
        "parent_id": kb_folder_id,
        "tenant_id": tenant_id,
        "created_by": tenant_id,
        "name": doc["name"],
        "type": doc["type"],
        "size": doc["size"],
        "location": doc["location"],
        "source_type": FileSource.KNOWLEDGEBASE,
    }
    cls.save(**file)

    # 创建文档关联
    File2DocumentService.save(**{
        "id": get_uuid(),
        "file_id": file["id"],
        "document_id": doc["id"]
    })
```

---

## 📊 完整流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                        HTTP POST Request                         │
│                   /api/v1/document/upload                       │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│                      Quart Router                                │
│                   @manager.route()                               │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│                   @validate_request("kb_id")                     │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  _coerce_request_data() → 获取表单数据                     │  │
│  │  process_args() → 验证 kb_id 存在                          │  │
│  └───────────────────────────────────────────────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│                      @login_required                             │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  current_user → _load_user()                               │  │
│  │  ├─ 解析 Authorization header                             │  │
│  │  ├─ UserService.query(access_token)                       │  │
│  │  └─ 返回用户对象或抛出 Unauthorized                        │  │
│  └───────────────────────────────────────────────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│                      upload() 函数主体                           │
├─────────────────────────────────────────────────────────────────┤
│  1. await request.form → 获取表单数据                           │
│  2. form.get("kb_id") → 提取知识库ID                            │
│  3. await request.files → 获取上传文件                          │
│  4. files.getlist("file") → 文件列表                            │
│  5. 验证文件名 (非空、长度限制)                                  │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│              KnowledgebaseService.get_by_id(kb_id)              │
│                     从数据库获取知识库                            │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│            check_kb_team_permission(kb, user_id)                │
│                   验证用户是否有权限访问                          │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│        thread_pool_exec(FileService.upload_document, ...)       │
│                    在线程池中执行上传                             │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│              FileService.upload_document()                      │
│                        【核心处理流程】                           │
├─────────────────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  阶段1: 初始化文件夹结构                                   │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  get_root_folder()          → 获取用户根文件夹             │  │
│  │  init_knowledgebase_docs()  → 初始化知识库目录结构         │  │
│  │  get_kb_folder()            → 获取知识库文件夹             │  │
│  │  new_a_file_from_kb()       → 创建KB子文件夹               │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              ↓                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  阶段2: 遍历处理每个文件                                   │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  FOR each file in file_objs:                              │  │
│  │    │                                                       │  │
│  │    ├─ DocumentService.get_by_id()                         │  │
│  │    │  └─ 检查文档是否已存在                                │  │
│  │    │                                                       │  │
│  │    ├─ IF 文档存在 (更新分支):                              │  │
│  │    │  ├─ file.read() → 读取文件内容                       │  │
│  │    │  ├─ xxhash.xxh128() → 计算哈希                       │  │
│  │    │  ├─ STORAGE_IMPL.put() → 更新存储                    │  │
│  │    │  └─ DocumentService.update_by_id() → 更新数据库      │  │
│  │    │                                                       │  │
│  │    └─ ELSE (创建新文档分支):                              │  │
│  │        ├─ check_doc_health() → 健康检查                   │  │
│  │        ├─ duplicate_name() → 处理重名                     │  │
│  │        ├─ filename_type() → 识别文件类型                  │  │
│  │        ├─ file.read() → 读取文件                          │  │
│  │        ├─ read_potential_broken_pdf() → PDF修复           │  │
│  │        ├─ STORAGE_IMPL.put() → 存储文件                   │  │
│  │        ├─ thumbnail_img() → 生成缩略图                    │  │
│  │        ├─ get_parser() → 获取解析器类型                   │  │
│  │        ├─ DocumentService.insert() → 插入数据库           │  │
│  │        └─ add_file_from_kb() → 关联到知识库               │  │
│  │                                                             │  │
│  └───────────────────────────────────────────────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│                    get_json_result(data=files)                  │
│                      返回 JSON 响应                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔑 关键数据结构

### 请求参数
```python
{
    "kb_id": "knowledge-base-id",  # 知识库ID (必需)
    "file": [                       # 文件列表 (支持多个)
        <FileObject 1>,
        <FileObject 2>,
        ...
    ]
}
```

### 响应格式
```python
# 成功响应
{
    "code": 0,
    "message": "success",
    "data": [
        {
            "id": "doc-id",
            "kb_id": "kb-id",
            "name": "filename.pdf",
            "type": "pdf",
            "size": 12345,
            "location": "filename.pdf",
            "thumbnail": "thumbnail_xxx.png",
            ...
        },
        ...
    ]
}

# 错误响应
{
    "code": <错误码>,
    "message": "错误信息",
    "data": null
}
```

### Document 数据结构
```python
{
    "id": "uuid",                    # 文档ID
    "kb_id": "kb-id",               # 知识库ID
    "parser_id": "naive",           # 解析器类型
    "pipeline_id": "pipeline-id",   # 管道ID
    "parser_config": {...},         # 解析器配置
    "created_by": "user-id",        # 创建者
    "type": "pdf",                  # 文件类型
    "name": "filename.pdf",         # 文件名
    "source_type": "local",         # 来源
    "suffix": "pdf",                # 后缀名
    "location": "kb-id/filename.pdf",  # 存储位置
    "size": 12345,                  # 文件大小
    "thumbnail": "thumbnail_xxx.png",  # 缩略图
    "content_hash": "xxhash128",    # 内容哈希
}
```

---

## ⚠️ 错误处理

| 错误码 | 说明 | 触发条件 |
|-------|------|---------|
| `RetCode.ARGUMENT_ERROR` | 参数错误 | 缺少 kb_id、文件为空、文件名过长 |
| `RetCode.AUTHENTICATION_ERROR` | 认证失败 | 未登录、Token无效 |
| `RetCode.SERVER_ERROR` | 服务器错误 | 文件处理失败、存储失败 |
| `RetCode.DATA_ERROR` | 数据错误 | 文件格式不支持、文件损坏 |

---

## 📝 注意事项

1. **文件名限制**: UTF-8编码后不超过 `FILE_NAME_LEN_LIMIT` 字节
2. **文件类型**: 通过 `filename_type()` 识别，不支持的类型返回错误
3. **重复文件**: 同一KB内文件名重复会自动添加下划线后缀
4. **更新机制**: 通过文件ID判断是更新还是创建
5. **存储**: 使用 `settings.STORAGE_IMPL` 抽象层，支持多种存储后端
6. **权限**: 必须是知识库所属租户的用户才能上传

---

## 🔗 相关文件

| 文件 | 说明 |
|-----|------|
| [[`../../api/apps/document_app.py`](../../api/apps/document_app.py)](../../api/apps/document_app.py) | 路由定义和处理入口 |
| [[`../../api/db/services/file_service.py`](../../api/db/services/file_service.py)](../../api/db/services/file_service.py) | 文件服务核心逻辑 |
| [[`../../api/db/services/document_service.py`](../../api/db/services/document_service.py)](../../api/db/services/document_service.py) | 文档数据库操作 |
| [[`../../api/db/services/knowledgebase_service.py`](../../api/db/services/knowledgebase_service.py)](../../api/db/services/knowledgebase_service.py) | 知识库服务 |
| [[`../../api/common/check_team_permission.py`](../../api/common/check_team_permission.py)](../../api/common/check_team_permission.py) | 权限检查 |
| [[`../../api/utils/file_utils.py`](../../api/utils/file_utils.py)](../../api/utils/file_utils.py) | 文件工具函数 |
| [`common/storage/`](../../common/storage/) | 存储抽象层实现 |

---

**更新时间**: 2026-04-15
**文档版本**: 1.0
