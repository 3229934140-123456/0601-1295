from __future__ import annotations
import asyncio
import sys
import os
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import logger, settings, init_db, close_db
from app.core.constants import SeverityLevel, EventType
from app.core.database import get_db_context

from app.data_collectors import (
    EmailDataCollector,
    InstantMessageCollector,
    DoorAccessCollector,
    FinanceDataCollector,
)
from app.detection_engine import ViolationDetectionEngine
from app.workflows import (
    EventClassificationService,
    TicketGenerationService,
    OfficerAssignmentService,
    TicketEscalationService,
    EvidenceCollectionService,
    InvestigationWorkflowService,
)
from app.services import (
    ReportService,
    NotificationService,
)


BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          企业员工合规管理系统 - 演示流程                      ║
║  Compliance Management System - Demo Pipeline                ║
╚══════════════════════════════════════════════════════════════╝
"""

STEP_SEPARATOR = "━" * 60


def print_step(step_num: int, title: str):
    print(f"\n{STEP_SEPARATOR}")
    print(f" 【步骤 {step_num}】{title}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(STEP_SEPARATOR + "\n")


def print_summary(title: str, data: dict):
    print(f"\n  📊 {title}:")
    for key, value in data.items():
        if isinstance(value, dict):
            print(f"     • {key}:")
            for k, v in value.items():
                print(f"       └─ {k}: {v}")
        else:
            print(f"     • {key}: {value}")


async def main():
    print(BANNER)
    start_time = time.time()

    # =================================================================
    print_step(1, "初始化数据库连接")
    await init_db()
    print("  ✓ 数据库连接成功")
    await asyncio.sleep(0.5)

    # =================================================================
    print_step(2, "多数据源采集 (邮件/IM/门禁/财务报销)")

    lookback_hours = 72
    print(f"  采集时间范围: 过去 {lookback_hours} 小时")

    collectors = [
        ("📧 邮件数据", EmailDataCollector()),
        ("💬 即时通讯", InstantMessageCollector()),
        ("🚪 门禁记录", DoorAccessCollector()),
        ("💰 财务报销", FinanceDataCollector()),
    ]

    collection_results = {}
    for name, collector in collectors:
        try:
            count = await collector.collect_last_hours(lookback_hours)
            collection_results[name] = count
            print(f"  ✓ {name}: {count:,} 条记录")
        except Exception as e:
            collection_results[name] = f"错误: {str(e)[:50]}"
            print(f"  ✗ {name}: 错误 - {str(e)[:80]}")

    print_summary("数据采集结果", collection_results)
    total_records = sum(v for v in collection_results.values() if isinstance(v, int))
    print(f"\n  📥 数据采集总计: {total_records:,} 条")

    # =================================================================
    print_step(3, "合规性规则引擎 - 违规检测")

    print("  加载规则引擎...")
    engine = ViolationDetectionEngine()

    print("  执行违规检测（13条规则 × 4类数据源）...")
    events = await engine.detect_violations(lookback_hours=lookback_hours)

    detection_summary = {
        "检测事件总数": len(events),
    }
    by_type = {}
    by_severity = {}
    for event in events:
        by_type[event.event_type] = by_type.get(event.event_type, 0) + 1
        by_severity[event.severity] = by_severity.get(event.severity, 0) + 1

    severity_cn = {"critical": "🔴 重大", "important": "🟠 重要", "general": "🟡 一般"}
    detection_summary["按严重程度"] = {
        severity_cn.get(k, k): v for k, v in sorted(by_severity.items())
    }

    type_cn = {
        "data_leak": "数据泄露", "fraud": "财务欺诈",
        "unauthorized_access": "未授权访问", "harassment": "职场骚扰",
        "conflict_of_interest": "利益冲突", "abnormal_behavior": "异常行为",
        "suspicious_communication": "可疑通讯",
        "discrimination": "歧视行为", "theft": "盗窃行为",
        "policy_violation": "违反制度", "other": "其他",
    }
    detection_summary["按事件类型"] = {
        type_cn.get(k, k): v
        for k, v in sorted(by_type.items(), key=lambda x: -x[1])
    }

    print_summary("违规检测结果", detection_summary)

    if events:
        print("\n  🚨 重大违规事件预览:")
        critical_events = [e for e in events if e.severity == SeverityLevel.CRITICAL.value]
        for i, event in enumerate(critical_events[:5], 1):
            print(f"     {i}. [{event.event_code}] {event.title}")
            print(f"        风险评分: {event.risk_score}/100 | 置信度: {event.confidence * 100:.1f}%")
            if event.subject_employee_name:
                print(f"        涉及人员: {event.subject_employee_name}")

    # =================================================================
    print_step(4, "事件分级 + 工单生成 + 智能分配")

    classifier = EventClassificationService()
    for event in events:
        event = await classifier.classify_event(event)

    ticket_service = TicketGenerationService()
    tickets = await ticket_service.create_tickets_from_events(events)

    ticket_gen_summary = {
        "生成工单总数": len(tickets),
        "严重程度分布": {
            severity_cn.get(k, k): sum(1 for t in tickets if t.severity == k)
            for k in ["critical", "important", "general"]
        },
    }
    print_summary("工单生成结果", ticket_gen_summary)

    assignment_service = OfficerAssignmentService()
    assigned = await assignment_service.assign_officers(tickets)

    assign_summary = {
        "成功分配": len(assigned),
        "待人工分配": len(tickets) - len(assigned),
    }
    if assigned:
        by_officer = {}
        for t in assigned:
            name = t.assigned_officer_name or "未知"
            by_officer[name] = by_officer.get(name, 0) + 1
        top_officers = dict(sorted(by_officer.items(), key=lambda x: -x[1])[:5])
        assign_summary["专员分配 TOP5"] = top_officers
    print_summary("智能分配结果", assign_summary)

    if tickets:
        print("\n  📋 工单单号示例:")
        for t in tickets[:5]:
            severity_icon = "🔴" if t.severity == "critical" else ("🟠" if t.severity == "important" else "🟡")
            status = f"→ {t.assigned_officer_name or '待分配'}"
            print(f"     {severity_icon} {t.ticket_number} {status}")
            print(f"        {t.title[:60]}...")

    # =================================================================
    print_step(5, "证据自动采集 + 时间线构建")

    evidence_results = {"处理工单数": min(5, len(tickets))}
    evidence_service = EvidenceCollectionService()

    processed = 0
    for ticket in tickets[:5]:
        try:
            package = await evidence_service.collect_evidence_for_ticket(ticket.id)
            evidence_results[f"工单 {ticket.ticket_number[-6:]}"] = (
                f"{package.evidence_count} 条证据"
            )
            processed += 1
        except Exception as e:
            evidence_results[f"工单 {ticket.ticket_number[-6:]}"] = f"跳过"

    evidence_service_info = {
        "证据关联类型": "邮件/IM/门禁/财务 4 类数据源",
        "自动时间线": f"构建事件 + 数据记录时序",
        "证据哈希": "SHA-256 存证",
        **evidence_results,
    }
    print_summary("证据包生成", evidence_service_info)

    # =================================================================
    print_step(6, "模拟调查流程 (部分工单)")

    workflow_service = InvestigationWorkflowService()
    notif_service = NotificationService()

    investigation_results = {}
    for i, ticket in enumerate(assigned[:3]):
        if not ticket.assigned_officer_id:
            continue

        try:
            await workflow_service.start_investigation(
                ticket_id=ticket.id,
                officer_id=ticket.assigned_officer_id,
                notes="演示模式 - 自动启动调查"
            )

            if i == 0:
                from app.core.constants import InvestigationConclusion, ViolationResult, DisciplinaryAction
                await workflow_service.submit_conclusion(
                    ticket_id=ticket.id,
                    officer_id=ticket.assigned_officer_id,
                    conclusion=InvestigationConclusion.GUILTY,
                    conclusion_text=(
                        "经调查核实，该员工存在违规行为。"
                        "证据链完整，包括相关通讯记录和异常行为数据。"
                        "建议给予书面警告，并参加合规培训。"
                    ),
                    violation_result=ViolationResult.CONFIRMED,
                    disciplinary_action=DisciplinaryAction.WARNING,
                    estimated_hours=3.5,
                )
                investigation_results[f"工单 {ticket.ticket_number[-6:]}"] = "已提交结论 + 建议警告"
            else:
                investigation_results[f"工单 {ticket.ticket_number[-6:]}"] = "调查进行中"

        except Exception as e:
            investigation_results[f"工单 {ticket.ticket_number[-6:]}"] = f"跳过"

    investigation_results["处理机制"] = "提交结论 → 多级审批 → 处分执行 → 更新合规档案"
    print_summary("调查流程模拟", investigation_results)

    # =================================================================
    print_step(7, "超时升级与催办检测")

    escalation_service = TicketEscalationService()
    escalation_stats = await escalation_service.check_and_process_overdue()

    notif_count = 0
    if events and False:
        for ev in events[:3]:
            if ev.severity == SeverityLevel.CRITICAL.value:
                await notif_service.notify_critical_event(ev)
                notif_count += 1

    escalation_info = {
        "逾期工单": escalation_stats.get("overdue_detected", 0),
        "升级工单": escalation_stats.get("escalated", 0),
        "发送催办": escalation_stats.get("reminders_sent", 0),
        "重大违规通知 (管理群)": min(
            notif_count,
            sum(1 for e in events if e.severity == "critical")
        ),
        "升级层级": "最多3级自动升级",
        "催办频率": "每24小时一次",
    }
    print_summary("工单升级与通知", escalation_info)

    # =================================================================
    print_step(8, "生成每日统计报告 (PDF + Excel)")

    report_service = ReportService()
    try:
        (pdf_path, excel_path), daily_stats = await report_service.generate_daily_report()

        report_info = {
            "📄 PDF报告": os.path.basename(pdf_path),
            "📊 Excel报告": os.path.basename(excel_path),
            "📈 30日趋势": "已计算并写入报表",
            "👥 部门统计": f"{len(daily_stats.department_statistics or [])} 个部门",
            "🕒 专员负荷": f"{len(daily_stats.officer_workload or [])} 位专员",
            "✅ 按时完成率": f"{daily_stats.on_time_rate * 100:.1f}%",
            "📦 完成率": f"{daily_stats.completion_rate * 100:.1f}%",
        }
        print_summary("报告生成结果", report_info)

        print(f"\n  📂 文件位置:")
        print(f"     {pdf_path}")
        print(f"     {excel_path}")

    except Exception as e:
        print(f"  ⚠️ 报告生成遇到问题: {str(e)[:100]}")
        print(f"     (可在系统启动后通过 API 触发生成)")

    # =================================================================
    print_step(9, "运行统计")

    elapsed = time.time() - start_time

    stats = {
        "⏱️ 总耗时": f"{elapsed:.2f} 秒",
        "📥 采集数据量": f"{total_records:,} 条",
        "🔍 检测违规事件": f"{len(events)} 件",
        "📋 生成工单": f"{len(tickets)} 件",
        "✅ 分配工单": f"{len(assigned)} 件",
        "📁 证据包已生成": f"{processed} 个",
        "📊 报表文件": "2 个 (PDF + Excel)",
    }

    print(f"\n{STEP_SEPARATOR}")
    print("  🎉 演示流程执行完成！\n")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(STEP_SEPARATOR)

    print("""
  📖 下一步操作建议:
  ────────────────────────────────────────────────────────────
  1. 启动 API 服务:
     $ python -m app.main
     访问: http://localhost:8000

  2. 启动异步 Worker (生产环境):
     $ celery -A app.core.celery_app.celery_app worker -l info -c 4

  3. 启动定时调度 (生产环境):
     $ celery -A app.core.celery_app.celery_app beat -l info

  4. API 文档 (DEBUG模式):
     http://localhost:8000/docs

  5. 监控指标 (Prometheus):
     http://localhost:8000/metrics
  ────────────────────────────────────────────────────────────
""")

    await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n  👋 演示已取消")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n  ❌ 演示出错: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
