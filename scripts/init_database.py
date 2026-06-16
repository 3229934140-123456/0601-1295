from __future__ import annotations
import asyncio
import sys
import random
from datetime import datetime, timedelta
from typing import List
import uuid

from app.core.database import get_db_context, init_db
from app.core import logger
from app.core.constants import SeverityLevel, EventType
from app.models.organization import Department, Employee, InvestigationOfficer


DEPARTMENTS_DATA = [
    ("D001", "技术研发部", "负责公司产品研发和技术架构"),
    ("D002", "市场营销部", "市场推广、品牌建设和销售支持"),
    ("D003", "财务部", "财务核算、资金管理和审计合规"),
    ("D004", "人力资源部", "人员招聘、培训和员工关系"),
    ("D005", "合规审计部", "内部控制、合规审查和风险管理"),
    ("D006", "信息安全部", "信息安全、数据保护和技术审计"),
    ("D007", "采购供应部", "供应商管理和物料采购"),
    ("D008", "行政后勤部", "行政事务和后勤保障"),
    ("D009", "销售一部", "华东区域销售业务"),
    ("D010", "销售二部", "华南区域销售业务"),
    ("D011", "客户服务部", "客户支持和售后服务"),
    ("D012", "产品设计部", "产品规划和UI/UX设计"),
]

FIRST_NAMES = [
    "张", "王", "李", "赵", "刘", "陈", "杨", "黄", "周", "吴",
    "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗",
    "梁", "宋", "郑", "谢", "韩", "唐", "冯", "于", "董", "萧",
]
LAST_NAMES = [
    "伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "军", "洋",
    "勇", "艳", "杰", "娟", "涛", "明", "超", "秀英", "霞", "平",
    "刚", "桂英", "文", "华", "玲", "辉", "鑫", "斌", "波", "宇",
]
POSITIONS = [
    "工程师", "高级工程师", "架构师", "技术经理", "产品经理",
    "设计师", "市场专员", "销售经理", "会计", "审计师",
    "人力资源专员", "合规专员", "安全工程师", "采购专员",
    "行政助理", "项目经理", "测试工程师", "运营专员", "分析师",
    "客服专员",
]
JOB_LEVELS = ["P4", "P5", "P6", "P7", "P8", "M1", "M2", "M3", "M4"]


async def seed_departments(db, existing_codes):
    departments = {}
    for code, name, desc in DEPARTMENTS_DATA:
        if code in existing_codes:
            continue
        dept = Department(
            id=uuid.uuid4(),
            code=code,
            name=name,
            description=desc,
            is_active=True,
        )
        db.add(dept)
        departments[code] = dept

    await db.flush()

    result = await db.execute(Department.__table__.select())
    all_depts = list(result.all())
    dept_map = {row.code: row.id for row in all_depts}

    if dept_map:
        manager_code = "D005"
        if manager_code in dept_map:
            pass

    logger.info(f"Seeded {len(departments)} new departments")
    return dept_map


async def seed_employees(db, dept_map: dict, existing_ids: set, count: int = 500):
    new_employees = []
    dept_codes = list(dept_map.keys())

    for i in range(count):
        emp_id = f"EMP{(10000 + len(existing_ids) + i):05d}"
        if emp_id in existing_ids:
            continue

        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        name = first + last
        email = f"{emp_id.lower()}@company.com"

        dept_code = random.choice(dept_codes)
        dept_id = dept_map[dept_code]
        position = random.choice(POSITIONS)
        job_level = random.choice(JOB_LEVELS)
        gender = random.choice(["男", "女"])
        phone = f"1{random.randint(3, 9)}{random.randint(100000000, 999999999)}"

        hire_date = datetime(
            year=random.randint(2015, 2024),
            month=random.randint(1, 12),
            day=random.randint(1, 28)
        )

        employee = Employee(
            id=uuid.uuid4(),
            employee_id=emp_id,
            name=name,
            email=email,
            phone=phone,
            department_id=dept_id,
            position=position,
            job_level=job_level,
            gender=gender,
            employment_status="active",
            hire_date=hire_date.date(),
            permissions=["basic_access"],
        )
        db.add(employee)
        new_employees.append(employee)

    await db.flush()
    logger.info(f"Seeded {len(new_employees)} new employees")
    return new_employees


async def seed_investigation_officers(
    db, employees: List[Employee], existing_emp_ids: set
):
    officer_specs = {
        "D005": ["合规审计", "综合调查", "财务调查"],
        "D006": ["信息安全", "数据泄露", "未授权访问"],
        "D004": ["人力资源", "职场骚扰", "歧视行为"],
    }

    new_officers = []

    for employee in employees:
        if str(employee.id) in existing_emp_ids:
            continue

        emp_dept_code = None
        for code, dept_id in officer_specs.keys():
            from sqlalchemy import select as s
            result = await db.execute(s(Department).where(Department.code == code))
            dept = result.scalar_one_or_none()
            if dept and str(employee.department_id) == str(dept.id):
                emp_dept_code = code
                break

        if emp_dept_code:
            specs = officer_specs[emp_dept_code]
            officer = InvestigationOfficer(
                id=uuid.uuid4(),
                employee_id=employee.id,
                specializations=specs,
                departments_covered=[
                    employee.department_id,
                ],
                max_ticket_capacity=random.randint(8, 15),
            )
            db.add(officer)
            new_officers.append(officer)

            if len(new_officers) >= 15:
                break

    await db.flush()
    logger.info(f"Seeded {len(new_officers)} new investigation officers")
    return new_officers


async def main(employee_count: int = 500):
    logger.info("Initializing database...")
    await init_db()

    logger.info(f"Seeding base data (employees={employee_count})...")

    async with get_db_context() as db:
        from sqlalchemy import select as s

        dept_result = await db.execute(s(Department.code))
        existing_dept_codes = set(row[0] for row in dept_result.all())

        dept_map = await seed_departments(db, existing_dept_codes)
        if not dept_map:
            all_depts = await db.execute(s(Department))
            for dept in all_depts.scalars().all():
                dept_map[dept.code] = dept.id

        emp_result = await db.execute(s(Employee.employee_id))
        existing_emp_ids = set(row[0] for row in emp_result.all())

        employees = await seed_employees(db, dept_map, existing_emp_ids, employee_count)

        all_emps_result = await db.execute(
            s(Employee).where(Employee.employment_status == "active")
        )
        all_employees = list(all_emps_result.scalars().all())

        officer_result = await db.execute(s(InvestigationOfficer.employee_id))
        existing_officer_ids = set(str(row[0]) for row in officer_result.all())

        await seed_investigation_officers(db, all_employees, existing_officer_ids)

    logger.info("Database seeding complete!")
    print("\n" + "=" * 60)
    print("企业合规管理系统 - 初始化数据完成")
    print("=" * 60)
    print(f"部门数量: {len(dept_map)}")
    print(f"员工总数: ~{len(existing_emp_ids) + len(employees)}")
    print(f"调查专员: 已配置")
    print("\n下一步操作:")
    print("  1. 启动 API:   python -m app.main")
    print("  2. 启动 Worker: celery -A app.core.celery_app.celery_app worker -l info -c 4")
    print("  3. 启动 Beat:   celery -A app.core.celery_app.celery_app beat -l info")
    print("  4. 或者运行示例: python run_demo.py")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    emp_count = 500
    if len(sys.argv) > 1:
        try:
            emp_count = int(sys.argv[1])
        except ValueError:
            pass

    asyncio.run(main(emp_count))
