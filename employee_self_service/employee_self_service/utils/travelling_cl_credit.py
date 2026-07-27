# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt
"""Travelling CL — holiday CL credit (Phase 5).

When a travelling-enabled staff type does an approved OUT-OF-LOCATION check-in /
check-out on a QUALIFYING holiday, that day is credited to the employee's Casual
Leave balance (+1). Run by the nightly attendance job.

"Qualifying holiday" uses the SAME OR rule as OTPL Payroll Col H: the holiday
must have a "present-ish" day (Present / Half Day attendance, or an approved
half-day leave) within the 3 days BEFORE or the 3 days AFTER it. Because the
"after" side needs up to 3 later days to exist, the job only evaluates holidays
that are at least LOOKBACK_MIN_DAYS old (and no older than LOOKBACK_MAX_DAYS).

Idempotent: each (employee, holiday_date) credit is recorded on a Travelling CL
Holiday Credit; a second run never double-credits.
"""

import frappe
from frappe.utils import getdate, add_days, nowdate
from datetime import timedelta

CASUAL_LEAVE = "Casual Leave"
LOOKBACK_MIN_DAYS = 3     # holiday must be >= 3 days old (so its "after" window exists)
LOOKBACK_MAX_DAYS = 15    # ...and <= 15 days old (bounded scan window)


def credit_travelling_cl_holidays():
	"""Scheduled entry point (nightly). Credits +1 CL for each qualifying-holiday
	out-of-location check-in that has not been credited yet."""
	today = getdate(nowdate())
	window_start = add_days(today, -LOOKBACK_MAX_DAYS)
	window_end = add_days(today, -LOOKBACK_MIN_DAYS)

	# Approved out-of-location punches in the window, grouped to (employee, date).
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT employee, DATE(time) AS d
		FROM `tabEmployee Checkin`
		WHERE COALESCE(approval_required, 0) = 1
		  AND COALESCE(approved, 0) = 1
		  AND COALESCE(rejected, 0) = 0
		  AND DATE(time) BETWEEN %(s)s AND %(e)s
		""",
		{"s": window_start, "e": window_end},
		as_dict=True,
	)

	credited = 0
	for r in rows:
		try:
			if _process_candidate(r.employee, getdate(r.d)):
				credited += 1
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Travelling CL holiday credit failed: {0} {1}".format(r.employee, r.d),
				message=frappe.get_traceback(),
			)

	msg = "Travelling CL holiday credit: {0} day(s) credited (window {1}..{2}).".format(
		credited, window_start, window_end
	)
	frappe.logger().info(msg)
	print(msg)
	return msg


def _process_candidate(employee, date):
	"""Credit +1 CL for (employee, date) if it is an uncredited qualifying holiday
	with the employee on a travelling-enabled staff type. Returns True if credited."""
	# Already credited?
	if frappe.db.exists("Travelling CL Holiday Credit", {"employee": employee, "holiday_date": date}):
		return False

	emp = frappe.db.get_value(
		"Employee", employee,
		["location", "staff_type", "holiday_list", "company"], as_dict=True,
	)
	if not emp:
		return False

	# Enabled (checked-box) staff type only. Site Workers are handled by their own
	# travelling mechanism and are not credited here.
	from employee_self_service.employee_self_service.doctype.travelling_cl.travelling_cl import (
		is_travel_enabled_staff_type,
	)
	if not is_travel_enabled_staff_type(emp.location, emp.staff_type):
		return False

	holiday_list = emp.holiday_list or _default_holiday_list(emp.company)
	if not holiday_list or not _is_holiday(holiday_list, date):
		return False

	if not _is_qualifying_holiday(employee, date, holiday_list):
		return False

	lle = _credit_casual_leave(employee, date)
	if not lle:
		# No Casual Leave allocation covering the date — cannot credit.
		frappe.log_error(
			title="Travelling CL holiday credit skipped (no CL allocation): {0} {1}".format(employee, date),
			message="Employee has no submitted Casual Leave allocation covering {0}.".format(date),
		)
		return False

	doc = frappe.get_doc({
		"doctype": "Travelling CL Holiday Credit",
		"employee": employee,
		"holiday_date": date,
		"leaves": 1,
		"leave_ledger_entry": lle,
	})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return True


def _is_holiday(holiday_list, date):
	return bool(frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": date}))


def _default_holiday_list(company):
	if not company:
		return None
	return frappe.db.get_value("Company", company, "default_holiday_list")


def _is_qualifying_holiday(employee, date, holiday_list):
	"""OR rule (mirrors OTPL Payroll Col H): present-ish within the 3 days BEFORE
	OR the 3 days AFTER the holiday. Present-ish = submitted Present / Half Day
	attendance (excluding false attendance), or an approved half-day leave."""
	before_dates = [add_days(date, -k) for k in (1, 2, 3)]
	after_dates = [add_days(date, k) for k in (1, 2, 3)]
	window = before_dates + after_dates

	presentish = _presentish_dates(employee, window)
	before = any(getdate(d) in presentish for d in before_dates)
	after = any(getdate(d) in presentish for d in after_dates)
	return before or after


def _presentish_dates(employee, dates):
	"""Set of dates in ``dates`` on which the employee is present-ish."""
	if not dates:
		return set()
	lo, hi = min(dates), max(dates)
	out = set()

	for r in frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"docstatus": 1,
			"status": ["in", ["Present", "Half Day"]],
			"attendance_date": ["between", [getdate(lo), getdate(hi)]],
		},
		fields=["attendance_date", "false_attendance"],
	):
		if not r.get("false_attendance"):
			out.add(getdate(r.attendance_date))

	# Approved half-day OTPL Leaves count as present-ish (the other half worked).
	for r in frappe.get_all(
		"OTPL Leave",
		filters={
			"employee": employee,
			"status": "Approved",
			"half_day": 1,
			"half_day_date": ["between", [getdate(lo), getdate(hi)]],
		},
		fields=["half_day_date"],
	):
		if r.half_day_date:
			out.add(getdate(r.half_day_date))

	return out


def _credit_casual_leave(employee, date):
	"""Add +1 to the employee's Casual Leave via a Leave Ledger Entry attached to
	their CL allocation, so get_leave_balance_on (and payroll) sees the higher
	balance. Returns the Leave Ledger Entry name, or None if no CL allocation
	covers the date."""
	alloc = frappe.db.get_value(
		"Leave Allocation",
		{
			"employee": employee,
			"leave_type": CASUAL_LEAVE,
			"docstatus": 1,
			"from_date": ["<=", date],
			"to_date": [">=", date],
		},
		["name", "from_date", "to_date"],
		as_dict=True,
	)
	if not alloc:
		return None

	lle = frappe.get_doc({
		"doctype": "Leave Ledger Entry",
		"employee": employee,
		"leave_type": CASUAL_LEAVE,
		"transaction_type": "Leave Allocation",
		"transaction_name": alloc.name,
		"leaves": 1,
		"from_date": alloc.from_date,
		"to_date": alloc.to_date,
		"is_carry_forward": 0,
		"is_expired": 0,
		"is_lwp": 0,
	})
	lle.flags.ignore_permissions = True
	lle.insert(ignore_permissions=True)
	lle.submit()
	return lle.name
