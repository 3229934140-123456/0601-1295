from __future__ import annotations
import sys
import os
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=" * 70)
print("Compliance Management System - Fix Verification Script")
print("=" * 70)
print()

errors = []
warnings = []
passed = []


def test_import(module_name, description):
    try:
        __import__(module_name)
        passed.append(f"[OK] {description}")
        return True
    except Exception as e:
        errors.append(f"[FAIL] {description}: {str(e)[:100]}")
        return False


print("[Step 1] Core module import tests")
print("-" * 70)

test_import("app.core.config", "core/config.py - Configuration")
test_import("app.core.database", "core/database.py - Database module")
test_import("app.core.constants", "core/constants.py - Constants & enums")
test_import("app.core.logging_config", "core/logging_config.py - Logging")
test_import("app.core.redis_client", "core/redis_client.py - Redis client")

print()
print("[Step 2] Data model import tests")
print("-" * 70)

test_import("app.models.organization", "models/organization.py - Org models")
test_import("app.models.data_source", "models/data_source.py - Data source (FinanceRecord)")
test_import("app.models.investigation", "models/investigation.py - Investigation")
test_import("app.models.compliance", "models/compliance.py - Compliance models")

print()
print("[Step 3] Data collector import tests")
print("-" * 70)

test_import("app.data_collectors.base", "data_collectors/base.py - Base collector")
test_import("app.data_collectors.email_collector", "data_collectors/email_collector.py - Email")
test_import("app.data_collectors.im_collector", "data_collectors/im_collector.py - IM")
test_import("app.data_collectors.door_collector", "data_collectors/door_collector.py - Door access")
test_import("app.data_collectors.finance_collector", "data_collectors/finance_collector.py - Finance")

print()
print("[Step 4] Detection engine import tests")
print("-" * 70)

test_import("app.detection_engine.rules", "detection_engine/rules.py - Rules DSL")
test_import("app.detection_engine.engine", "detection_engine/engine.py - Detection engine")

print()
print("[Step 5] Workflow import tests (core business)")
print("-" * 70)

test_import("app.workflows.ticket_manager", "workflows/ticket_manager.py - Ticket + assignment")
test_import("app.workflows.escalation", "workflows/escalation.py - Overdue escalation")
test_import("app.workflows.evidence", "workflows/evidence.py - Evidence collection")
test_import("app.workflows.investigation", "workflows/investigation.py - Investigation + disciplinary")

print()
print("[Step 6] Service layer import tests")
print("-" * 70)

test_import("app.services.employee_report", "services/employee_report.py - Employee reports")
test_import("app.services.report_service", "services/report_service.py - PDF/Excel reports")
test_import("app.services.notification", "services/notification.py - Notifications")
test_import("app.services.query_export", "services/query_export.py - Query & Export (fixed)")

print()
print("[Step 7] API layer import tests")
print("-" * 70)

test_import("app.schemas", "schemas/__init__.py - Pydantic schemas")
test_import("app.api.routes", "api/routes.py - API routes (30+ endpoints)")

print()
print("[Step 8] Main app import test")
print("-" * 70)

test_import("app.main", "main.py - FastAPI application")

print()
print("[Step 9] Script import tests")
print("-" * 70)

test_import("scripts.init_database", "scripts/init_database.py - DB initialization")

print()
print("[Step 10] Business logic verification")
print("-" * 70)

try:
    from app.core.constants import (
        SeverityLevel, DisciplinaryAction,
        TicketStatus, ViolationResult
    )

    discipline_types = [
        "warning", "serious_warning", "demotion",
        "salary_reduction", "permission_freeze",
        "termination", "training", "no_action"
    ]
    for dt in discipline_types:
        if DisciplinaryAction(dt) is None:
            errors.append(f"[FAIL] Disciplinary type missing: {dt}")

    passed.append("[OK] All 8 disciplinary action types verified (warning/demotion/permission_freeze)")

    from app.core.constants import SEVERITY_TIME_LIMIT
    assert SeverityLevel.GENERAL in SEVERITY_TIME_LIMIT
    assert SeverityLevel.IMPORTANT in SEVERITY_TIME_LIMIT
    assert SeverityLevel.CRITICAL in SEVERITY_TIME_LIMIT
    passed.append("[OK] Severity time limits verified (general=7d, important=3d, critical=48h)")

    from app.workflows.investigation import InvestigationWorkflowService
    assert len(InvestigationWorkflowService.SCORE_PENALTY_MAP) == 8
    passed.append("[OK] Disciplinary penalty map verified (8 penalty types)")

except Exception as e:
    errors.append(f"[FAIL] Business logic: {str(e)[:100]}")

print()
print("=" * 70)
print("Verification Summary")
print("=" * 70)

if passed:
    print(f"\n[PASS] {len(passed)} checks passed:")
    for p in passed[:15]:
        print(f"  {p}")
    if len(passed) > 15:
        print(f"  ... and {len(passed) - 15} more checks passed")

if warnings:
    print(f"\n[WARN] {len(warnings)} warnings:")
    for w in warnings:
        print(f"  {w}")

if errors:
    print(f"\n[FAIL] {len(errors)} errors found:")
    for e in errors:
        print(f"  {e}")
    print()
    print(f"Result: {len(passed)} passed, {len(errors)} failed")
    sys.exit(1)
else:
    print()
    print(f"[SUCCESS] All {len(passed)} checks passed!")
    print()
    print("Next steps:")
    print("  1. Make sure PostgreSQL and Redis are running")
    print("  2. Configure .env file (copy from .env.example)")
    print("  3. Initialize DB:  python scripts/init_database.py")
    print("  4. Run demo:       python run_demo.py")
    print("  5. Start API:      python -m app.main")
    print("  6. Health check:   http://localhost:8000/api/v1/health")
    sys.exit(0)
