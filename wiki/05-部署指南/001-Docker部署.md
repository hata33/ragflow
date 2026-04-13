---
name: Docker 部署
category: 部署指南
source: docker/docker-compose.yml
---

## 描述

RAGFlow 使用 Docker Compose 进行容器化部署。主编排文件 `docker/docker-compose.yml` 定义了所有服务。部署包括基础服务 (MySQL、Redis、MinIO、Elasticsearch) 和应用服务 (RAGFlow Server)。

## 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/infiniflow/ragflow.git
cd ragflow

# 2. 启动基础服务
docker compose -f docker/docker-compose-base.yml up -d

# 3. 启动完整服务
docker compose -f docker/docker-compose.yml up -d

# 4. 查看日志
docker logs -f ragflow-server
```

## 服务架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Docker 服务架构                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │   Nginx     │  │ RAGFlow     │  │   Worker    │                 │
│  │  :80/443    │  │  :9380      │  │  (异步)     │                 │
│  └─────────────┘  └─────────────┘  └─────────────┘                 │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │   MySQL     │  │   Redis     │  │ MinIO       │                 │
│  │  :3306      │  │  :6379      │  │  :9000      │                 │
│  └─────────────┘  └─────────────┘  └─────────────┘                 │
│                                                                     │
│  ┌─────────────┐                                                  │
│  │  ES / Infinity│                                                 │
│  │  :9200/9001  │                                                 │
│  └─────────────┘                                                  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## 服务列表

| 服务 | 镜像 | 端口 | 描述 |
|------|------|------|------|
| mysql | mysql:8.0 | 3306 | 数据库 |
| redis | redis:7.2 | 6379 | 缓存 |
| minio | minio/minio | 9000 | 对象存储 |
| elasticsearch | elasticsearch:8.11 | 9200 | 向量存储 |
| ragflow-server | infiniflow/ragflow:nightly | 9380 | 应用服务 |

## 配置文件

### docker-compose.yml

主编排文件，定义所有服务。

### .env

环境变量配置：

```bash
# MySQL
MYSQL_ROOT_PASSWORD=root123
MYSQL_DATABASE=ragflow
MYSQL_USER=ragflow
MYSQL_PASSWORD=ragflow123

# Redis
REDIS_PASSWORD=redis123

# MinIO
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin123

# Elasticsearch
ES_USER=elastic
ES_PASSWORD=elastic123

# RAGFlow
RAGFLOW_PORT=9380
RAGFLOW_VERSION=nightly
```

## 常用命令

```bash
# 启动服务
docker compose up -d

# 停止服务
docker compose down

# 重启服务
docker compose restart ragflow-server

# 查看日志
docker logs -f ragflow-server

# 进入容器
docker exec -it ragflow-server bash

# 更新服务
docker compose pull && docker compose up -d
```

## 数据持久化

```yaml
volumes:
  mysql_data:
  redis_data:
  minio_data:
  es_data:
```

## 相关文档

- [002-环境配置.md](./002-环境配置.md) - 环境变量
- [003-服务架构.md](./003-服务架构.md) - 服务详解
- [004-生产环境.md](./004-生产环境.md) - 生产部署

## 相关文件

- [`docker/docker-compose.yml`](../docker/docker-compose.yml) - 主编排文件
- [`docker/docker-compose-base.yml`](../docker/docker-compose-base.yml) - 基础服务
- [`docker/.env`](../docker/.env) - 环境变量
