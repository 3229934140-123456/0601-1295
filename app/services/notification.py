from __future__ import annotations
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import uuid
import json
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import logger, settings
from app.core.constants import SeverityLevel, TicketStatus, LogActionType
from app.core.database import get_db_context
from app.models.investigation import InvestigationTicket, ComplianceEvent
from app.models.compliance import SystemLog
from app.models.organization import Employee
import httpx


class NotificationService:
    def __init__(self):
        self.logger = logger.bind(module="NotificationService")

    async def notify_critical_event(self, event: ComplianceEvent) -> bool:
        if event.severity != SeverityLevel.CRITICAL.value:
            return False

        emp_info = event.subject_employee_name or "未知员工"
        dept_info = event.subject_department_name or "未知部门"
        event_time = event.event_time.strftime("%Y-%m-%d %H:%M:%S") if event.event_time else "未知时间"

        severity_cn = "重大"
        event_type_cn = self._event_type_cn(event.event_type)

        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"【{severity_cn}预警】违规事件告警",
                "text": f"""## 🔴 重大违规事件告警

> **事件编号**: `{event.event_code}`
> **事件类型**: {event_type_cn}
> **严重程度**: <font color="warning">重大</font>
> **检测时间**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}

### 👤 涉及人员
- **姓名**: {emp_info}
- **部门**: {dept_info}

### 📋 事件摘要
- **事件时间**: {event_time}
- **风险评分**: {event.risk_score}/100
- **置信度**: {event.confidence * 100:.1f}%

### 📝 详细描述
{event.title}
{event.description or ''}

### 📊 匹配数据源
{', '.join(event.matched_data_sources or [])}

---
<i>请合规部门立即跟进处理。系统已自动生成调查工单。</i>
"""
            }
        }

        success = await self._send_webhook(settings.MANAGEMENT_GROUP_WEBHOOK, message)

        async with get_db_context() as db:
            log = SystemLog(
                id=uuid.uuid4(),
                log_level="CRITICAL" if success else "ERROR",
                action_type="notification_sent",
                target_type="compliance_event",
                target_id=event.id,
                target_name=event.event_code,
                action_details={
                    "notification_type": "critical_event_management_group",
                    "webhook_url": settings.MANAGEMENT_GROUP_WEBHOOK,
                    "success": success,
                    "severity": event.severity,
                },
                status="success" if success else "failed",
            )
            db.add(log)

        if success:
            self.logger.info(
                "Critical event notification sent",
                event_code=event.event_code,
                event_type=event.event_type
            )
        else:
            self.logger.warning(
                "Critical event notification failed",
                event_code=event.event_code
            )

        return success

    async def notify_ticket_assignment(
        self, ticket: InvestigationTicket, officer: Employee
    ) -> bool:
        severity_cn = {"critical": "重大", "important": "重要", "general": "一般"}.get(ticket.severity, ticket.severity)
        deadline_str = ticket.deadline.strftime("%Y-%m-%d %H:%M") if ticket.deadline else "未设置"

        now = datetime.utcnow()
        deadline_info = ""
        if ticket.deadline:
            remaining = ticket.deadline - now
            if remaining.total_seconds() > 0:
                hours = remaining.total_seconds() / 3600
                deadline_info = f"\n⏰ **剩余时限**: {hours:.1f}小时"
            else:
                deadline_info = f"\n⚠️ **已逾期**: {abs(remaining.total_seconds())/3600:.1f}小时"

        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"【工单分配】新调查任务",
                "text": f"""## 📋 调查工单已分配给您

> **工单编号**: `{ticket.ticket_number}`
> **分配时间**: {now.strftime('%Y-%m-%d %H:%M:%S')}

### 📌 基本信息
- **严重程度**: <font color="{'warning' if ticket.severity == 'critical' else 'info' if ticket.severity == 'important' else 'comment'}">{severity_cn}</font>
- **涉及员工**: {ticket.subject_employee_name or '未知'}
- **所属部门**: {ticket.department_name or '未知'}
- **处理截止**: {deadline_str}{deadline_info}

### 📝 工单标题
{ticket.title}

### 🔗 操作指引
请立即登录合规管理系统查看详情并开始调查工作。
"""
            }
        }

        webhook = settings.IM_WEBHOOK_URL
        success = await self._send_webhook(webhook, message)

        async with get_db_context() as db:
            log = SystemLog(
                id=uuid.uuid4(),
                log_level="INFO" if success else "ERROR",
                action_type="notification_sent",
                target_type="investigation_ticket",
                target_id=ticket.id,
                target_name=ticket.ticket_number,
                user_id=officer.id,
                user_name=officer.name,
                action_details={
                    "notification_type": "ticket_assignment",
                    "officer_id": str(officer.id),
                    "success": success,
                },
                status="success" if success else "failed",
            )
            db.add(log)

        return success

    async def notify_ticket_escalation(
        self,
        ticket: InvestigationTicket,
        escalated_to: Employee,
        escalation_reason: str
    ) -> bool:
        severity_cn = {"critical": "重大", "important": "重要", "general": "一般"}.get(ticket.severity)

        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"【工单升级】处理超时提醒",
                "text": f"""## ⚠️ 调查工单自动升级

> **工单编号**: `{ticket.ticket_number}`
> **升级级别**: 第{ticket.escalation_count}级
> **升级时间**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}

### 📌 工单信息
- **严重程度**: {severity_cn}
- **涉及员工**: {ticket.subject_employee_name or '未知'}
- **原负责人**: {ticket.assigned_officer_name or '未知'}
- **当前状态**: {ticket.status}

### 🚨 升级原因
{escalation_reason}

### ⏱️ 时间信息
- **创建时间**: {ticket.created_at.strftime('%Y-%m-%d %H:%M') if ticket.created_at else '未知'}
- **截止时间**: {ticket.deadline.strftime('%Y-%m-%d %H:%M') if ticket.deadline else '未知'}
- **是否逾期**: {'是 ❌' if ticket.is_overdue else '否 ✅'}

---
请您介入协调处理，确保工单尽快完成。
"""
            }
        }

        webhook = settings.MANAGEMENT_GROUP_WEBHOOK
        success = await self._send_webhook(webhook, message)

        return success

    async def notify_daily_report(
        self,
        report_date: datetime,
        pdf_path: str,
        excel_path: str,
        summary_stats: Dict[str, Any]
    ) -> bool:
        date_str = report_date.strftime("%Y年%m月%d日")

        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"【每日报告】{date_str}合规日报",
                "text": f"""## 📊 {date_str} 合规日报已生成

> **生成时间**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}

### 📈 关键指标
| 指标 | 数值 |
| --- | --- |
| 📥 数据采集量 | {summary_stats.get('total_data_collected', 0):,} 条 |
| 🔍 检测事件数 | {summary_stats.get('total_events_detected', 0):,} 件 |
| 📋 新增工单 | {summary_stats.get('total_tickets_created', 0):,} 件 |
| ✅ 结案件数 | {summary_stats.get('total_tickets_closed', 0):,} 件 |
| ⚠️ 逾期工单 | {summary_stats.get('overdue_tickets', 0):,} 件 |
| 🔴 重大预警 | {summary_stats.get('critical_events', 0):,} 件 |
| 📊 完成率 | {summary_stats.get('completion_rate', 0)*100:.1f}% |
| ⏱️ 准时率 | {summary_stats.get('on_time_rate', 0)*100:.1f}% |

### 📎 报告文件
- 📄 PDF格式报告
- 📊 Excel格式报告

报告已推送至合规部门邮箱，请及时查阅。
"""
            }
        }

        webhook = settings.MANAGEMENT_GROUP_WEBHOOK
        success = await self._send_webhook(webhook, message)

        async with get_db_context() as db:
            log = SystemLog(
                id=uuid.uuid4(),
                log_level="INFO" if success else "ERROR",
                action_type=LogActionType.REPORT_GENERATED.value,
                target_type="daily_report",
                target_name=f"daily_{report_date.strftime('%Y%m%d')}",
                action_details={
                    "notification_type": "daily_report_notification",
                    "pdf_path": pdf_path,
                    "excel_path": excel_path,
                    "report_date": report_date.isoformat(),
                    "success": success,
                },
                status="success" if success else "failed",
            )
            db.add(log)

        return success

    async def notify_approval_pending(
        self,
        ticket: InvestigationTicket,
        approver: Employee,
        approval_level: int,
        total_levels: int
    ) -> bool:
        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"【待审批】调查工单审批请求",
                "text": f"""## 📝 审批请求待处理

> **工单编号**: `{ticket.ticket_number}`
> **审批层级**: 第{approval_level}级 / 共{total_levels}级
> **请求时间**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}

### 📌 工单概要
- **事件类型**: {ticket.event_type}
- **严重程度**: {ticket.severity}
- **涉及员工**: {ticket.subject_employee_name or '未知'}
- **调查结论**: {ticket.violation_result or '待认定'}

### 📋 调查摘要
{ticket.conclusion_summary or ticket.title}

---
请您在系统中完成审批操作，如有疑问请联系调查专员。
"""
            }
        }

        webhook = settings.IM_WEBHOOK_URL
        return await self._send_webhook(webhook, message)

    async def _send_webhook(self, webhook_url: str, payload: Dict[str, Any]) -> bool:
        if not webhook_url or "example.com" in webhook_url:
            self.logger.info(
                "Webhook notification skipped (placeholder URL)",
                webhook_preview=webhook_url[:50] if webhook_url else "empty"
            )
            return True

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )

                if response.status_code == 200:
                    try:
                        result = response.json()
                        if result.get("errcode", 0) == 0:
                            return True
                        self.logger.warning(
                            "Webhook returned error",
                            error=result.get("errmsg", "Unknown"),
                            code=result.get("errcode")
                        )
                        return False
                    except Exception:
                        return True
                else:
                    self.logger.warning(
                        "Webhook HTTP error",
                        status_code=response.status_code,
                        response=response.text[:200]
                    )
                    return False
        except Exception as e:
            self.logger.error(
                "Webhook request failed",
                webhook_preview=webhook_url[:50],
                error=str(e)
            )
            return False

    @staticmethod
    def _event_type_cn(event_type_value: str) -> str:
        from app.core.constants import EventType
        try:
            et = EventType(event_type_value)
            cn_map = {
                EventType.DATA_LEAK: "数据泄露",
                EventType.UNAUTHORIZED_ACCESS: "未授权访问",
                EventType.FRAUD: "财务欺诈",
                EventType.CONFLICT_OF_INTEREST: "利益冲突",
                EventType.HARASSMENT: "职场骚扰",
                EventType.DISCRIMINATION: "歧视行为",
                EventType.THEFT: "盗窃行为",
                EventType.POLICY_VIOLATION: "违反制度",
                EventType.SUSPICIOUS_COMMUNICATION: "可疑通讯",
                EventType.ABNORMAL_BEHAVIOR: "异常行为",
                EventType.OTHER: "其他违规",
            }
            return cn_map.get(et, event_type_value)
        except Exception:
            return event_type_value
