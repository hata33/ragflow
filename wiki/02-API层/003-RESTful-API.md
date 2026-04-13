---
name: RESTful API
category: API层
source: api/apps/restful_apis
---

## 描述

RAGFlow 提供标准化的 RESTful API 接口，位于 `api/apps/restful_apis/` 目录。包含 7 个核心资源模块：知识库、数据集、文档、对话、分块、文件和画布。每个模块遵循 REST 设计规范，提供完整的 CRUD 操作和资源关联接口。

## API 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    RESTful API 架构                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  /api/v1/                                                   │
│      │                                                      │
│      ├── /kb/                                              │
│      │   ├── GET    /kb/{id}              → 获取知识库      │
│      │   ├── POST   /kb                    → 创建知识库      │
│      │   ├── PUT    /kb/{id}              → 更新知识库      │
│      │   ├── DELETE /kb/{id}              → 删除知识库      │
│      │   └── GET    /kb/{id}/document      → 获取文档列表    │
│      │                                                      │
│      ├── /dataset/                                         │
│      │   ├── GET    /dataset/{id}           → 获取数据集     │
│      │   ├── POST   /dataset               → 创建数据集     │
│      │   ├── PUT    /dataset/{id}           → 更新数据集     │
│      │   ├── DELETE /dataset/{id}           → 删除数据集     │
│      │   └── POST   /dataset/{id}/chunk     → 添加分块      │
│      │                                                      │
│      ├── /document/                                        │
│      │   ├── GET    /document/{id}          → 获取文档      │
│      │   ├── POST   /document               → 上传文档      │
│      │   ├── PUT    /document/{id}          → 更新文档      │
│      │   ├── DELETE /document/{id}          → 删除文档      │
│      │   └── GET    /document/{id}/chunk    → 获取分块      │
│      │                                                      │
│      ├── /dialog/                                          │
│      │   ├── GET    /dialog/{id}            → 获取对话      │
│      │   ├── POST   /dialog                → 创建对话      │
│      │   ├── POST   /dialog/{id}/message    → 发送消息      │
│      │   └── DELETE /dialog/{id}            → 删除对话      │
│      │                                                      │
│      ├── /chunk/                                           │
│      │   ├── GET    /chunk/{id}             → 获取分块      │
│      │   ├── PUT    /chunk/{id}             → 更新分块      │
│      │   └── DELETE /chunk/{id}             → 删除分块      │
│      │                                                      │
│      ├── /file/                                            │
│      │   ├── POST   /file/upload           → 上传文件      │
│      │   ├── GET    /file/{id}              → 下载文件      │
│      │   └── DELETE /file/{id}              → 删除文件      │
│      │                                                      │
│      └── /canvas/                                          │
│          ├── GET    /canvas/{id}            → 获取画布      │
│          ├── POST   /canvas                → 创建画布      │
│          ├── PUT    /canvas/{id}            → 更新画布      │
│          ├── DELETE /canvas/{id}            → 删除画布      │
│          └── POST   /canvas/{id}/run        → 运行画布      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 统一响应格式

### 成功响应

```json
{
  "code": 0,
  "message": "success",
  "data": { ... }
}
```

### 错误响应

```json
{
  "code": error_code,
  "message": "error message",
  "data": null
}
```

## API 模块详解

### 1. 知识库 API (kb.py)

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/kb/{id}` | 获取知识库详情 |
| POST | `/kb` | 创建知识库 |
| PUT | `/kb/{id}` | 更新知识库 |
| DELETE | `/kb/{id}` | 删除知识库 |
| GET | `/kb/{id}/document` | 获取知识库文档列表 |
| GET | `/kb` | 获取知识库列表 |

### 2. 数据集 API (dataset.py)

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/dataset/{id}` | 获取数据集详情 |
| POST | `/dataset` | 创建数据集 |
| PUT | `/dataset/{id}` | 更新数据集 |
| DELETE | `/dataset/{id}` | 删除数据集 |
| POST | `/dataset/{id}/chunk` | 添加分块 |
| GET | `/dataset/{id}/chunk` | 获取分块列表 |

### 3. 文档 API (document.py)

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/document/{id}` | 获取文档详情 |
| POST | `/document` | 上传文档 |
| PUT | `/document/{id}` | 更新文档 |
| DELETE | `/document/{id}` | 删除文档 |
| GET | `/document/{id}/chunk` | 获取文档分块 |
| POST | `/document/{id}/parse` | 解析文档 |

### 4. 对话 API (dialog.py)

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/dialog/{id}` | 获取对话详情 |
| POST | `/dialog` | 创建对话 |
| DELETE | `/dialog/{id}` | 删除对话 |
| POST | `/dialog/{id}/message` | 发送消息 |
| GET | `/dialog/{id}/message` | 获取消息历史 |

### 5. 分块 API (chunk.py)

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/chunk/{id}` | 获取分块详情 |
| PUT | `/chunk/{id}` | 更新分块 |
| DELETE | `/chunk/{id}` | 删除分块 |
| POST | `/chunk/search` | 搜索分块 |

### 6. 文件 API (file.py)

| 方法 | 路径 | 描述 |
|------|------|------|
| POST | `/file/upload` | 上传文件 |
| GET | `/file/{id}` | 下载文件 |
| DELETE | `/file/{id}` | 删除文件 |
| GET | `/file/{id}/preview` | 预览文件 |

### 7. 画布 API (canvas.py)

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/canvas/{id}` | 获取画布详情 |
| POST | `/canvas` | 创建画布 |
| PUT | `/canvas/{id}` | 更新画布 |
| DELETE | `/canvas/{id}` | 删除画布 |
| POST | `/canvas/{id}/run` | 运行画布 |

## 认证方式

```
Authorization: Bearer {token}

或通过 Cookie:
session_id={session_id}
```

## 分页参数

```
GET /api/v1/kb?page=1&page_size=20

响应:
{
  "code": 0,
  "data": {
    "items": [...],
    "total": 100,
    "page": 1,
    "page_size": 20
  }
}
```

## 相关文档

- [001-Flask服务器.md](./001-Flask服务器.md) - 服务器架构
- [002-蓝图系统.md](./002-蓝图系统.md) - 蓝图系统
- [004-服务层.md](./004-服务层.md) - 服务层

## 相关文件

- [`api/apps/restful_apis/__init__.py`](../api/apps/restful_apis/__init__.py) - REST API 注册
