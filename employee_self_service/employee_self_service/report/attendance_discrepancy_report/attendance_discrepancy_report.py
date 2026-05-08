# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

"""
Attendance Discrepancy Report
=============================

Surfaces ONLY the cases where attendance for a date is likely WRONG and needs
management review. Correct attendance (e.g. Worker+Site or Field present from
a single check-in, plain Absent with no logs at all) is NOT shown.

Categories detected (mirrors employee_self_service.utils.daily_attendance and
worker_attendance routing):

1. Absent - Missing Check-out
   Employee has check-in(s) for the day but NO check-out, and was therefore
   marked Absent by the processor.
   Applies to:
     - Worker + non-Site (remarks: "Worker - Check-in only, no check-out recorded")
     - Non-Worker + non-Site (remarks: "Missing check-out (Non-Worker, Non-Site)")
   Likely action: convert to Present after confirming presence.

2. Absent - Missing Check-in
   Employee has check-out(s) but NO check-in, marked Absent.
   Applies to: Non-Worker + non-Site
   (remarks: "Missing check-in (Non-Worker, Non-Site)")

3. Absent Despite Check-in & Check-out
   Both IN and OUT logs exist but Attendance is Absent. Logic anomaly that
   should not normally happen.

4. Attendance Not Processed
   No Attendance record was created for an active employee, AND the employee
   is not on approved leave AND the date is not a holiday for them. Excludes
   `no_check_in` employees on a holiday (correctly skipped).

5. Attendance Creation Failed
   An Attendance Creation Failed Log exists for the date.

6. Pending Check-in Approval
   Employee was Skipped because at least one check-in is awaiting approval
   (approval_required=1, approved=0, rejected=0) and there is no Attendance.
"""

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, add_days, today


DISCREPANCY_MISSING_CHECKOUT = "Absent - Missing Check-out"
DISCREPANCY_MISSING_CHECKIN = "Absent - Missing Check-in"
DISCREPANCY_ABSENT_WITH_BOTH = "Absent Despite Check-in & Check-out"
DISCREPANCY_NOT_PROCESSED = "Attendance Not Processed"
DISCREPANCY_FAILED_LOG = "Attendance Creation Failed"
DISCREPANCY_PENDING_APPROVAL = "Pending Check-in Approval"


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{
			"label": "Discrepancy",
			"fieldname": "discrepancy_type",
			"fieldtype": "Data",
			"width": 240,
		},
		{
			"label": "Why",
			"fieldname": "why",
			"fieldtype": "Small Text",
			"width": 320,
		},
		{
			"label": "Employee",
			"fieldname": "employee",
			"fieldtype": "Link",
			"options": "Employee",
			"width": 130,
		},
		{
			"label": "Employee Name",
			"fieldname": "employee_name",
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"label": "Staff Type",
			"fieldname": "staff_type",
			"fieldtype": "Data",
			"width": 100,
		},
		{
			"label": "Location",
			"fieldname": "location",
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"label": "Business Vertical",
			"fieldname": "business_vertical",
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"label": "Reports To",
			"fieldname": "reporting_manager",
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"label": "Check-in",
			"fieldname": "checkin_time",
			"fieldtype": "Datetime",
			"width": 160,
		},
		{
			"label": "Check-out",
			"fieldname": "checkout_time",
			"fieldtype": "Datetime",
			"width": 160,
		},
		{
			"label": "Attendance",
			"fieldname": "attendance",
			"fieldtype": "Link",
			"options": "Attendance",
			"width": 140,
		},
		{
			"label": "Status",
			"fieldname": "attendance_status",
			"fieldtype": "Data",
			"width": 100,
		},
		{
			"label": "Suggested Action",
			"fieldname": "suggested_action",
			"fieldtype": "Small Text",
			"width": 260,
		},
	]


def get_data(filters):
	date = getdate(filters.get("date") or add_days(today(), -1))
	day_start = str(date)
	day_end = str(add_days(date, 1))
	wanted_type = (filters.get("discrepancy_type") or "").strip()
	wanted_location = (filters.get("location") or "").strip()
	wanted_staff_type = (filters.get("staff_type") or "").strip()

	emp_filters = {"status": "Active"}
	if wanted_location:
		emp_filters["location"] = wanted_location
	if wanted_staff_type:
		emp_filters["staff_type"] = wanted_staff_type

	employees = frappe.get_all(
		"Employee",
		filters=emp_filters,
		fields=[
			"name", "employee_name", "staff_type", "location", "business_vertical",
			"no_check_in", "reports_to", "company", "holiday_list",
		],
		order_by="employee_name asc",
	)
	if not employees:
		return []

	emp_ids = [e.name for e in employees]

	# Reporting manager names
	manager_ids = list({e.reports_to for e in employees if e.reports_to})
	manager_name_map = {}
	if manager_ids:
		for m in frappe.get_all(
			"Employee",
			filters={"name": ["in", manager_ids]},
			fields=["name", "employee_name"],
		):
			manager_name_map[m.name] = m.employee_name or m.name

	# Check-ins for the day with approval flags
	checkin_rows = frappe.db.sql(
		"""
		SELECT employee, time, log_type, approval_required, approved, rejected
		FROM `tabEmployee Checkin`
		WHERE time >= %(day_start)s AND time < %(day_end)s
		  AND employee IN %(employees)s
		ORDER BY time ASC
		""",
		{"day_start": day_start, "day_end": day_end, "employees": emp_ids},
		as_dict=True,
	)

	# Group by employee
	per_emp = {}
	for r in checkin_rows:
		per_emp.setdefault(r.employee, []).append(r)

	checkin_map = {}
	checkout_map = {}
	pending_approval = set()
	for emp_id, rows in per_emp.items():
		# Pending approval if any non-rejected, non-approved checkin requires approval
		for r in rows:
			if r.get("approval_required") and not r.get("approved") and not r.get("rejected"):
				pending_approval.add(emp_id)
				break
		# Effective IN/OUT (ignore rejected)
		effective = [r for r in rows if not r.get("rejected")]
		for r in effective:
			if r.log_type == "IN":
				checkin_map[emp_id] = r.time
				break
		for r in reversed(effective):
			if r.log_type == "OUT":
				checkout_map[emp_id] = r.time
				break

	# Submitted attendance
	attendance_records = frappe.get_all(
		"Attendance",
		filters={
			"attendance_date": date,
			"employee": ["in", emp_ids],
			"docstatus": 1,
		},
		fields=["name", "employee", "status"],
	)
	attendance_map = {a.employee: a for a in attendance_records}

	# Approved leave applications covering the date
	leave_apps = frappe.db.sql(
		"""
		SELECT employee
		FROM `tabLeave Application`
		WHERE status = 'Approved' AND docstatus = 1
		  AND from_date <= %(date)s AND to_date >= %(date)s
		  AND employee IN %(employees)s
		""",
		{"date": date, "employees": emp_ids},
		as_dict=True,
	)
	on_leave = {l.employee for l in leave_apps}

	# Per-employee holiday flag
	holiday_map = _build_holiday_map(employees, date)

	# Attendance Creation Failed Logs for the date
	failed_map = {}
	if frappe.db.exists("DocType", "Attendance Creation Failed Log"):
		for f in frappe.get_all(
			"Attendance Creation Failed Log",
			filters={"date": date, "employee": ["in", emp_ids]},
			fields=["employee", "reason"],
		):
			failed_map.setdefault(f.employee, []).append(f.reason or "")

	results = []

	for emp in employees:
		emp_id = emp.name
		ci = checkin_map.get(emp_id)
		co = checkout_map.get(emp_id)
		att = attendance_map.get(emp_id)
		is_on_leave = emp_id in on_leave
		is_holiday = holiday_map.get(emp_id, False)
		is_pending = emp_id in pending_approval
		has_any_checkin = emp_id in per_emp

		base = {
			"employee": emp_id,
			"employee_name": emp.employee_name,
			"staff_type": emp.staff_type or "",
			"location": emp.location or "",
			"business_vertical": emp.business_vertical or "",
			"reporting_manager": manager_name_map.get(emp.reports_to, emp.reports_to or ""),
			"checkin_time": ci,
			"checkout_time": co,
			"attendance": att.name if att else None,
			"attendance_status": att.status if att else "",
		}

		# (1) Failed log entry — always a discrepancy
		if emp_id in failed_map:
			results.append(dict(
				base,
				discrepancy_type=DISCREPANCY_FAILED_LOG,
				why="Processor raised an error: " + "; ".join(failed_map[emp_id])[:400],
				suggested_action="Review the Attendance Creation Failed Log entry, fix the underlying data, then re-run attendance for this employee/date.",
			))

		# (2) Pending approval and no attendance was created — skipped silently
		if is_pending and not att:
			results.append(dict(
				base,
				discrepancy_type=DISCREPANCY_PENDING_APPROVAL,
				why="Check-in awaiting team-leader approval. Processor skipped this employee, no Attendance was created.",
				suggested_action="Approve or reject the pending check-in, then re-run attendance for this date.",
			))

		# Cases that depend on Attendance existing
		if att:
			status = (att.status or "").strip()

			# (3) Absent despite both IN and OUT (logic anomaly)
			if ci and co and status == "Absent":
				results.append(dict(
					base,
					discrepancy_type=DISCREPANCY_ABSENT_WITH_BOTH,
					why="Both check-in and check-out exist but attendance is Absent. Likely a processing bug or a later manual change.",
					suggested_action="Investigate the Attendance record and the day's check-ins; correct the status if the employee was actually present.",
				))
			# (4a) Absent due to missing check-out (employee had check-in)
			elif ci and not co and status == "Absent":
				# Only flag for staff types where this scenario is the discrepancy:
				#   Worker + non-Site, Non-Worker non-Site (Driver/Staff/etc).
				# For Worker+Site and Field staff, no check-out is fine and they are
				# marked Present, so they won't reach here as Absent.
				results.append(dict(
					base,
					discrepancy_type=DISCREPANCY_MISSING_CHECKOUT,
					why=("Employee has check-in but no check-out, so the system marked them Absent. "
						"They may have actually worked but forgot to check out."),
					suggested_action="Confirm with the employee/team leader. If present, add a manual check-out and reprocess, or update the Attendance status.",
				))
			# (4b) Absent due to missing check-in (employee has check-out only)
			elif co and not ci and status == "Absent":
				results.append(dict(
					base,
					discrepancy_type=DISCREPANCY_MISSING_CHECKIN,
					why=("Employee has check-out but no check-in, so the system marked them Absent. "
						"The check-in may have failed or not been recorded."),
					suggested_action="Confirm presence; add a manual check-in and reprocess, or update the Attendance status.",
				))

		# (5) Attendance not processed at all
		else:
			# Skip valid no-attendance cases:
			#   - on approved leave (handled by leave)
			#   - holiday for this employee AND no check-ins for the day
			#     (no_check_in employees get nothing on holidays - this is correct)
			#   - pending approval (already reported above as its own category)
			if is_on_leave:
				continue
			if is_pending:
				continue
			if is_holiday and not has_any_checkin:
				continue

			# Build a precise "why" based on staff_type / location, mirroring the processor
			why = _explain_not_processed(emp, ci, co, has_any_checkin, is_holiday)
			results.append(dict(
				base,
				discrepancy_type=DISCREPANCY_NOT_PROCESSED,
				why=why,
				suggested_action="Re-run attendance for this employee/date. If it still fails, check the Attendance Creation Failed Log and the employee's configuration (location, staff_type, holiday list).",
			))

	if wanted_type:
		results = [r for r in results if r["discrepancy_type"] == wanted_type]

	# Order: by discrepancy type, then employee name
	type_order = {
		DISCREPANCY_FAILED_LOG: 0,
		DISCREPANCY_NOT_PROCESSED: 1,
		DISCREPANCY_ABSENT_WITH_BOTH: 2,
		DISCREPANCY_MISSING_CHECKOUT: 3,
		DISCREPANCY_MISSING_CHECKIN: 4,
		DISCREPANCY_PENDING_APPROVAL: 5,
	}
	results.sort(key=lambda r: (type_order.get(r["discrepancy_type"], 99), r["employee_name"] or ""))
	return results


def _explain_not_processed(emp, ci, co, has_any_checkin, is_holiday):
	staff_type = emp.staff_type or "Staff"
	location = emp.location or "(no location)"
	parts = [
		"No Attendance record exists for this employee on the report date.",
		"Staff Type: {0}, Location: {1}.".format(staff_type, location),
	]
	if is_holiday:
		parts.append("Date is a holiday in the employee's holiday list, but check-ins were recorded — processor should have created Present.")
	if emp.no_check_in:
		parts.append("Employee is marked 'No Check-in required' and should have been auto-marked Present.")
	if has_any_checkin and not ci and not co:
		parts.append("Only rejected/pending-approval check-ins exist for the day.")
	elif ci and co:
		parts.append("Both check-in and check-out exist; expected Present (or Half Day per location rules).")
	elif ci and not co:
		parts.append("Only check-in exists; expected Absent (non-Site) or Present (Site/Field) — neither was created.")
	elif co and not ci:
		parts.append("Only check-out exists; expected Absent — not created.")
	else:
		parts.append("No check-ins for the day; expected Absent — not created.")
	return " ".join(parts)


def _build_holiday_map(employees, date):
	"""Return {employee_name: True} when `date` is a holiday in the employee's resolved holiday list."""
	if not employees:
		return {}

	# Build per-employee holiday list (employee.holiday_list else company default)
	companies = list({e.company for e in employees if e.company})
	company_default = {}
	if companies:
		for c in frappe.get_all(
			"Company", filters={"name": ["in", companies]}, fields=["name", "default_holiday_list"]
		):
			company_default[c.name] = c.default_holiday_list

	emp_to_list = {}
	for e in employees:
		hl = e.holiday_list or company_default.get(e.company)
		if hl:
			emp_to_list[e.name] = hl

	holiday_lists = list({hl for hl in emp_to_list.values() if hl})
	if not holiday_lists:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT parent
		FROM `tabHoliday`
		WHERE parent IN %(lists)s AND holiday_date = %(date)s
		""",
		{"lists": tuple(holiday_lists), "date": date},
		as_dict=True,
	)
	lists_with_holiday = {r.parent for r in rows}
	return {emp: (hl in lists_with_holiday) for emp, hl in emp_to_list.items()}
