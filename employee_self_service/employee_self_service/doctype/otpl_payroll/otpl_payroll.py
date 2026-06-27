# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt
"""
OTPL Payroll
============

Generates the OTPL salary sheet for the period [from_date, to_date] for
employees matching the provided filters (Staff Type, Location, Business
Line of their Sales Order). The whole calculation is done in O(N) using
a handful of grouped SQL queries (no per-employee N+1 calls).

All formulas are taken from `salary_rules` sheet of the reference Excel
`salarysheet_otpl.xlsx`.
"""

from __future__ import unicode_literals

from collections import defaultdict
from calendar import monthrange
from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, flt, getdate, get_last_day


# Constants from the salary spec
ESIC_GROSS_LIMIT = 21000.0
ESIC_EMPLOYEE_RATE = 0.0075
ESIC_EMPLOYER_FACTOR = 3.25 / 0.75
PF_EMPLOYEE_RATE = 0.12
PF_EMPLOYER_FACTOR = 13.0 / 12.0
WORKER_HARIDWAR_INCENTIVE = 200.0
# Per-present-day hours treated as standard (anything above counts as OT).
STD_HOURS_PER_DAY = 8.0
# Salary hours used to compute the per-hour rate for OT.
SALARY_HOURS_PER_DAY = 8.0


# -----------------------------------------------------------------------------
# DocType
# -----------------------------------------------------------------------------
class OTPLPayroll(Document):
	def validate(self):
		if getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("From Date cannot be after To Date"))

		self.days_in_period = (getdate(self.to_date) - getdate(self.from_date)).days + 1
		self.title = _("Payroll {0} to {1}").format(self.from_date, self.to_date)

		# Whenever the user (or the engine) edits rows manually, refresh the
		# net columns and the summary totals so the UI stays consistent with
		# the formulas in the spec.
		for row in self.employees:
			_recompute_row_nets(row)

		_set_totals(self)

	def on_submit(self):
		"""Persist the closing AL/CL into OTPL Employee Leave Balance so it
		becomes the opening for the next payroll run.
		"""
		_persist_leave_balances(self)


# -----------------------------------------------------------------------------
# Whitelisted entry points (called from the JS)
# -----------------------------------------------------------------------------
@frappe.whitelist()
def get_employees(doc):
	"""Return the list of employees matching the doc's filters.

	Pure read-only; does no calculations.
	"""
	doc = frappe.parse_json(doc) if isinstance(doc, str) else doc
	filters = _build_employee_filter(doc)
	return _select_employees(filters["sql"], filters["values"])


def _select_employees(where_sql, where_values):
	"""Internal helper: run the canonical employee SELECT used by payroll
	calculation. ``where_sql`` is appended after ``e.status='Active'``-style
	conditions already enforced by ``_build_employee_filter`` (or a raw
	predicate when fetching by explicit IDs).
	"""
	dummy_expr = "e.dummy_employee" if frappe.db.has_column("Employee", "dummy_employee") else "NULL"
	return frappe.db.sql(
		"""
		SELECT
			e.name                              AS employee,
			e.employee_name                     AS employee_name,
			e.department                        AS department,
			e.staff_type                        AS staff_type,
			e.location                          AS location,
			e.sales_order                       AS sales_order,
			e.uan_no                            AS uan_no,
			e.esi_number                        AS esic_no,
			e.advance_to_be_deducted            AS gross_salary,
			e.basic_salary                      AS basic_salary,
			COALESCE(e.no_validation, 0)        AS no_validation,
			COALESCE(esl.min_wages, 0)          AS min_wages,
			COALESCE(esl.max_wage_pf, 0)        AS max_wage_pf,
			COALESCE(esl.max_wage_esic, 0)      AS max_wage_esic,
			COALESCE(esl.late_count_for_half_day, 3) AS late_count_for_half_day,
			COALESCE(esl.late_count_for_full_day, 5) AS late_count_for_full_day,
			COALESCE(esl.treat_late_as_half_day_after, 5) AS treat_late_as_half_day_after,
			COALESCE(e.no_validation_base_salary, 0) AS no_validation_base_salary,
			{tada_expr}                         AS daily_tada,
			{hra_expr}                          AS hra_amount,
			{conv_expr}                         AS conveyance_amount,
			{tel_expr}                          AS telephone_amount,
			{dummy_expr}                        AS dummy_employee,
			so.business_line                    AS business_line
		FROM `tabEmployee` e
		LEFT JOIN `tabSales Order` so
			ON so.name = e.sales_order
		LEFT JOIN `tabESS Location` esl
			ON esl.name = e.location
		WHERE {where}
		ORDER BY e.employee_name ASC
		""".format(
			where=where_sql,
			tada_expr="COALESCE(e.daily_tada, 0)" if frappe.db.has_column("Employee", "daily_tada") else "0",
			hra_expr="COALESCE(e.hra_amount, 0)" if frappe.db.has_column("Employee", "hra_amount") else "0",
			conv_expr="COALESCE(e.conveyance_amount, 0)" if frappe.db.has_column("Employee", "conveyance_amount") else "0",
			tel_expr="COALESCE(e.telephone_amount, 0)" if frappe.db.has_column("Employee", "telephone_amount") else "0",
			dummy_expr=dummy_expr,
		),
		where_values,
		as_dict=True,
	)


def _fetch_employees_by_ids(emp_ids):
	"""Fetch full employee data dicts for an explicit list of employee IDs,
	bypassing the doc-filter (used for dummy-employee parents that may not
	match the user's payroll filters)."""
	if not emp_ids:
		return []
	return _select_employees("e.name IN %(ids)s", {"ids": tuple(emp_ids)})


@frappe.whitelist()
def calculate_payroll(doc):
	"""Run the full salary calculation for the doc's filters.

	Returns the list of computed child rows. The JS dumps them into the
	`employees` table; the user can then `Save`.
	"""
	doc = frappe.parse_json(doc) if isinstance(doc, str) else doc
	from_date = getdate(doc.get("from_date"))
	to_date = getdate(doc.get("to_date"))

	if not from_date or not to_date:
		frappe.throw(_("From Date and To Date are required"))
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date"))

	days_in_period = (to_date - from_date).days + 1

	employees = get_employees(doc)
	if not employees:
		return {"rows": [], "log": ["No employees matched the filters."]}

	emp_ids = [e["employee"] for e in employees]

	# Dummy-employee parent mapping ----------------------------------------
	# If Employee X has dummy_employee = Y, then when payroll is run for Y,
	# Y's Col Q (payable_days) is taken from X's calculation (parent). All
	# other columns of Y are computed normally from Y's own basic/gross/etc.
	parent_of = _fetch_dummy_parents(emp_ids)

	# Include any out-of-batch parent employees so their payable_days can
	# be computed (their rows are NOT emitted unless already in the batch).
	extra_parent_ids = [p for p in set(parent_of.values()) if p not in set(emp_ids)]
	extra_emp_data = _fetch_employees_by_ids(extra_parent_ids) if extra_parent_ids else []
	all_emps = list(employees) + extra_emp_data
	all_ids = [e["employee"] for e in all_emps]
	employee_by_id = {e["employee"]: e for e in all_emps}

	# Pull every dependency once, in O(N) grouped queries
	att_map = _fetch_attendance_aggregates(all_ids, from_date, to_date)
	leave_map = _fetch_approved_leaves(all_ids, from_date, to_date)
	holidays_by_emp = _fetch_holidays_per_employee(all_emps, from_date, to_date)
	balance_map = _fetch_leave_balances(all_ids)
	cl_balance_map = _fetch_cl_balances(all_ids, from_date)
	tds_map = _fetch_tds(all_ids, from_date)
	advance_map = _fetch_advance_balances(all_ids, from_date, to_date)
	payable_balance_map = _fetch_payroll_payable_balance(all_ids, to_date)
	al_eligible_emps = _fetch_al_eligible_employees(all_ids)
	al_eligible_bls = _fetch_al_eligible_business_lines()

	# Memoize payable_days when an out-of-batch parent (or any parent) is
	# referenced via dummy_employee, so we never recompute it.
	payable_days_cache = {}

	def _payable_days_for(emp_id):
		if emp_id in payable_days_cache:
			return payable_days_cache[emp_id]
		emp_data = employee_by_id.get(emp_id)
		if not emp_data:
			return None
		parent_row = _calculate_employee(
			emp_data,
			from_date=from_date,
			to_date=to_date,
			days_in_period=days_in_period,
			att=att_map.get(emp_id, {}),
			leaves=leave_map.get(emp_id, {"full_leave_dates": set(), "half_leave_dates": set(), "short_leave_count": 0}),
			holiday_dates=holidays_by_emp.get(emp_id, set()),
			balance=balance_map.get(emp_id, {}),
			cl_balance=cl_balance_map.get(emp_id, 0.0),
			tds=tds_map.get(emp_id, 0.0),
			advance=advance_map.get(emp_id, {"full": 0.0, "part": 0.0}),
			payable_balance=payable_balance_map.get(emp_id, 0.0),
			al_eligible=(emp_id in al_eligible_emps and emp_data.get("business_line") in al_eligible_bls),
		)
		payable_days_cache[emp_id] = parent_row["payable_days"]
		return parent_row["payable_days"]

	rows = []
	log_lines = []

	for emp in employees:
		try:
			eid = emp["employee"]
			override = None
			if eid in parent_of:
				override = _payable_days_for(parent_of[eid])

			row = _calculate_employee(
				emp,
				from_date=from_date,
				to_date=to_date,
				days_in_period=days_in_period,
				att=att_map.get(eid, {}),
				leaves=leave_map.get(eid, {"full_leave_dates": set(), "half_leave_dates": set(), "short_leave_count": 0}),
				holiday_dates=holidays_by_emp.get(eid, set()),
				balance=balance_map.get(eid, {}),
				cl_balance=cl_balance_map.get(eid, 0.0),
				tds=tds_map.get(eid, 0.0),
				advance=advance_map.get(eid, {"full": 0.0, "part": 0.0}),
				payable_balance=payable_balance_map.get(eid, 0.0),
				al_eligible=(eid in al_eligible_emps and (emp.get("business_line") in al_eligible_bls)),
				payable_days_override=override,
				payable_days_source=parent_of.get(eid),
			)
			rows.append(row)
		except Exception:
			frappe.log_error(
				title="OTPL Payroll calc error: {0}".format(emp["employee"]),
				message=frappe.get_traceback(),
			)
			log_lines.append("{0}: ERROR (see Error Log)".format(emp["employee"]))

	return {"rows": rows, "log": log_lines}


def _fetch_dummy_parents(emp_ids):
	"""Return {child_emp: parent_emp} for any child in ``emp_ids`` that
	appears as another employee's ``dummy_employee``. Empty when the
	column doesn't exist."""
	if not emp_ids or not frappe.db.has_column("Employee", "dummy_employee"):
		return {}
	rows = frappe.db.sql(
		"""SELECT name AS parent, dummy_employee
		   FROM `tabEmployee`
		   WHERE dummy_employee IN %(ids)s
		     AND dummy_employee IS NOT NULL
		     AND dummy_employee != ''""",
		{"ids": tuple(emp_ids)},
		as_dict=True,
	)
	return {r.dummy_employee: r.parent for r in rows}


# -----------------------------------------------------------------------------
# Filter builder
# -----------------------------------------------------------------------------
def _build_employee_filter(doc):
	conditions = ["e.status = 'Active'"]
	values = {}

	if doc.get("company"):
		conditions.append("e.company = %(company)s")
		values["company"] = doc["company"]
	if doc.get("staff_type"):
		conditions.append("e.staff_type = %(staff_type)s")
		values["staff_type"] = doc["staff_type"]
	if doc.get("location"):
		conditions.append("e.location = %(location)s")
		values["location"] = doc["location"]
	if doc.get("business_line"):
		conditions.append("so.business_line = %(business_line)s")
		values["business_line"] = doc["business_line"]
	if doc.get("employee"):
		conditions.append("e.name = %(employee)s")
		values["employee"] = doc["employee"]

	return {"sql": " AND ".join(conditions), "values": values}


# -----------------------------------------------------------------------------
# Bulk fetchers (one SQL each)
# -----------------------------------------------------------------------------
def _fetch_attendance_aggregates(emp_ids, from_date, to_date):
	"""Per-employee aggregates for the period.

	Returns a dict keyed by employee with:
		processed_dates       set[date]   - any submitted att (excluding false)
		present_dates         set[date]   - submitted Present (excluding false)
		half_day_dates        set[date]   - submitted Half Day (excluding false)
		absent_dates          set[date]   - submitted Absent (excluding false)
		extra_late_half_days  int         - count of Half Day status w/ late_entry
		late_count            int         - sum of custom_late_mark
		working_hours         float       - sum of Attendance.working_hours
	"""
	if not emp_ids:
		return {}

	# `custom_late_mark` is a site-specific custom field and may not exist
	# on every site (e.g. winamore). Fall back to literal 0 when absent.
	has_late_mark = frappe.db.has_column("Attendance", "custom_late_mark")
	late_mark_expr = "COALESCE(a.custom_late_mark, 0)" if has_late_mark else "0"
	has_working_hours = frappe.db.has_column("Attendance", "working_hours")
	working_hours_expr = "COALESCE(a.working_hours, 0)" if has_working_hours else "0"

	rows = frappe.db.sql(
		"""
		SELECT
			a.employee,
			a.attendance_date,
			a.status,
			{late_mark_expr}                  AS late_mark,
			COALESCE(a.late_entry, 0)         AS late_entry,
			COALESCE(a.early_exit, 0)         AS early_exit,
			{working_hours_expr}              AS working_hours,
			COALESCE(a.false_attendance, 0)   AS false_attendance
		FROM `tabAttendance` a
		WHERE a.employee IN %(emp_ids)s
		  AND a.attendance_date BETWEEN %(from_date)s AND %(to_date)s
		  AND a.docstatus = 1
		""".format(late_mark_expr=late_mark_expr, working_hours_expr=working_hours_expr),
		{"emp_ids": tuple(emp_ids), "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)

	out = defaultdict(lambda: {
		"processed_dates": set(),
		"present_dates": set(),
		"half_day_dates": set(),
		"absent_dates": set(),
		"late_count": 0,
		"extra_late_half_days": 0,
		"working_hours": 0.0,
		"false_attendance_count": 0,
	})

	for r in rows:
		if cint(r.false_attendance):
			out[r.employee]["false_attendance_count"] += 1
			continue
		bucket = out[r.employee]
		d = getdate(r.attendance_date)
		bucket["processed_dates"].add(d)
		if r.status == "Present":
			bucket["present_dates"].add(d)
		elif r.status == "Half Day":
			bucket["half_day_dates"].add(d)
			if cint(r.late_entry) or cint(r.early_exit):
				bucket["extra_late_half_days"] += 1
		elif r.status == "Absent":
			bucket["absent_dates"].add(d)
		# Late count = any attendance record in the period flagged as late
		# entry or early exit (one count per day, regardless of which flag).
		if cint(r.late_entry) or cint(r.early_exit):
			bucket["late_count"] += 1
		bucket["working_hours"] += flt(r.working_hours)

	return out


def _fetch_approved_leaves(emp_ids, from_date, to_date):
	"""Per-employee approved leave breakdown for the period.

	Returns dict employee -> {
		"full_leave_dates":  set[date]   # full-day approved leaves only
		"half_leave_dates":  set[date]   # half-day approved leaves
		"short_leave_count": int         # # of approved short leaves
	}

	Per observation #7 "approved leaves" used in CL/AL adjustment must not
	include half days or short leaves; they are surfaced separately.
	"""
	empty = lambda: {"full_leave_dates": set(), "half_leave_dates": set(), "short_leave_count": 0}
	out = defaultdict(empty)
	if not emp_ids:
		return out

	rows = frappe.db.sql(
		"""
		SELECT employee, approved_from_date, approved_to_date,
		       COALESCE(half_day, 0) AS half_day, half_day_date,
		       COALESCE(short_leave, 0) AS short_leave
		FROM `tabOTPL Leave`
		WHERE employee IN %(emp_ids)s
		  AND status = 'Approved'
		  AND approved_from_date IS NOT NULL
		  AND approved_to_date IS NOT NULL
		  AND approved_from_date <= %(to_date)s
		  AND approved_to_date   >= %(from_date)s
		""",
		{"emp_ids": tuple(emp_ids), "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)

	for r in rows:
		bucket = out[r.employee]

		# Per observation #7: half-day and short-leave records must NEVER
		# contribute to the full-day approved-leave count. They are surfaced
		# separately (half_leave_dates / short_leave_count) and consumed by
		# the Late Deduction column instead of the CL/AL adjustment.
		if cint(r.short_leave):
			bucket["short_leave_count"] += 1
			continue

		if cint(r.half_day):
			hd = getdate(r.half_day_date) if r.half_day_date else None
			if hd and from_date <= hd <= to_date:
				bucket["half_leave_dates"].add(hd)
			# A half-day leave application can still cover a multi-day range;
			# treat all OTHER dates of the range as full-day leaves. If
			# half_day_date is missing, the whole range collapses to a single
			# half-day (still excluded from full leaves) — never inflated.
			if hd:
				start = max(getdate(r.approved_from_date), from_date)
				end = min(getdate(r.approved_to_date), to_date)
				d = start
				while d <= end:
					if d != hd:
						bucket["full_leave_dates"].add(d)
					d += timedelta(days=1)
			continue

		start = max(getdate(r.approved_from_date), from_date)
		end = min(getdate(r.approved_to_date), to_date)
		d = start
		while d <= end:
			bucket["full_leave_dates"].add(d)
			d += timedelta(days=1)
	return out


def _fetch_holidays_per_employee(employees, from_date, to_date):
	"""Returns dict employee -> set of holiday dates.

	One query per distinct holiday list (small set in practice).
	"""
	if not employees:
		return {}

	emp_ids = [e["employee"] for e in employees]
	hl_rows = frappe.db.sql(
		"""
		SELECT name, holiday_list
		FROM `tabEmployee`
		WHERE name IN %(emp_ids)s
		""",
		{"emp_ids": tuple(emp_ids)},
		as_dict=True,
	)
	emp_to_hl = {r.name: r.holiday_list for r in hl_rows}

	# Fallback to default holiday list if employee has none
	default_hl = frappe.db.get_value(
		"Company",
		frappe.defaults.get_user_default("Company") or
		frappe.db.get_value("Employee", emp_ids[0], "company"),
		"default_holiday_list",
	)

	holiday_lists = set(filter(None, emp_to_hl.values())) | ({default_hl} if default_hl else set())
	if not holiday_lists:
		return {emp: set() for emp in emp_ids}

	hl_dates = defaultdict(set)
	hrows = frappe.db.sql(
		"""
		SELECT parent, holiday_date
		FROM `tabHoliday`
		WHERE parent IN %(hls)s
		  AND holiday_date BETWEEN %(from_date)s AND %(to_date)s
		""",
		{"hls": tuple(holiday_lists), "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	for r in hrows:
		hl_dates[r.parent].add(getdate(r.holiday_date))

	out = {}
	for emp in emp_ids:
		hl = emp_to_hl.get(emp) or default_hl
		out[emp] = set(hl_dates.get(hl, set()))
	return out


def _fetch_leave_balances(emp_ids):
	"""AL balance per employee (from OTPL Employee Leave Balance).

	This doctype tracks only Annual Leave (AL) for AL-eligible employees
	(Worker @ Site with an opening row). Casual Leave (CL) is sourced
	separately from Frappe's standard leave allocation - see
	``_fetch_cl_balances``.
	"""
	if not emp_ids:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT employee, al_balance, year_opening_al
		FROM `tabOTPL Employee Leave Balance`
		WHERE employee IN %(emp_ids)s
		""",
		{"emp_ids": tuple(emp_ids)},
		as_dict=True,
	)
	return {r.employee: dict(r) for r in rows}


def _fetch_cl_balances(emp_ids, as_on_date):
	"""Casual Leave balance per employee, as of ``as_on_date``.

	Uses Frappe's standard ``get_leave_balance_on`` against leave type
	``Casual Leave`` - the same source OTPL Leave uses when splitting
	an application into CL + LWP.  Available for ALL employees regardless
	of AL eligibility.
	"""
	out = {e: 0.0 for e in emp_ids}
	if not emp_ids:
		return out
	try:
		from erpnext.hr.doctype.leave_application.leave_application import (
			get_leave_balance_on,
		)
	except Exception:
		return out
	for emp in emp_ids:
		try:
			# Match the standard "Leave Balance" report: balance as of the
			# given date, i.e. allocation − leaves taken strictly before
			# ``as_on_date``. Do NOT pass
			# ``consider_all_leaves_in_the_allocation_period=True`` — that
			# would also subtract leaves applied AFTER the payroll period
			# within the same allocation, which is not what payroll wants.
			bal = get_leave_balance_on(
				employee=emp,
				leave_type="Casual Leave",
				date=as_on_date,
			) or 0
			frappe.msgprint("CL balance for {emp} as of {as_on_date}: {bal}".format(emp=emp, as_on_date=as_on_date, bal=bal))
			out[emp] = flt(bal)
		except Exception:
			out[emp] = 0.0
	return out


def _fetch_tds(emp_ids, from_date):
	"""Pick TDS amount from OTPL Employee Investment for the fiscal year of
	from_date. Falls back to 0 if missing.
	"""
	if not emp_ids:
		return {}

	fy = frappe.db.sql(
		"""
		SELECT name FROM `tabFiscal Year`
		WHERE %(d)s BETWEEN year_start_date AND year_end_date
		LIMIT 1
		""",
		{"d": from_date},
	)
	if not fy:
		return {}
	fy = fy[0][0]

	rows = frappe.db.sql(
		"""
		SELECT employee, tds_amount
		FROM `tabOTPL Employee Investment`
		WHERE employee IN %(emp_ids)s AND fiscal_year = %(fy)s
		""",
		{"emp_ids": tuple(emp_ids), "fy": fy},
		as_dict=True,
	)
	return {r.employee: flt(r.tds_amount) for r in rows}


def _fetch_advance_balances(emp_ids, from_date, to_date):
	"""Per-employee Full / Part advance ledger figures.

	**Col AA (Full Advance)** = GL balance on the configured full-advance
	account (``OTPL Accounting Settings.full_advance_salary_adjustment``)
	as on ``to_date``.

	**Col AB (Part Advance)** = sum, per employee, of submitted Journal
	Entry rows posted on the LAST DAY of the period's month where:
	  * Journal Entry ``purpose`` = "Part Advance Salary Adjustment"
	  * Journal Entry Account row has ``party_type`` = "Employee" and
	    ``party`` = the employee id
	The row-level amount taken is ``debit_in_account_currency +
	credit_in_account_currency`` (typically only one side is non-zero on
	the employee row).
	"""
	out = {e: {"full": 0.0, "part": 0.0} for e in emp_ids}
	if not emp_ids:
		return out

	# Part-advance JVs are posted on the last calendar day of the selected
	# payroll month (driven by from_date).
	month_end = get_last_day(from_date)

	settings = frappe.get_cached_doc("OTPL Accounting Settings", "OTPL Accounting Settings")
	full_acc = settings.get("full_advance_salary_adjustment")
	part_acc = settings.get("part_advance_salary_adjustment")

	# --- AA: party balance on full-advance account as of to_date
	if full_acc:
		from erpnext.accounts.utils import get_balance_on
		for emp in emp_ids:
			bal = get_balance_on(
				account=full_acc,
				date=to_date,
				party_type="Employee",
				party=emp,
			)
			out[emp]["full"] = max(flt(bal), 0.0)

	# --- AB: Part Advance Salary Adjustment JVs posted on the month-end
	if part_acc:
		rows = frappe.db.sql(
			"""
			SELECT jea.party AS employee,
			       SUM(ABS(COALESCE(jea.debit_in_account_currency, 0)
			             - COALESCE(jea.credit_in_account_currency, 0))) AS amt
			FROM `tabJournal Entry Account` jea
			JOIN `tabJournal Entry` je ON je.name = jea.parent
			WHERE je.docstatus = 1
			  AND je.posting_date = %(d)s
			  AND je.purpose = 'Part Advance Salary Adjustment'
			  AND jea.account = %(acc)s
			  AND jea.party_type = 'Employee'
			  AND jea.party IN %(emps)s
			GROUP BY jea.party
			""",
			{"emps": tuple(emp_ids), "acc": part_acc, "d": month_end},
			as_dict=True,
		)
		for r in rows:
			out[r.employee]["part"] = max(flt(r.amt), 0.0)

	return out


def _fetch_payroll_payable_balance(emp_ids, to_date):
	"""Party balance on the configured Payroll Payable account per employee as
	of ``to_date``. Used for Col AD (expenses).

	Payroll Payable is a credit (liability) account, so we negate the
	``get_balance_on`` debit-minus-credit result to express it as a positive
	outstanding balance.
	"""
	out = {e: 0.0 for e in emp_ids}
	if not emp_ids:
		return out

	acc = frappe.db.get_value(
		"OTPL Accounting Settings", "OTPL Accounting Settings", "payroll_payable"
	)
	if not acc:
		return out

	from erpnext.accounts.utils import get_balance_on
	for emp in emp_ids:
		bal = get_balance_on(
			account=acc,
			date=to_date,
			party_type="Employee",
			party=emp,
		)
		out[emp] = flt(bal)
	return out


def _fetch_al_eligible_employees(emp_ids):
	"""Return the subset of ``emp_ids`` that have an opening row in
	``OTPL Employee Leave Balance``. Per observation #10, AL Generated/
	Adjustment/Closing AL are only computed for employees seeded there.
	"""
	if not emp_ids:
		return set()
	rows = frappe.db.sql(
		"""
		SELECT employee FROM `tabOTPL Employee Leave Balance`
		WHERE employee IN %(emps)s
		""",
		{"emps": tuple(emp_ids)},
	)
	return {r[0] for r in rows}


def _fetch_al_eligible_business_lines():
	"""Return the set of Business Line names that have the
	``al_eligible`` custom field checked. Per observation #12.
	"""
	if not frappe.db.has_column("Business Line", "al_eligible"):
		return set()
	rows = frappe.db.sql(
		"""SELECT name FROM `tabBusiness Line` WHERE COALESCE(al_eligible, 0) = 1"""
	)
	return {r[0] for r in rows}


# -----------------------------------------------------------------------------
# Per-employee calculation
# -----------------------------------------------------------------------------
def _calculate_employee(emp, from_date, to_date, days_in_period,
                        att, leaves, holiday_dates,
                        balance, tds, advance,
                        cl_balance=0.0,
                        payable_balance=0.0, al_eligible=False,
                        payable_days_override=None,
                        payable_days_source=None):
	gross = flt(emp.get("gross_salary"))
	basic = flt(emp.get("basic_salary"))
	staff_type = emp.get("staff_type")
	location = emp.get("location")

	is_worker_site = (staff_type == "Worker" and location == "Site")
	is_worker_field_site = (staff_type in ("Worker", "Field") and location == "Site")
	is_worker_haridwar = (staff_type == "Worker" and location == "Haridwar")
	is_worker_noida_or_hwr = (staff_type == "Worker" and location in ("Noida", "Haridwar"))
	is_driver = (staff_type == "Driver")
	# OT applies to Worker@Noida/Haridwar and to all Drivers (any location).
	ot_eligible = is_worker_noida_or_hwr or is_driver
	# Late tracking is N/A for Worker/Field at Site (per business rule).
	skip_late_metrics = is_worker_field_site

	# AL is gated by BOTH: employee has a row in OTPL Employee Leave Balance
	# AND the employee's Business Line has al_eligible=1 (observation #12).
	al_enabled = bool(is_worker_site and al_eligible)

	# Attendance aggregates -----------------------------------------------------
	present_dates = att.get("present_dates", set())
	half_day_dates = att.get("half_day_dates", set())
	absent_dates = att.get("absent_dates", set())
	processed_dates = att.get("processed_dates", set())
	late_count = 0 if skip_late_metrics else att.get("late_count", 0)
	extra_late_half_days = 0 if skip_late_metrics else att.get("extra_late_half_days", 0)
	working_hours = flt(att.get("working_hours", 0.0))
	false_attendance_count = att.get("false_attendance_count", 0)

	# Approved leaves (per observation #7 separated) ----------------------------
	full_leave_dates = leaves.get("full_leave_dates", set())
	half_leave_dates = leaves.get("half_leave_dates", set())
	short_leave_count = leaves.get("short_leave_count", 0)
	approved_leaves_count = len(full_leave_dates)  # full-day only

	# "Present-ish" set for holiday qualification per observation #5
	# (half day counts as present, both from attendance and approved half leave)
	presentish_dates = (
		present_dates
		| half_day_dates
		| half_leave_dates
	)

	# ---- Qualified holidays --------------------------------------------------
	# Two rules:
	#   * OR rule  (Days Worked, Col H): holiday qualifies if the employee is
	#     "present-ish" in ANY of the 3 days preceding OR following.
	#   * AND rule (AL Generated, Col G): holiday qualifies only if the
	#     employee is "present-ish" in ANY of the 3 days preceding AND ANY
	#     of the 3 days following. A holiday sandwiched inside a leave
	#     block (e.g. employee on full leave both sides) therefore does
	#     NOT generate AL.
	#
	# Period-boundary relaxation: attendance/leave are only known within
	# [from_date, to_date]. For a holiday on the first/last day of the
	# period, one side of the window has no observable data, so that side
	# cannot be checked — the AND rule then falls back to the side that IS
	# observable. Example: a Sunday on the last day of the month (e.g.
	# 31-May) qualifies on the "before" side alone, since there are no
	# in-period days after it to evaluate.
	qualified_holidays = 0          # OR rule, used for Col H
	qualified_holidays_strict = 0   # AND rule, used for Col G
	for h in holiday_dates:
		before = any((h - timedelta(days=k)) in presentish_dates for k in (1, 2, 3))
		after = any((h + timedelta(days=k)) in presentish_dates for k in (1, 2, 3))
		if before or after:
			qualified_holidays += 1
		# If the day immediately before/after the holiday lies outside the
		# payroll period, that side is unobservable -> treat it as satisfied
		# so a boundary holiday is not unfairly disqualified.
		has_before_in_period = (h - timedelta(days=1)) >= from_date
		has_after_in_period = (h + timedelta(days=1)) <= to_date
		strict_before = before or not has_before_in_period
		strict_after = after or not has_after_in_period
		if strict_before and strict_after:
			qualified_holidays_strict += 1

	# ---- Col G: AL Generated --------------------------------------------------
	# AL is generated for each holiday that has a present-ish day on BOTH
	# sides. Only counted when AL is enabled for this employee/business line.
	al_generated = qualified_holidays_strict if al_enabled else 0

	# ---- Col H: Days Worked (Worked / Holidays / Leave Adjustment) -----------
	# Half days count as a full present day here (obs #5); the 0.5-day salary
	# impact is taken out separately via the Late Deduction column (Col K), so
	# counting half days as 1 here prevents a 1.5-day net loss for the employee.
	non_holiday_present = sum(
		1 for d in (present_dates | half_day_dates | half_leave_dates)
		if d not in holiday_dates
	)

	days_worked = non_holiday_present + qualified_holidays
	if is_worker_site:
		dw_explain = "Worker@Site: non-holiday present + qualifying holidays (OR rule)"
	else:
		dw_explain = "Non-(Worker@Site): non-holiday present + qualifying holidays (OR rule)"

	# Each false attendance still deducts 2 days from days worked.
	days_worked -= 2 * false_attendance_count
	days_worked = max(days_worked, 0)

	# ---- Col K: Late deduction days (observation #9) -------------------------
	# K = "Deduction in days due to Late and Extra Late" =
	#       (approved-half-days + extra-late-half-days) / 2   (half-day leave part)
	#     + late-mark deduction derived from the No. of Late Marked (Col I).
	#
	# The late-mark deduction is a pure function of the late count, using the
	# three ESS Location "Leave Deduction Rules" fields (defaults 3 / 5 / 5):
	#   * late_count >= late_count_for_full_day      -> 1 day (full day)
	#   * late_count >= late_count_for_half_day      -> 0.5 day (half day)
	#   * else                                       -> 0
	#   * additionally, for every late beyond treat_late_as_half_day_after,
	#     add 0.5 (that late is treated as an extra half day).
	# So with 3 / 5 / 5:  3->0.5, 4->0.5, 5->1.0, 6->1.5, 7->2.0, ...
	approved_half_days = len(half_leave_dates)

	late_count_for_half_day = cint(emp.get("late_count_for_half_day")) or 3
	late_count_for_full_day = cint(emp.get("late_count_for_full_day")) or 5
	treat_late_as_half_day_after = cint(emp.get("treat_late_as_half_day_after")) or 5
	late_mark_deduction = 0.0
	if late_count >= late_count_for_full_day:
		late_mark_deduction = 1.0
		if late_count > treat_late_as_half_day_after:
			late_mark_deduction += (late_count - treat_late_as_half_day_after) * 0.5
	elif late_count >= late_count_for_half_day:
		late_mark_deduction = 0.5

	late_deduction = (approved_half_days + extra_late_half_days) / 2.0 + late_mark_deduction

	# ---- Col L: Absent (observation #4) --------------------------------------
	# Just count Attendance.status='Absent' (excluding false attendance).
	absent_count = len(absent_dates)

	# ---- Col M / N: Adjusted from CL / AL ------------------------------------
	# CL comes from the standard "Casual Leave" allocation (passed in by the
	# caller). AL comes from OTPL Employee Leave Balance.
	#
	# AL Bal is only available when AL is enabled (Worker@Site + opening row +
	# AL-eligible business line); otherwise it's treated as 0 for the M
	# formula.
	#
	# N = If(AL Bal >= (approved - L), (approved - L), AL Bal)
	# M = If((approved - L) > AL Bal,
	#        If((approved - L - AL Bal) >= 2,
	#           If(CL Bal >= 2, 2, CL Bal),
	#           If(CL Bal > 0, approved - L - AL Bal, 0)),
	#        0)
	cl_balance = flt(cl_balance)
	al_balance = flt(balance.get("al_balance") or balance.get("year_opening_al") or 0)

	effective_al = al_balance if al_enabled else 0
	adjusted_leaves = max(0, approved_leaves_count - absent_count)

	# Col N: Adjusted from AL
	if al_enabled:
		adj_al = adjusted_leaves if effective_al >= adjusted_leaves else effective_al
		closing_al = al_balance + al_generated - adj_al
	else:
		adj_al = 0
		closing_al = 0

	# Col M: Adjusted from CL
	if adjusted_leaves > effective_al:
		uncovered = adjusted_leaves - effective_al
		if uncovered >= 2:
			adj_cl = 2 if cl_balance >= 2 else max(cl_balance, 0)
		else:
			adj_cl = uncovered if cl_balance > 0 else 0
	else:
		adj_cl = 0

	# ---- Col O / P: Balances --------------------------------------------------
	balance_cl = cl_balance - adj_cl

	# ---- Col Q: Payable Days -------------------------------------------------
	# Per observation #23 do NOT clamp negative values.
	payable_days = days_worked - late_deduction - absent_count + adj_cl + adj_al

	# Dummy-employee override: when this employee is set as another
	# Employee's ``dummy_employee``, Col Q is taken from the parent.
	# Everything downstream of Q (R, S, T, U, V, W) is then computed using
	# the overridden value with the dummy's own basic/gross.
	if payable_days_override is not None:
		payable_days = flt(payable_days_override)

	# ---- Col R: Salary Amount -------------------------------------------------
	days_in_month = monthrange(from_date.year, from_date.month)[1]
	per_day = (gross / days_in_month) if days_in_month else 0
	salary_amount = per_day * payable_days

	# ---- Col S: OT/HRA/Petrol (observations #17, #18) ------------------------
	# OT hours = [working_hours + (holiday-list dates in period * 8)] - (days_worked * 8)
	# OT amount = OT hours * (gross / (days_in_month * 8))
	ot_hra_petrol = 0.0
	ot_hours = 0.0
	if ot_eligible and gross and days_in_month:
		ot_hours = (
			working_hours
			+ (len(holiday_dates) * STD_HOURS_PER_DAY)
			- (days_worked * SALARY_HOURS_PER_DAY)
		)
		hourly_rate = gross / (days_in_month * SALARY_HOURS_PER_DAY)
		ot_hra_petrol = ot_hours * hourly_rate

	# ---- Col T: Incentive (observation #11) ----------------------------------
	# If present_days + qualified_holidays = days_in_month => Rs 200; Worker@HWR only.
	incentive = 0.0
	present_count = len(present_dates) + 0.5 * len(half_day_dates)
	if is_worker_haridwar and (present_count + qualified_holidays) >= days_in_month:
		incentive = WORKER_HARIDWAR_INCENTIVE

	# ---- Col U: Total Salary Due ---------------------------------------------
	total_salary_due = salary_amount + ot_hra_petrol + incentive

	# ---- Col V: PF Employee --------------------------------------------------
	# PF basic wage band:
	#   * basic < min_wages              -> use min_wages
	#   * min_wages <= basic <= max_wage_pf -> use basic
	#   * basic > max_wage_pf            -> use max_wage_pf
	# When Employee.no_validation = 1, override with no_validation_base_salary
	# (band check bypassed entirely).
	# Computed only if UAN is populated.
	no_validation = cint(emp.get("no_validation"))
	min_wages = flt(emp.get("min_wages"))
	max_wage_pf = flt(emp.get("max_wage_pf"))
	max_wage_esic = flt(emp.get("max_wage_esic"))

	if no_validation:
		pf_basic = flt(emp.get("no_validation_base_salary"))
	else:
		pf_basic = basic
		if min_wages and pf_basic < min_wages:
			pf_basic = min_wages
		if max_wage_pf and pf_basic > max_wage_pf:
			pf_basic = max_wage_pf

	pf_employee = 0.0
	if emp.get("uan_no") and pf_basic and days_in_month:
		pf_employee = (pf_basic / days_in_month) * payable_days * PF_EMPLOYEE_RATE

	# ---- Col W: ESIC Employee ------------------------------------------------
	# ESIC basic wage band (computed only if ESIC No is populated):
	#   * basic < min_wages                   -> use min_wages
	#   * min_wages <= basic <= max_wage_esic -> use basic
	#   * basic > max_wage_esic               -> use max_wage_esic (capped)
	esic_employee = 0.0
	if emp.get("esic_no") and days_in_month:
		esic_base = basic
		if min_wages and esic_base < min_wages:
			esic_base = min_wages
		if max_wage_esic and esic_base > max_wage_esic:
			esic_base = max_wage_esic
		if esic_base:
			esic_employee = (esic_base / days_in_month) * payable_days * ESIC_EMPLOYEE_RATE

	# ---- Col Y / Z: Employer shares -------------------------------------------
	pf_employer = pf_employee * PF_EMPLOYER_FACTOR
	esic_employer = esic_employee * ESIC_EMPLOYER_FACTOR

	# ---- TDS -----------------------------------------------------------------
	tds_amount = flt(tds)

	# If salary amount (R) is negative, zero out V/W/X/Y/Z (no statutory
	# deductions / TDS on a negative wage).
	if salary_amount < 0:
		pf_employee = 0.0
		esic_employee = 0.0
		tds_amount = 0.0
		pf_employer = 0.0
		esic_employer = 0.0

	# ---- Col AA / AB ---------------------------------------------------------
	full_adv = flt(advance.get("full", 0.0))
	part_adv = flt(advance.get("part", 0.0))

	# ---- Col AC: Net Amount Payable ------------------------------------------
	net_payable = (
		total_salary_due - pf_employee - esic_employee - tds_amount - full_adv - part_adv
	)

	# ---- Col AD: Expenses balance (observation #16) --------------------------
	# Payroll Payable ledger balance as of period end, netted against AB
	# (part-advance transfers within the period are already captured in AB).
	#   * balance >= 0  ->  AD = balance - AB
	#   * balance <  0  ->  AD = balance + AB
	pp_balance = flt(payable_balance)
	if pp_balance >= 0:
		expenses_balance = pp_balance - part_adv
	else:
		expenses_balance = pp_balance + part_adv

	# ---- Col AE: Extra Allowance (observations #19, #20) ---------------------
	# TADA  -> only Worker/Site or Field/Site, per present day * daily_tada
	# HRA/Conveyance/Telephone -> everyone EXCEPT Worker/Site & Field/Site
	tada_amount = 0.0
	if is_worker_field_site:
		tada_days = flt(payable_days) - flt(adj_cl) - flt(adj_al)
		if tada_days < 0:
			tada_days = 0.0
		tada_amount = flt(emp.get("daily_tada")) * tada_days
	hra = conv = tel = 0.0
	if not is_worker_field_site:
		hra = flt(emp.get("hra_amount"))
		conv = flt(emp.get("conveyance_amount"))
		tel = flt(emp.get("telephone_amount"))
	extra_allowance = tada_amount + hra + conv + tel

	# ---- Col AF: Net amount to pay (observation #21) -------------------------
	# AF = AC - AD + AE
	net_to_pay = net_payable - expenses_balance + extra_allowance

	row = {
		"employee": emp["employee"],
		"employee_name": emp.get("employee_name"),
		"sales_order": emp.get("sales_order"),
		"business_line": emp.get("business_line"),
		"uan_no": emp.get("uan_no"),
		"esic_no": emp.get("esic_no"),
		"gross_salary": gross,
		"basic_salary": basic,
		"al_generated": al_generated,
		"days_worked": flt(days_worked, 2),
		"late_count": late_count,
		"approved_half_days": approved_half_days,
		"extra_late_half_days": extra_late_half_days,
		"short_leaves_count": short_leave_count,
		"late_deduction_days": flt(late_deduction, 2),
		"absent_no_info_days": absent_count,
		"adjusted_from_cl": flt(adj_cl, 2),
		"adjusted_from_al": flt(adj_al, 2),
		"balance_cl": flt(balance_cl, 2),
		"closing_al": flt(closing_al, 2),
		"payable_days": flt(payable_days, 2),
		"salary_amount": flt(salary_amount, 2),
		"working_hours": flt(working_hours, 2),
		"ot_hra_petrol": flt(ot_hra_petrol, 2),
		"incentive": flt(incentive, 2),
		"tada_amount": flt(tada_amount, 2),
		"hra_amount": flt(hra, 2),
		"conveyance_amount": flt(conv, 2),
		"telephone_amount": flt(tel, 2),
		"extra_allowance": flt(extra_allowance, 2),
		"total_salary_due": flt(total_salary_due, 2),
		"pf_employee_share": flt(pf_employee, 2),
		"esic_employee_share": flt(esic_employee, 2),
		"tds": flt(tds_amount, 2),
		"pf_employer_share": flt(pf_employer, 2),
		"esic_employer_share": flt(esic_employer, 2),
		"full_advance_adjustment": flt(full_adv, 2),
		"part_advance_adjustment": flt(part_adv, 2),
		"net_amount_payable": flt(net_payable, 2),
		"expenses_balance": flt(expenses_balance, 2),
		"net_amount_to_pay": flt(net_to_pay, 2),
		"staff_type": staff_type,
		"location": location,
		"department": emp.get("department"),
	}
	return row


# -----------------------------------------------------------------------------
# Helpers (called from validate)
# -----------------------------------------------------------------------------
def _recompute_row_nets(row):
	# If Salary Amount (R) is negative, zero out V/W/X/Y/Z so they match the
	# Calculate Salary output even after manual edits.
	if flt(row.salary_amount) < 0:
		row.pf_employee_share = 0
		row.esic_employee_share = 0
		row.tds = 0
		row.pf_employer_share = 0
		row.esic_employer_share = 0

	row.net_amount_payable = flt(
		flt(row.total_salary_due)
		- flt(row.pf_employee_share)
		- flt(row.esic_employee_share)
		- flt(row.tds)
		- flt(row.full_advance_adjustment)
		- flt(row.part_advance_adjustment),
		2,
	)
	# Col AF = AC - AD + AE (observation #21)
	row.net_amount_to_pay = flt(
		flt(row.net_amount_payable)
		- flt(row.expenses_balance)
		+ flt(getattr(row, "extra_allowance", 0) or 0),
		2,
	)


def _set_totals(doc):
	t = defaultdict(float)
	for r in doc.employees:
		t["gross"] += flt(r.gross_salary)
		t["payable_days"] += flt(r.payable_days)
		t["salary_amount"] += flt(r.salary_amount)
		t["ot"] += flt(r.ot_hra_petrol)
		t["incentive"] += flt(r.incentive)
		t["due"] += flt(r.total_salary_due)
		t["pfe"] += flt(r.pf_employee_share)
		t["esice"] += flt(r.esic_employee_share)
		t["tds"] += flt(r.tds)
		t["pfemp"] += flt(r.pf_employer_share)
		t["esicemp"] += flt(r.esic_employer_share)
		t["adv"] += flt(r.full_advance_adjustment) + flt(r.part_advance_adjustment)
		t["extra"] += flt(getattr(r, "extra_allowance", 0) or 0)
		t["net_pay"] += flt(r.net_amount_payable)
		t["net_to_pay"] += flt(r.net_amount_to_pay)

	doc.total_gross_salary = t["gross"]
	doc.total_payable_days = t["payable_days"]
	doc.total_salary_amount = t["salary_amount"]
	doc.total_ot_hra_petrol = t["ot"]
	doc.total_incentive = t["incentive"]
	doc.total_salary_due = t["due"]
	doc.total_pf_employee = t["pfe"]
	doc.total_esic_employee = t["esice"]
	doc.total_tds = t["tds"]
	doc.total_pf_employer = t["pfemp"]
	doc.total_esic_employer = t["esicemp"]
	doc.total_advance_adjustment = t["adv"]
	if hasattr(doc, "total_extra_allowance"):
		doc.total_extra_allowance = t["extra"]
	doc.total_net_payable = t["net_pay"]
	doc.total_net_to_pay = t["net_to_pay"]


def _persist_leave_balances(doc):
	"""Roll the row's closing AL into OTPL Employee Leave Balance.

	Only AL-eligible employees (i.e. those that already have an entry in
	OTPL Employee Leave Balance) get updated; CL is tracked by Frappe's
	standard Leave Allocation system and is not written here.
	"""
	for r in doc.employees:
		bal_name = frappe.db.get_value(
			"OTPL Employee Leave Balance", {"employee": r.employee}, "name"
		)
		if not bal_name:
			continue
		frappe.db.set_value(
			"OTPL Employee Leave Balance",
			bal_name,
			{
				"al_balance": flt(r.closing_al),
				"as_on_date": doc.to_date,
			},
			update_modified=True,
		)
	frappe.db.commit()


# -----------------------------------------------------------------------------
# Calculation trace (used by the "View Calculation" dialog in the UI)
# -----------------------------------------------------------------------------
@frappe.whitelist()
def get_calculation_trace(doc, employee):
	"""Return a human-readable, step-by-step breakdown of how each column
	was computed for a single employee.

	This is intentionally a thin wrapper around the same code path used by
	``calculate_payroll`` so the dialog always reflects the live formulas.
	"""
	doc = frappe.parse_json(doc) if isinstance(doc, str) else doc
	from_date = getdate(doc.get("from_date"))
	to_date = getdate(doc.get("to_date"))
	if not from_date or not to_date:
		frappe.throw(_("From Date and To Date are required"))
	days_in_period = (to_date - from_date).days + 1
	days_in_month = monthrange(from_date.year, from_date.month)[1]

	emp_rows = _fetch_employees_by_ids([employee])
	if not emp_rows:
		frappe.throw(_("Employee {0} not found").format(employee))
	emp = emp_rows[0]

	# Dummy-employee parent: if another Employee has dummy_employee=this,
	# we need that parent's data too so we can override Q.
	parent_map = _fetch_dummy_parents([employee])
	parent_id = parent_map.get(employee)
	parent_emp = None
	if parent_id:
		parent_rows = _fetch_employees_by_ids([parent_id])
		parent_emp = parent_rows[0] if parent_rows else None

	ids_for_fetch = [employee] + ([parent_id] if parent_id else [])
	emps_for_fetch = [emp] + ([parent_emp] if parent_emp else [])

	att_map = _fetch_attendance_aggregates(ids_for_fetch, from_date, to_date)
	leave_map = _fetch_approved_leaves(ids_for_fetch, from_date, to_date)
	holidays_by_emp = _fetch_holidays_per_employee(emps_for_fetch, from_date, to_date)
	balance_map = _fetch_leave_balances(ids_for_fetch)
	cl_balance_map = _fetch_cl_balances(ids_for_fetch, from_date)
	tds_map = _fetch_tds(ids_for_fetch, from_date)
	advance_map = _fetch_advance_balances(ids_for_fetch, from_date, to_date)
	payable_balance_map = _fetch_payroll_payable_balance(ids_for_fetch, to_date)
	al_eligible_emps = _fetch_al_eligible_employees(ids_for_fetch)
	al_eligible_bls = _fetch_al_eligible_business_lines()

	att = att_map.get(employee, {})
	leaves = leave_map.get(employee, {"full_leave_dates": set(), "half_leave_dates": set(), "short_leave_count": 0})
	holiday_dates = holidays_by_emp.get(employee, set())
	balance = balance_map.get(employee, {})
	cl_bal = cl_balance_map.get(employee, 0.0)
	tds = tds_map.get(employee, 0.0)
	advance = advance_map.get(employee, {"full": 0.0, "part": 0.0})
	payable_balance = payable_balance_map.get(employee, 0.0)
	al_eligible = (employee in al_eligible_emps) and (emp.get("business_line") in al_eligible_bls)

	# If this employee is a dummy of another, compute parent's payable_days
	# and override Q for the dummy.
	payable_days_override = None
	if parent_emp:
		parent_row = _calculate_employee(
			parent_emp, from_date=from_date, to_date=to_date,
			days_in_period=days_in_period,
			att=att_map.get(parent_id, {}),
			leaves=leave_map.get(parent_id, {"full_leave_dates": set(), "half_leave_dates": set(), "short_leave_count": 0}),
			holiday_dates=holidays_by_emp.get(parent_id, set()),
			balance=balance_map.get(parent_id, {}),
			cl_balance=cl_balance_map.get(parent_id, 0.0),
			tds=tds_map.get(parent_id, 0.0),
			advance=advance_map.get(parent_id, {"full": 0.0, "part": 0.0}),
			payable_balance=payable_balance_map.get(parent_id, 0.0),
			al_eligible=(parent_id in al_eligible_emps and parent_emp.get("business_line") in al_eligible_bls),
		)
		payable_days_override = parent_row["payable_days"]

	row = _calculate_employee(
		emp, from_date=from_date, to_date=to_date,
		days_in_period=days_in_period, att=att, leaves=leaves,
		holiday_dates=holiday_dates, balance=balance, tds=tds,
		advance=advance, cl_balance=cl_bal,
		payable_balance=payable_balance,
		al_eligible=al_eligible,
		payable_days_override=payable_days_override,
		payable_days_source=parent_id,
	)

	# --- Pretty-print helpers -------------------------------------------------
	def _f(v):
		return "{0:.2f}".format(flt(v))

	staff_type = emp.get("staff_type")
	location = emp.get("location")
	is_worker_site = (staff_type == "Worker" and location == "Site")
	is_worker_field_site = (staff_type in ("Worker", "Field") and location == "Site")
	is_worker_haridwar = (staff_type == "Worker" and location == "Haridwar")
	is_worker_noida_or_hwr = (staff_type == "Worker" and location in ("Noida", "Haridwar"))
	is_driver = (staff_type == "Driver")
	ot_eligible = is_worker_noida_or_hwr or is_driver

	present_dates = att.get("present_dates", set())
	half_day_dates = att.get("half_day_dates", set())
	absent_dates = att.get("absent_dates", set())
	processed_dates = att.get("processed_dates", set())
	working_hours = flt(att.get("working_hours", 0.0))
	false_count = att.get("false_attendance_count", 0)
	approved_full = len(leaves.get("full_leave_dates", set()))
	approved_half = len(leaves.get("half_leave_dates", set()))
	short_n = leaves.get("short_leave_count", 0)

	cl_balance = flt(cl_bal)
	al_balance = flt(balance.get("al_balance") or balance.get("year_opening_al") or 0)
	full_adv = flt(advance.get("full", 0.0))
	part_adv = flt(advance.get("part", 0.0))

	# Late-mark portion of Col K (mirrors _calculate_employee).
	_lc_half = cint(emp.get("late_count_for_half_day")) or 3
	_lc_full = cint(emp.get("late_count_for_full_day")) or 5
	_lc_treat = cint(emp.get("treat_late_as_half_day_after")) or 5
	_late_mark_deduction = 0.0
	if row["late_count"] >= _lc_full:
		_late_mark_deduction = 1.0
		if row["late_count"] > _lc_treat:
			_late_mark_deduction += (row["late_count"] - _lc_treat) * 0.5
	elif row["late_count"] >= _lc_half:
		_late_mark_deduction = 0.5

	al_reason = []
	if not is_worker_site:
		al_reason.append("not Worker@Site")
	if employee not in al_eligible_emps:
		al_reason.append("no OTPL Employee Leave Balance row")
	if emp.get("business_line") not in al_eligible_bls:
		al_reason.append("Business Line not AL-eligible")

	steps = [
		{
			"section": "Source",
			"items": [
				("Employee", "{0} ({1})".format(emp.get("employee_name"), employee)),
				("Sales Order / Business", "{0} / {1}".format(emp.get("sales_order") or "-", emp.get("business_line") or "-")),
				("Staff Type / Location", "{0} / {1}".format(staff_type or "-", location or "-")),
				("Period", "{0} → {1} ({2} days selected; {3} days in month)".format(from_date, to_date, days_in_period, days_in_month)),
				("UAN No / ESIC No", "{0} / {1}".format(emp.get("uan_no") or "-", emp.get("esic_no") or "-")),
				("Gross (Rate of Wages)", _f(emp.get("gross_salary"))),
				("Basic Salary", _f(emp.get("basic_salary"))),
				("Wage Bands (ESS Location)",
				 "Min Wages {0} | Max Wage PF {1} | Max Wage ESIC {2}"
				 .format(_f(emp.get("min_wages")), _f(emp.get("max_wage_pf")), _f(emp.get("max_wage_esic")))),
				("No Validation / Override Basic",
				 "{0} / {1}".format(cint(emp.get("no_validation")), _f(emp.get("no_validation_base_salary")))),
				("Opening AL (from OTPL Employee Leave Balance)",
				 "al_balance={0} | year_opening_al={1} | effective opening={2}"
				 .format(_f(balance.get("al_balance") or 0),
				         _f(balance.get("year_opening_al") or 0),
				         _f(al_balance))),
				("Holiday list dates in period", str(len(holiday_dates))),
				("AL Calculation", "ENABLED" if al_eligible else ("DISABLED — " + ", ".join(al_reason))),
			],
		},
		{
			"section": "Attendance",
			"items": [
				("Attendance Processed (excl. false)", str(len(processed_dates))),
				("Present days", str(len(present_dates))),
				("Half-day attendance due to Late Entry / Early Exit", str(row.get("extra_late_half_days", 0))),
				("Absent days", str(len(absent_dates))),
				("Late Entry/Early Exit", str(row.get("late_count", 0))),
				("Total working hours (Attendance.working_hours)", "{0:.2f}".format(working_hours)),
				("False attendances", str(false_count) + " (deducts 2 days each)"),
			],
		},
		{
			"section": "Approved Leaves (OTPL Leave)",
			"items": [
				("Approved full-day leaves (used for CL/AL adj.)", str(approved_full)),
				("Approved half-day leaves (from OTPL Leave, half_day=1)", str(approved_half)),
				("Approved short leaves", str(short_n)),
			],
		},
		{
			"section": "Computed Columns",
			"items": [
				("(G) AL Generated",
				 "{0}  —  {1}".format(row["al_generated"],
				                      "holidays with a present-ish day in BOTH the 3 days before AND the 3 days after"
				                      if al_eligible else "0 (AL disabled)")),
				("(H) Days Worked",
				 "{0}  —  non-holiday present + holidays qualifying by OR rule (present-ish before OR after); minus 2×{1} false attendance"
				 .format(_f(row["days_worked"]), false_count)),
				("(I) Late Marked", str(row["late_count"])),
				("(J) Half days marked due to extra late", str(row["extra_late_half_days"])),
				("(K) Late deduction days",
				 "{total} = approved half-day leaves ({ah}×0.5={ahv}) + extra-late half-days ({eh}×0.5={ehv}) "
				 "+ late-mark deduction ({lmv})  [Late Marked {lc}; thresholds: half@{h}, full@{f}, treat-as-half-after@{t}]"
				 .format(total=_f(row["late_deduction_days"]),
				         ah=approved_half, ahv=_f(approved_half * 0.5),
				         eh=row["extra_late_half_days"], ehv=_f(row["extra_late_half_days"] * 0.5),
				         lmv=_f(_late_mark_deduction), lc=row["late_count"],
				         h=cint(emp.get("late_count_for_half_day")) or 3,
				         f=cint(emp.get("late_count_for_full_day")) or 5,
				         t=cint(emp.get("treat_late_as_half_day_after")) or 5)),
				("(L) Absent w/o info",
				 "{0}  —  count of Attendance.status='Absent' (excl. false)".format(row["absent_no_info_days"])),
				("(M) Adjusted from CL",
				 "{0}  —  approved={1}, L={2}, AL Bal={3}, CL Bal={4}; CL covers up to 2 of (approved−L−AL Bal)"
				 .format(_f(row["adjusted_from_cl"]), approved_full, row["absent_no_info_days"],
				         _f(al_balance if al_eligible else 0), _f(cl_balance))),
				("(N) Adjusted from AL",
				 "{0}  —  {1}".format(_f(row["adjusted_from_al"]),
				                      "min(AL Bal {0}, approved {1} − L {2})".format(_f(al_balance), approved_full, row["absent_no_info_days"])
				                      if al_eligible else "0 (AL disabled)")),
				("(O) Balance CL", "{0} = {1} − {2}".format(_f(row["balance_cl"]), _f(cl_balance), _f(row["adjusted_from_cl"]))),
				("(P) Closing AL",
				 "{0}  —  {1}".format(_f(row["closing_al"]),
				                      "{0} + {1} − {2}".format(_f(al_balance), row["al_generated"], _f(row["adjusted_from_al"]))
				                      if al_eligible else "0 (AL disabled)")),
				("(Q) Payable Days",
				 "{0}  —  {1}".format(_f(row["payable_days"]),
				                       "TAKEN FROM PARENT employee {0} (this employee is set as that employee's dummy_employee)".format(parent_id)
				                       if parent_id else
				                       "H({0}) − K({1}) − L({2}) + M({3}) + N({4})   (can be negative)"
				                       .format(_f(row["days_worked"]),
				                               _f(row["late_deduction_days"]), row["absent_no_info_days"],
				                               _f(row["adjusted_from_cl"]), _f(row["adjusted_from_al"])))),
				("(R) Salary Amount",
				 "{0} = (Gross {1} / {2} days-in-month) × Q {3}"
				 .format(_f(row["salary_amount"]), _f(emp.get("gross_salary")),
				         days_in_month, _f(row["payable_days"]))),
				("(S) OT/HRA/Petrol",
				 "{0}  —  {1}".format(_f(row["ot_hra_petrol"]),
				                      "OT hours = [working_hours({0:.2f}) + holiday-dates({1}) × 8] − (H({2}) × 8) ; amount = OT × Gross/({3}×8)"
				                      .format(working_hours, len(holiday_dates), _f(row["days_worked"]), days_in_month)
				                      if ot_eligible else "N/A (only Worker@Noida/Haridwar or Driver)")),
				("(T) Incentive",
				 "{0}  —  {1}".format(_f(row["incentive"]),
				                      "Worker@Haridwar: present + qualified holidays ≥ {0} ⇒ ₹200".format(days_in_month)
				                      if is_worker_haridwar else "N/A")),
				("(U) Total Salary Due", "{0} = R + S + T{1}".format(
					_f(row["total_salary_due"]),
					"  (R negative ⇒ V–Z forced to 0)" if flt(row["salary_amount"]) < 0 else "")),
				("(V) PF Employee",
				 "{0}  —  {1}".format(_f(row["pf_employee_share"]),
				                      ("(PF basic / {0}) × Q × 12%  [basic={1}, band {2}–{3}; no_validation={4}, override_basic={5}]"
				                       .format(days_in_month, _f(emp.get("basic_salary")),
				                               _f(emp.get("min_wages")), _f(emp.get("max_wage_pf")),
				                               cint(emp.get("no_validation")), _f(emp.get("no_validation_base_salary"))))
				                      if emp.get("uan_no") else "0 (no UAN)")),
				("(W) ESIC Employee",
				 "{0}  —  {1}".format(_f(row["esic_employee_share"]),
				                      ("(ESIC basic / {0}) × Q × 0.75%  [basic={1}, band {2}–{3}; capped at max_wage_esic when basic exceeds it]"
				                       .format(days_in_month, _f(emp.get("basic_salary")),
				                               _f(emp.get("min_wages")), _f(emp.get("max_wage_esic"))))
				                      if emp.get("esic_no") else "0 (no ESIC)")),
				("(X) TDS", "{0}  —  from OTPL Employee Investment".format(_f(row["tds"]))),
				("(Y) PF Employer", "{0} = V × 13/12".format(_f(row["pf_employer_share"]))),
				("(Z) ESIC Employer", "{0} = W × 3.25/0.75".format(_f(row["esic_employer_share"]))),
				("(AA) Full Advance Salary Adjustment",
				 "{0}  —  GL balance on full-advance account as on {1}".format(_f(full_adv), to_date)),
				("(AB) Part Advance Salary Adjustment",
				 "{0}  —  sum of submitted Journal Entries (purpose='Part Advance Salary Adjustment', account=part-advance-account) posted on last day of selected month ({1}) where employee is the party"
				 .format(_f(part_adv), get_last_day(from_date))),
				("(AC) Net Payable", "{0} = U − V − W − X − AA − AB".format(_f(row["net_amount_payable"]))),
				("(AD) Expenses (Payroll Payable balance)",
				 "{0} = payroll-payable balance as on {1} {2} AB (balance ≥ 0 ⇒ − AB; balance < 0 ⇒ + AB)"
				 .format(_f(row["expenses_balance"]), to_date,
				         "−" if flt(payable_balance) >= 0 else "+")),
				("(AE) Extra Allowance",
				 "{0} = TADA {1} + HRA {2} + Conv {3} + Tel {4}"
				 .format(_f(row.get("extra_allowance", 0)),
				         _f(row.get("tada_amount", 0)),
				         _f(row.get("hra_amount", 0)),
				         _f(row.get("conveyance_amount", 0)),
				         _f(row.get("telephone_amount", 0)))),
				("(AF) Net Amount to Pay", "{0} = AC − AD + AE".format(_f(row["net_amount_to_pay"]))),
			],
		},
	]
	return {"steps": steps}

