# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt
"""Work-on-holiday CL credit.

When any non-Driver employee is Present (full day) or Half Day on a holiday, that
day is credited to their Casual Leave balance: +1 for a full day, +0.5 for a half
day. The qualifying / "sandwich" rule is NOT used — simply working the holiday
earns the CL. Drivers are excluded (they earn a flat OT instead, in payroll).

The credit is reconciled from Attendance: granted when the holiday shows Present /
Half Day, adjusted if that status changes, and reverted if the day is no longer
worked. A nightly job scans recent holidays as a backstop, and an Attendance
doc_event keeps it current for ad-hoc changes. Each (employee, holiday_date)
credit is recorded on a Travelling CL Holiday Credit; reconciliation never
double-credits.
"""

import frappe
from frappe.utils import getdate, add_days, nowdate, flt

CASUAL_LEAVE = "Casual Leave"
LOOKBACK_DAYS = 31    # nightly backstop scans holidays within this many days


def credit_travelling_cl_holidays():
	"""Scheduled entry point (nightly). Reconciles the work-on-holiday CL credit
	for every non-Driver with Present / Half Day attendance on a holiday in the
	recent window."""
	today = getdate(nowdate())
	window_start = add_days(today, -LOOKBACK_DAYS)
	window_end = add_days(today, -1)

	holiday_dates = frappe.db.sql_list(
		"""
		SELECT DISTINCT holiday_date FROM `tabHoliday`
		WHERE holiday_date BETWEEN %(s)s AND %(e)s
		""",
		{"s": window_start, "e": window_end},
	)
	if not holiday_dates:
		return "Work-on-holiday CL credit: no holidays in window {0}..{1}.".format(window_start, window_end)

	# Only attendance that falls ON a holiday date is a candidate.
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT employee, attendance_date AS d
		FROM `tabAttendance`
		WHERE docstatus = 1
		  AND COALESCE(false_attendance, 0) = 0
		  AND status IN ('Present', 'Half Day')
		  AND attendance_date IN %(dates)s
		""",
		{"dates": tuple(holiday_dates)},
		as_dict=True,
	)

	credited = 0
	for r in rows:
		try:
			if reevaluate_holiday_credit(r.employee, getdate(r.d)):
				credited += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Work-on-holiday CL credit failed: {0} {1}".format(r.employee, r.d),
				message=frappe.get_traceback(),
			)

	msg = "Work-on-holiday CL credit: {0} day(s) reconciled (window {1}..{2}).".format(
		credited, window_start, window_end
	)
	frappe.logger().info(msg)
	print(msg)
	return msg


def reevaluate_holiday_credits_for_range(employee, from_date, to_date):
	"""Reconcile the work-on-holiday CL credit for every holiday in
	[from_date, to_date] for this employee. Used by the event-driven triggers
	(leave / Travelling-CL changes that re-run attendance). Best-effort per
	holiday; meant to run in a background job so its per-holiday commit/rollback
	never touches the caller's transaction."""
	if not (employee and from_date and to_date):
		return
	emp = frappe.db.get_value("Employee", employee, ["holiday_list", "company"], as_dict=True)
	if not emp:
		return
	holiday_list = emp.holiday_list or _default_holiday_list(emp.company)
	if not holiday_list:
		return

	holidays = frappe.get_all(
		"Holiday",
		filters={"parent": holiday_list, "holiday_date": ["between", [getdate(from_date), getdate(to_date)]]},
		pluck="holiday_date",
	)
	for d in holidays:
		try:
			if reevaluate_holiday_credit(employee, getdate(d)):
				frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Work-on-holiday CL credit re-eval failed: {0} {1}".format(employee, d),
				message=frappe.get_traceback(),
			)


def reevaluate_holiday_credit(employee, date):
	"""Reconcile the work-on-holiday CL credit for one (employee, holiday date) to
	the amount currently earned (0 / 0.5 / 1.0). Grants when newly earned, reverts
	when the day is no longer worked, and adjusts when the amount changes (e.g.
	Present <-> Half Day). Returns 'granted', 'reverted', 'adjusted', or None.
	Idempotent."""
	date = getdate(date)
	desired = _holiday_credit_amount(employee, date)
	active = _active_credit(employee, date)

	if desired > 0 and not active:
		return "granted" if _grant_credit(employee, date, desired) else None
	if active and desired <= 0:
		_revert_credit(active, employee, date)
		return "reverted"
	if active and desired > 0 and flt(active.get("leaves")) != desired:
		# Amount changed (Present <-> Half Day): revert the old, grant the new.
		_revert_credit(active, employee, date)
		return "adjusted" if _grant_credit(employee, date, desired) else "reverted"
	return None


def _active_credit(employee, date):
	"""The live (non-reverted) Travelling CL Holiday Credit for (employee, date),
	or None."""
	return frappe.db.get_value(
		"Travelling CL Holiday Credit",
		{"employee": employee, "holiday_date": date, "status": ["!=", "Reverted"]},
		["name", "leaves", "leave_ledger_entry"],
		as_dict=True,
	)


def _holiday_credit_amount(employee, date):
	"""CL earned by (employee, date): 1.0 if Present on the holiday, 0.5 if Half
	Day, else 0.0. Drivers never earn it (they get a flat OT in payroll instead),
	and it applies only on a holiday of the employee's holiday list."""
	emp = frappe.db.get_value(
		"Employee", employee,
		["staff_type", "holiday_list", "company"], as_dict=True,
	)
	if not emp or emp.staff_type == "Driver":
		return 0.0

	holiday_list = emp.holiday_list or _default_holiday_list(emp.company)
	if not holiday_list or not _is_holiday(holiday_list, date):
		return 0.0

	att = frappe.db.get_value(
		"Attendance",
		{"employee": employee, "attendance_date": date, "docstatus": 1},
		["status", "false_attendance"], as_dict=True,
	)
	if not att or att.get("false_attendance"):
		return 0.0
	if att.status == "Present":
		return 1.0
	if att.status == "Half Day":
		return 0.5
	return 0.0


def _grant_credit(employee, date, amount):
	"""Create the CL Leave Ledger Entry (+``amount``) and its Travelling CL Holiday
	Credit record. Returns True if credited, False if no CL allocation covers the
	date."""
	lle = _credit_casual_leave(employee, date, amount)
	if not lle:
		# No Casual Leave allocation covering the date — cannot credit.
		frappe.log_error(
			title="Work-on-holiday CL credit skipped (no CL allocation): {0} {1}".format(employee, date),
			message="Employee has no submitted Casual Leave allocation covering {0}.".format(date),
		)
		return False

	doc = frappe.get_doc({
		"doctype": "Travelling CL Holiday Credit",
		"employee": employee,
		"holiday_date": date,
		"leaves": amount,
		"status": "Active",
		"leave_ledger_entry": lle,
	})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return True


def _revert_credit(credit, employee, date):
	"""Reverse a previously granted holiday credit by posting a Casual Leave Leave
	Ledger Entry for the negative of its amount (audit trail: the original stays)
	and marking the Travelling CL Holiday Credit record Reverted."""
	amount = flt(credit.get("leaves")) or 1.0
	rev = _reverse_casual_leave(employee, date, credit.get("leave_ledger_entry"), amount)
	frappe.db.set_value(
		"Travelling CL Holiday Credit", credit["name"],
		{"status": "Reverted", "reversal_leave_ledger_entry": rev},
		update_modified=False,
	)


def _is_holiday(holiday_list, date):
	return bool(frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": date}))


def _default_holiday_list(company):
	if not company:
		return None
	return frappe.db.get_value("Company", company, "default_holiday_list")


def _credit_casual_leave(employee, date, amount):
	"""Add +``amount`` to the employee's Casual Leave via a Leave Ledger Entry
	attached to their CL allocation, so get_leave_balance_on (and payroll) see the
	higher balance. Returns the Leave Ledger Entry name, or None if no CL
	allocation covers the date."""
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
		"leaves": amount,
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


def _reverse_casual_leave(employee, date, original_lle, amount):
	"""Post a -``amount`` Casual Leave Leave Ledger Entry against the same
	allocation the original credit used (falling back to the allocation covering
	the date). This pulls the balance back down while leaving the original entry in
	place as an audit trail. Returns the reversing Leave Ledger Entry name, or None
	if no allocation can be resolved."""
	alloc_name = from_d = to_d = None
	if original_lle and frappe.db.exists("Leave Ledger Entry", original_lle):
		row = frappe.db.get_value(
			"Leave Ledger Entry", original_lle,
			["transaction_name", "from_date", "to_date"], as_dict=True,
		)
		if row:
			alloc_name, from_d, to_d = row.transaction_name, row.from_date, row.to_date

	if not alloc_name:
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
		alloc_name, from_d, to_d = alloc.name, alloc.from_date, alloc.to_date

	lle = frappe.get_doc({
		"doctype": "Leave Ledger Entry",
		"employee": employee,
		"leave_type": CASUAL_LEAVE,
		"transaction_type": "Leave Allocation",
		"transaction_name": alloc_name,
		"leaves": -amount,
		"from_date": from_d,
		"to_date": to_d,
		"is_carry_forward": 0,
		"is_expired": 0,
		"is_lwp": 0,
	})
	lle.flags.ignore_permissions = True
	lle.insert(ignore_permissions=True)
	lle.submit()
	return lle.name


# ---------------------------------------------------------------------------
# Event-driven reconciliation
# ---------------------------------------------------------------------------

def reprocess_period(employee, from_date, to_date):
	"""Background reconciliation after a leave is approved/cancelled or a Travelling
	CL is approved late:

	1. Re-run attendance for the affected days that were ALREADY processed — a
	   cancelled leave day reverts to Present / Absent, an approved leave day
	   becomes On Leave, and a Travelling-CL day with a now-approved out-of-location
	   punch becomes Present. Only days with an existing submitted Attendance are
	   touched, so future / unprocessed days are left to the daily job.
	2. Re-evaluate holiday credits in the ±3-day window around the change, granting
	   newly qualifying holidays and reverting ones that no longer qualify.

	Runs in its own transaction (background job), so the commits below never affect
	the document save that scheduled it."""
	if not (employee and from_date and to_date):
		return
	_rerun_attendance_range(employee, from_date, to_date)
	reevaluate_holiday_credits_for_range(employee, from_date, to_date)


def _rerun_attendance_range(employee, from_date, to_date):
	"""Re-run attendance for each already-processed day in [from_date, to_date]."""
	from employee_self_service.employee_self_service.utils.rerun_attendance import (
		rerun_attendance_for_employee_date,
	)
	d = getdate(from_date)
	end = getdate(to_date)
	while d <= end:
		if frappe.db.exists(
			"Attendance",
			{"employee": employee, "attendance_date": d, "docstatus": 1},
		):
			try:
				rerun_attendance_for_employee_date(employee, d)
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()
				frappe.log_error(
					title="Attendance re-run after leave/travel change failed: {0} {1}".format(employee, d),
					message=frappe.get_traceback(),
				)
		d = add_days(d, 1)


def enqueue_reprocess(employee, from_date, to_date):
	"""Queue reprocess_period after the current transaction commits, so the job
	sees the final leave/attendance state and its commits stay isolated from the
	caller. No-op if the range is incomplete."""
	if not (employee and from_date and to_date):
		return
	frappe.enqueue(
		"employee_self_service.employee_self_service.utils.travelling_cl_credit.reprocess_period",
		queue="long",
		enqueue_after_commit=True,
		employee=employee,
		from_date=str(getdate(from_date)),
		to_date=str(getdate(to_date)),
	)


def on_leave_application_change(doc, method=None):
	"""doc_event for Leave Application submit / cancel: a leave changes the day's
	attendance (On Leave <-> Present/Absent), which the Attendance trigger already
	reconciles, but re-running the leave's own days keeps a cancelled leave day
	from lingering as On Leave. Queue a reconciliation over the leave's range."""
	enqueue_reprocess(doc.employee, doc.from_date, doc.to_date)


def on_attendance_change(doc, method=None):
	"""doc_event for Attendance submit / cancel / update: when the day is a holiday
	for a non-Driver employee, (re)evaluate the work-on-holiday CL credit so a
	holiday marked Present/Half Day is credited and a reverted one is pulled back.
	Gated on the date actually being a holiday so ordinary working days enqueue
	nothing."""
	employee = doc.get("employee")
	attendance_date = doc.get("attendance_date")
	if not (employee and attendance_date):
		return
	emp = frappe.db.get_value(
		"Employee", employee, ["staff_type", "holiday_list", "company"], as_dict=True,
	)
	if not emp or emp.staff_type == "Driver":
		return
	holiday_list = emp.holiday_list or _default_holiday_list(emp.company)
	if not holiday_list or not _is_holiday(holiday_list, getdate(attendance_date)):
		return
	enqueue_holiday_credit(employee, attendance_date)


def enqueue_holiday_credit(employee, date):
	"""Queue a single (employee, holiday date) credit reconciliation after commit,
	so it sees the final attendance state and its commit stays isolated from the
	caller's transaction."""
	if not (employee and date):
		return
	frappe.enqueue(
		"employee_self_service.employee_self_service.utils.travelling_cl_credit.reevaluate_holiday_credit",
		queue="long",
		enqueue_after_commit=True,
		employee=employee,
		date=str(getdate(date)),
	)


