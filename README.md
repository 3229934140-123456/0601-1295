# 企业员工合规管理系统
# Enterprise Employee Compliance Management System

---

## 📋 系统概述

面向大型企业的全流程合规管理平台，支持**数万员工、每日数百万条数据**的高并发场景。系统从邮件、即时通讯、门禁、财务报销等多数据源自动采集员工行为数据，基于智能规则引擎识别潜在违规事件，自动分级、生成工单、分配专员、关联证据、完成调查审批闭环，全程自动审计追踪。

### ✨ 核心特性

| 模块 | 功能亮点 |
|------|---------|
| **数据采集** | 邮件IMAP/钉钉飞书Webhook/门禁API/财务API 4类数据源全量采集，含降级模拟 |
| **规则引擎** | 13条预置合规规则 + 自定义条件操作符 + 时间窗口阈值聚合 |
| **智能分级** | 多因素加权评分（事件类型+数据源数+历史违规）动态调整严重程度 |
| **工单系统** | 自动编号 + 按严重程度设置处理时限（一般7天/重要3天/重大48小时） |
| **智能分配** | 专长匹配+负载均衡+部门覆盖的多维评分算法 |
| **超时升级** | 最多3级自动升级 + 每24小时催办 |
| **证据链** | 4类数据源自动关联检索 + 时间线构建 + SHA256存证哈希 |
| **审批流程** | 重大3级/重要2级/一般1级多级审批 + 纪律处分自动执行 |
| **员工申报** | 7天时间窗口去重 + Jaccard相似度校验 + 自动合并现有工单 |
| **统计报表** | 每日凌晨自动生成PDF（8章节样式）+ Excel（4工作表）+ 30日趋势 |
| **查询导出** | 多条件组合查询 + 工单全生命周期详情 + CSV/Excel批量导出 |
| **审计追踪** | 全操作详细日志 + 操作明细导出 |
| **实时通知** | 重大违规秒级推送管理层群 + 5类IM通知模板 |

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户交互层 (Web/Portal)                        │
│     工单查询  报告下载  员工申报  调查审批  统计仪表板                  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
┌─────────────────────────────────────────────────────────────────────┐
│                     API 服务层 (FastAPI + Async)                      │
│   /api/v1/*  30+ RESTful 接口 + Prometheus 指标 + CORS/Gzip          │
└─────────────────────────────────────────────────────────────────────┘
                                    │
┌─────────────────────────────────────────────────────────────────────┐
│                    核心业务服务层 (Service + Workflow)                │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐    │
│  │ 数据采集  │  │ 规则引擎  │  │ 工单系统  │  │ 调查+审批+档案    │    │
│  │ Service  │  │ Engine   │  │ Workflow │  │ Workflow        │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘    │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐    │
│  │ 证据采集  │  │ 报表生成  │  │ 消息推送  │  │ 查询+导出+日志    │    │
│  │ Service  │  │ Service  │  │ Service  │  │ Query&Export    │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                                    │
┌─────────────────────────────────────────────────────────────────────┐
│                     异步任务层 (Celery + Redis)                      │
│  每15分钟采集 → 每30分钟检测 → 每小时工单 → 每日3点报表 + 超时巡检    │
└─────────────────────────────────────────────────────────────────────┘
                                    │
┌─────────────────────────────────────────────────────────────────────┐
│                       数据层 (PostgreSQL + Redis)                     │
│  ┌─────────────────────┐     ┌──────────────────────────────────┐   │
│  │ PostgreSQL 15+      │     │ Redis 7.x                        │   │
│  │ · 员工/组织架构      │     │ · Celery Broker (队列/调度)       │   │
│  │ · 原始数据(4类)     │     │ · 结果缓存 + 去重哈希              │   │
│  │ · 规则/事件/工单    │     │ · 分布式锁 + 计数器                │   │
│  │ · 证据/时间线       │     └──────────────────────────────────┘   │
│  │ · 审批/处分/档案    │                                            │
│  │ · 日志/统计         │   连接池: 50 基础 + 100 溢出 / 异步       │
│  └─────────────────────┘                                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 📦 环境要求

| 组件 | 最低版本 | 推荐配置 |
|------|---------|---------|
| Python | 3.10+ | 3.11 |
| PostgreSQL | 14+ | 16 |
| Redis | 6.2+ | 7.2 |
| 内存 | 4GB | 16GB+ |
| 存储 | 20GB | 500GB SSD+ |

### 1️⃣ 安装依赖

```bash
# 克隆或进入项目目录
cd compliance-system

# 创建虚拟环境 (推荐)
python -m venv venv
source venv/bin/activate      # Linux/Mac
# 或 Windows:
venv\Scripts\activate

# 安装依赖 (国内用户可加 -i https://pypi.tuna.tsinghua.edu.cn/simple)
pip install -r requirements.txt
```

### 2️⃣ 配置环境变量

```bash
# 复制模板，然后按需修改
cp .env.example .env

# 关键配置项:
# - DATABASE_URL: PostgreSQL连接串
# - REDIS_URL / CELERY_*: Redis 队列
# - MAIL_* / IM_* / DOOR_* / FINANCE_*: 4类数据源 API
# - MANAGEMENT_GROUP_WEBHOOK: 管理层通知群
```

### 3️⃣ 初始化数据库与种子数据

```bash
# 确保 PostgreSQL 和 Redis 已启动
# 然后执行初始化 (12个部门 + 500员工 + 调查专员)
python scripts/init_database.py

# 如需更多测试员工:
python scripts/init_database.py 5000
```

### 4️⃣ 一键体验全流程 (推荐)

```bash
# 运行演示脚本 - 9步完整闭环，带彩色进度和统计输出
python run_demo.py
```

演示流程预览：
```
步骤1: 初始化数据库连接
步骤2: 多数据源采集 (邮件/IM/门禁/报销) → 约数万条模拟数据
步骤3: 合规性规则引擎检测 → 生成违规事件
步骤4: 事件分级 + 工单生成 + 智能分配专员
步骤5: 证据自动采集 (4类关联) + 时间线构建
步骤6: 模拟调查 → 提交结论 → 审批流程
步骤7: 超时升级 + 催办 + 重大违规通知
步骤8: 生成 PDF + Excel 报告
步骤9: 运行统计和性能指标
```

### 5️⃣ 启动正式服务

```bash
# 终端 1: 启动 API 服务 (默认端口 8000)
python -m app.main

# 终端 2: 启动 Celery Worker (4并发)
celery -A app.core.celery_app.celery_app worker -l info -c 4 -Q data_collection,violation_detection,workflow_processing,reports,notifications,default

# 终端 3: 启动定时调度 (每天凌晨3点报表等)
celery -A app.core.celery_app.celery_app beat -l info

# 可选: 启动 Flower 监控面板
celery -A app.core.celery_app.celery_app flower --port=5555
```

### 6️⃣ 访问系统

| 端点 | 地址 | 说明 |
|------|------|------|
| **系统根路径** | http://localhost:8000 | 状态+端点清单 |
| **API 文档** | http://localhost:8000/docs | Swagger UI (仅DEBUG模式) |
| **健康检查** | http://localhost:8000/api/v1/health | 系统健康状态 |
| **仪表板** | http://localhost:8000/api/v1/dashboard | 实时统计概览 |
| **Prometheus** | http://localhost:8000/metrics | 监控指标抓取 |
| **Flower** | http://localhost:5555 | Celery 任务监控面板 |

---

## 📚 主要 API 接口

### 🎫 工单管理

```bash
# 组合查询工单 (分页)
GET  /api/v1/tickets
     ?severity=critical
     &status=under_investigation
     &department_id=xxx
     &date_from=2024-01-01T00:00:00
     &page=1&page_size=50

# 工单全生命周期详情 (事件+证据+审批+时间线+处分)
GET  /api/v1/tickets/{ticket_id}/lifecycle

# 批量导出工单
POST /api/v1/tickets/export
     { query_params: {...}, export_format: "xlsx" }
```

### 🔍 违规事件

```bash
# 检索违规事件
GET  /api/v1/events?severity=critical&has_ticket=true

# 系统常量字典
GET  /api/v1/options/constants
```

### 📝 员工主动申报

```bash
# 员工提交举报/申报
POST /api/v1/reports/submit
{
  "is_anonymous": false,
  "reporter_employee_id": "xxx",
  "reported_employee_name": "张三",
  "reported_event_type": "fraud",
  "event_date": "2024-01-15T09:30:00",
  "title": "关于涉嫌虚报报销的举报",
  "description": "详细描述...",
  "key_details": ["日期:15号", "金额:5000元"],
  "witness_names": ["李四"]
}

# 合规部处理申报
POST /api/v1/reports/process
{
  "report_id": "xxx",
  "reviewer_id": "yyy",
  "action": "merge",           # merge/dismiss/flag_for_review
  "target_ticket_id": "zzz"    # 可选:合并到现有工单
}
```

### 🕵️ 调查审批流程

```bash
# 调查专员启动调查
POST /api/v1/investigations/start?ticket_id=xxx&officer_id=yyy&notes=调查启动

# 提交调查结论 → 自动进入审批
POST /api/v1/investigations/conclusion
{
  "ticket_id": "xxx",
  "officer_id": "yyy",
  "conclusion": "guilty",                # guilty/not_guilty/insufficient_evidence/false_alarm
  "violation_result": "confirmed",        # confirmed/unconfirmed/false_positive
  "disciplinary_action": "warning",       # warning/serious_warning/demotion/salary_reduction/permission_freeze/termination/training
  "conclusion_text": "详细调查结论说明...",
  "estimated_hours": 8.5
}

# 审批人决定
POST /api/v1/investigations/approval
{
  "ticket_id": "xxx",
  "approver_id": "zzz",
  "decision": "approved",                 # approved/rejected
  "comments": "审批意见"
}
```

### 📊 报表统计

```bash
# 仪表板统计 (30日概览)
GET  /api/v1/dashboard

# 合规档案查询
GET  /api/v1/profiles?risk_level=high&has_violations=true

# 操作日志查询 + 导出
GET  /api/v1/logs?action_type=ticket_escalated
POST /api/v1/logs/export

# 下载日报
GET  /api/v1/reports/daily/download?date=2024-01-15&format=pdf
```

### ⚙️ 任务触发

```bash
# 手动触发完整 Pipeline (采集→检测→工单→升级)
POST /api/v1/pipeline/trigger
{ "lookback_hours": 24 }

# 单独触发各阶段
POST /api/v1/tasks/collect-data?hours=24
POST /api/v1/tasks/detect-violations?lookback_hours=24
POST /api/v1/tasks/generate-report
```

---

## 🧩 预置合规规则 (13条)

| 编号 | 规则名称 | 数据源 | 严重程度 | 检测逻辑 |
|------|---------|--------|---------|---------|
| R001 | 外部发送敏感附件 | 邮件 | 🔴重大 | 外部收件+敏感关键词+>1MB附件 |
| R002 | 下班后发送敏感信息 | 邮件 | 🟠重要 | 非工作时间+敏感关键词 |
| R003 | IM出现贿赂舞弊词汇 | 即时通讯 | 🔴重大 | 回扣/好处费/飞单/漏税等关键词 |
| R004 | 短时间大量删除消息 | 即时通讯 | 🟠重要 | 1小时内删除≥5条消息 |
| R005 | IM泄露内部信息 | 即时通讯 | 🔴重大 | 机密/股价/收购/并购等关键词 |
| R006 | 职场骚扰/歧视言论 | 即时通讯 | 🔴重大 | 骚扰/低俗/威胁/歧视等关键词 |
| R007 | 非工作时间进入限制区 | 门禁 | 🟠重要 | 20:00-08:00 + 服务器/财务/机密区 |
| R008 | 短时间多次门禁被拒 | 门禁 | 🟠重要 | 1小时内≥3次刷卡拒绝 |
| R009 | 限制区短时间频繁进出 | 门禁 | 🟡一般 | 2小时内限制区刷卡≥5次 |
| R010 | 报销金额远超阈值 | 财务 | 🔴重大 | 超出类别上限+high_amount标记 |
| R011 | 大额整数报销异常 | 财务 | 🟠重要 | ¥3000+且为整数额 |
| R012 | 高危类别报销审查 | 财务 | 🟠重要 | 礼品/咨询/劳务费需合规复核 |
| R013 | 短时间高频报销 | 财务 | 🟡一般 | 24h≥5笔且总额≥¥10000 |

---

## ⏱️ 处理时限矩阵

| 严重程度 | 时限 | 首次升级 | 催办频率 | 最大升级 | 审批层级 |
|---------|------|---------|---------|---------|---------|
| 🟡 **一般** | 7天 | 第5天 | 每24h | 3级 | 1级 (合规主管) |
| 🟠 **重要** | 3天 | 第2天 | 每24h | 3级 | 2级 (主管+总监) |
| 🔴 **重大** | 48小时 | 12h | 每24h | 3级 | 3级 (主管+总监+合规委员会) |

---

## 📁 目录结构

```
compliance-system/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI 主程序入口
│   │
│   ├── core/                      # 核心基础设施
│   │   ├── config.py              # 统一配置 (pydantic-settings)
│   │   ├── constants.py           # 枚举常量+时限映射
│   │   ├── database.py            # 异步 SQLAlchemy + 连接池
│   │   ├── redis_client.py        # 异步 Redis 客户端
│   │   ├── celery_app.py          # Celery 队列 + 6队列优先级
│   │   └── logging_config.py      # 结构化日志 (JSON+Console+File)
│   │
│   ├── models/                    # 18张核心数据库表
│   │   ├── organization.py        # 部门/员工/调查专员
│   │   ├── data_source.py         # 原始记录/邮件/IM/门禁/财务
│   │   ├── investigation.py       # 规则/事件/工单/证据/证据包
│   │   └── compliance.py          # 审批/档案/处分/申报/时间线/日志/统计
│   │
│   ├── schemas/                   # Pydantic 请求/响应 Schema
│   │   └── __init__.py            # 40+ 数据模型
│   │
│   ├── data_collectors/           # 4类数据源采集器
│   │   ├── base.py                # 抽象基类 + 批处理
│   │   ├── email_collector.py     # 邮件 (IMAP + 降级模拟)
│   │   ├── im_collector.py        # 即时通讯 (Webhook + 降级模拟)
│   │   ├── door_collector.py      # 门禁 (API + 降级模拟)
│   │   └── finance_collector.py   # 财务 (API + 降级模拟)
│   │
│   ├── detection_engine/          # 合规性规则引擎
│   │   ├── rules.py               # 13条预置规则 + 条件DSL
│   │   └── engine.py              # 检测/阈值聚合/去重/合并
│   │
│   ├── workflows/                 # 核心工作流
│   │   ├── ticket_manager.py      # 分级/工单生成/智能分配
│   │   ├── escalation.py          # 超时升级 + 催办机制
│   │   ├── evidence.py            # 证据关联 + 时间线
│   │   └── investigation.py       # 调查 → 审批 → 处分 → 档案
│   │
│   ├── services/                  # 业务服务
│   │   ├── employee_report.py     # 员工申报 + 去重校验
│   │   ├── report_service.py      # PDF/Excel 报表生成
│   │   ├── notification.py        # 5类 IM 通知模板
│   │   └── query_export.py        # 多维查询 + 批量导出 + 仪表板
│   │
│   ├── tasks/                     # Celery 异步任务
│   │   └── __init__.py            # 8任务+调度表+全流程Pipeline
│   │
│   └── api/                       # API 层
│       └── routes.py              # 30+ 接口路由
│
├── scripts/
│   └── init_database.py           # 数据库 + 种子数据初始化
│
├── reports/                       # PDF/Excel/导出文件 (自动创建)
├── evidence_packages/             # 证据包 + 清单 (自动创建)
├── logs/                          # 结构化日志 (自动创建)
│
├── run_demo.py                    # ✨ 一键演示脚本 (9步完整流程)
├── requirements.txt               # 25个依赖包
├── .env.example                   # 环境变量模板
└── README.md                      # 本文档
```

---

## 🔧 技术栈详解

| 技术选型 | 作用 | 优势 |
|---------|------|------|
| **FastAPI** | API框架 | 原生异步 + 自动文档 + Pydantic强类型 |
| **SQLAlchemy 2.0** | ORM | 异步会话 + 连接池(50+100溢出) |
| **Asyncpg** | PG驱动 | 纯Python最快异步PG，百万级QPS |
| **Celery 5** | 任务队列 | 6优先级队列 + 定时调度 |
| **Redis 7** | 缓存/消息 | 连接池100 + 分布式锁 |
| **ReportLab** | PDF报告 | 8章节+样式+表格 |
| **OpenPyXL** | Excel报告 | 4工作表+公式+样式 |
| **structlog** | 日志 | JSON结构化 + 按日切割 |
| **APScheduler** | 调度 | 进程内定时（可选替代Celery Beat） |
| **Prometheus** | 监控 | 请求计数+耗时+活跃工单指标 |

---

## 📈 高并发设计要点

1. **连接池**: DB池50+100溢出，Redis池100
2. **批量处理**: 所有采集器批量写入（批次1000条）
3. **异步IO**: 全部IO（DB/Redis/HTTP）均为async/await
4. **去重缓存**: 事件哈希先内存+DB联合去重
5. **队列分级**: 高优先级队列(重大事件)独立Worker
6. **索引优化**: 20+复合B树索引覆盖所有查询场景
7. **Worker隔离**: 采集/检测/工单/报表物理队列分离
8. **降级策略**: 所有外部API失败自动降级为模拟数据

---

## 📝 数据库 ER 图 (核心关系)

```
Department ─1:N─ Employee ─1:1─ ComplianceProfile
                      │              │
                      │1:N            │1:N
                      ▼              ▼
              InvestigationTicket ── ComplianceProfileHistory
               │        │
               │1:N     │1:1
               ▼        ▼
          ComplianceEvent  EvidencePackage
               │        │1:N
               │1:N     ▼
               ▼    EvidenceItem
          EventTimeline (关联事件+工单+员工)
               │
               │1:N
               ▼
          RawDataRecord (邮件/IM/门禁/财务 → 继承式5表)
               │
               │1:1
               ▼
          4类专用明细表 (Email/IM/Door/Finance Records)
```

---

## 🛟 常见问题 FAQ

**Q: 不配置真实数据源可以跑通吗？**
A: 可以。所有采集器自带降级模拟数据生成，直接运行 `run_demo.py` 即可体验完整流程。

**Q: 如何添加自定义合规规则？**
A: 系统启动时从数据库加载，也可修改 `app/detection_engine/rules.py` 中 `build_default_rules()` 函数添加。

**Q: 重大违规实时通知是推送的？**
A: 是的。检测到严重程度=重大的事件，自动通过 Webhook 推送到 `MANAGEMENT_GROUP_WEBHOOK` 配置的群。

**Q: 支持多少并发用户？**
A: 架构支持横向扩展：多Worker实例 + Nginx负载均衡 + 数据库主从。单机4Worker可支持万级日活。

---

## 📮 技术支持

- **部署**: 推荐 Docker Swarm / Kubernetes 集群化部署
- **备份**: PostgreSQL 每日物理备份 + Redis AOF 持久化
- **监控**: 对接 Prometheus + Grafana 面板
- **告警**: Prometheus Alertmanager 基于 /metrics 告警

---

## 📄 License

企业内部使用。请勿将真实敏感数据存储于测试环境。

---

> **合规是信任的基石** — 让每一个决策都有据可查、有迹可循、有责可追。
