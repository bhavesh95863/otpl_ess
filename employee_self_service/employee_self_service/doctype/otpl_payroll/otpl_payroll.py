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
from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, flt, getdate


# Constants from the salary spec
ESIC_GROSS_LIMIT = 21000.0
ESIC_EMPLOYEE_RATE = 0.0075
ESIC_EMPLOYER_FACTOR = 3.25 / 0.75
PF_EMPLOYEE_RATE = 0.12
PF_EMPLOYER_FACTOR = 13.0 / 12.0
WORKER_HARIDWAR_INCENTIVE = 200.0
STD_HOURS_PER_DAY = 8.5
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

	rows = frappe.db.sql(
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
			so.business_line                    AS business_line
		FROM `tabEmployee` e
		LEFT JOIN `tabSales Order` so
			ON so.name = e.sales_order
		WHERE {where}
		ORDER BY e.employee_name ASC
		""".format(where=filters["sql"]),
		filters["values"],
		as_dict=True,
	)
	return rows


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

	# Pull every dependency once, in O(N) grouped queries
	att_map = _fetch_attendance_aggregates(emp_ids, from_date, to_date)
	leave_map = _fetch_approved_leaves(emp_ids, from_date, to_date)
	holidays_by_emp = _fetch_holidays_per_employee(employees, from_date, to_date)
	balance_map = _fetch_leave_balances(emp_ids)
	tds_map = _fetch_tds(emp_ids, from_date)
	advance_map = _fetch_advance_balances(emp_ids, to_date)

	rows = []
	log_lines = []

	for emp in employees:
		try:
			row = _calculate_employee(
				emp,
				from_date=from_date,
				to_date=to_date,
				days_in_period=days_in_period,
				att=att_map.get(emp["employee"], {}),
				approved_leave_dates=leave_map.get(emp["employee"], set()),
				holiday_dates=holidays_by_emp.get(emp["employee"], set()),
				balance=balance_map.get(emp["employee"], {}),
				tds=tds_map.get(emp["employee"], 0.0),
				advance=advance_map.get(emp["employee"], {"full": 0.0, "part": 0.0}),
			)
			rows.append(row)
		except Exception:
			frappe.log_error(
				title="OTPL Payroll calc error: {0}".format(emp["employee"]),
				message=frappe.get_traceback(),
			)
			log_lines.append("{0}: ERROR (see Error Log)".format(emp["employee"]))

	return {"rows": rows, "log": log_lines}


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
		present_dates         set[date]   - submitted Present (excluding false)
		half_day_dates        set[date]   - submitted Half Day (excluding false)
		all_marked_dates      set[date]   - any submitted att (excluding false)
		late_count            int         - sum of custom_late_mark
		extra_late_half_days  int         - count of Half Day status w/ late_entry
		absent_no_info_count  int         - both checkin & checkout NULL on a marked
		                                    day with no leave
		checkin_seconds       int         - sum of (checkout - checkin) seconds
		full_checkin_days     int         - days where both checkin & checkout set
	"""
	if not emp_ids:
		return {}

	# `custom_late_mark` is a site-specific custom field and may not exist
	# on every site (e.g. winamore). Fall back to literal 0 when absent.
	has_late_mark = frappe.db.has_column("Attendance", "custom_late_mark")
	late_mark_expr = "COALESCE(a.custom_late_mark, 0)" if has_late_mark else "0"

	rows = frappe.db.sql(
		"""
		SELECT
			a.employee,
			a.attendance_date,
			a.status,
			a.checkin_time,
			a.checkout_time,
			{late_mark_expr}                  AS late_mark,
			COALESCE(a.late_entry, 0)         AS late_entry,
			COALESCE(a.false_attendance, 0)   AS false_attendance
		FROM `tabAttendance` a
		WHERE a.employee IN %(emp_ids)s
		  AND a.attendance_date BETWEEN %(from_date)s AND %(to_date)s
		  AND a.docstatus = 1
		""".format(late_mark_expr=late_mark_expr),
		{"emp_ids": tuple(emp_ids), "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)

	out = defaultdict(lambda: {
		"present_dates": set(),
		"half_day_dates": set(),
		"all_marked_dates": set(),
		"late_count": 0,
		"extra_late_half_days": 0,
		"checkin_seconds": 0,
		"full_checkin_days": 0,
		"false_attendance_count": 0,
		"_rows": [],   # for absent-no-info pass below (needs leave info)
	})

	for r in rows:
		if cint(r.false_attendance):
			# Per spec: each false attendance deducts 2 days from days worked.
			out[r.employee]["false_attendance_count"] += 1
			continue
		bucket = out[r.employee]
		d = getdate(r.attendance_date)
		bucket["all_marked_dates"].add(d)
		if r.status == "Present":
			bucket["present_dates"].add(d)
		elif r.status == "Half Day":
			bucket["half_day_dates"].add(d)
			if cint(r.late_entry):
				bucket["extra_late_half_days"] += 1
		bucket["late_count"] += cint(r.late_mark)

		if r.checkin_time and r.checkout_time:
			delta = (r.checkout_time - r.checkin_time).total_seconds()
			if delta > 0:
				bucket["checkin_seconds"] += delta
				bucket["full_checkin_days"] += 1

		bucket["_rows"].append({
			"date": d,
			"status": r.status,
			"checkin_time": r.checkin_time,
			"checkout_time": r.checkout_time,
		})

	return out


def _fetch_approved_leaves(emp_ids, from_date, to_date):
	"""Set of approved leave dates per employee.

	Uses OTPL Leave (status Approved) and expands the approved range into
	individual dates, intersecting with the payroll period.
	"""
	if not emp_ids:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT employee, approved_from_date, approved_to_date,
		       half_day, half_day_date
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

	out = defaultdict(set)
	for r in rows:
		start = max(getdate(r.approved_from_date), from_date)
		end = min(getdate(r.approved_to_date), to_date)
		d = start
		while d <= end:
			out[r.employee].add(d)
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
	if not emp_ids:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT employee, al_balance, cl_balance, year_opening_al, year_opening_cl
		FROM `tabOTPL Employee Leave Balance`
		WHERE employee IN %(emp_ids)s
		""",
		{"emp_ids": tuple(emp_ids)},
		as_dict=True,
	)
	return {r.employee: dict(r) for r in rows}


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


def _fetch_advance_balances(emp_ids, to_date):
	"""Per-employee Full / Part advance ledger balances.

	Uses the same ledger accounts that ``Salary Payable Request`` uses,
	configured in ``OTPL Accounting Settings``:

	  * ``full_advance_salary_adjustment`` -> Full Advance Salary Adj. (AA)
	  * ``part_advance_salary_adjustment`` -> Part Advance Salary Adj. (AB)

	Balance is computed as ``SUM(debit - credit)`` on tabGL Entry up to and
	including ``to_date`` for ``party_type='Employee'`` per account. Only
	positive balances (employee owes the company) become an adjustment;
	negative balances yield 0 so we never inflate the salary payable.
	"""
	out = {e: {"full": 0.0, "part": 0.0} for e in emp_ids}
	if not emp_ids:
		return out

	settings = frappe.db.get_value(
		"OTPL Accounting Settings", "OTPL Accounting Settings",
		["full_advance_salary_adjustment", "part_advance_salary_adjustment"],
		as_dict=True,
	) or {}

	def _bulk_balance(account):
		if not account:
			return {}
		rows = frappe.db.sql(
			"""
			SELECT party AS employee, SUM(debit - credit) AS bal
			FROM `tabGL Entry`
			WHERE party_type = 'Employee'
			  AND party IN %(emps)s
			  AND account = %(acc)s
			  AND posting_date <= %(d)s
			GROUP BY party
			""",
			{"emps": tuple(emp_ids), "acc": account, "d": to_date},
			as_dict=True,
		)
		return {r.employee: max(flt(r.bal), 0.0) for r in rows}

	full_map = _bulk_balance(settings.get("full_advance_salary_adjustment"))
	part_map = _bulk_balance(settings.get("part_advance_salary_adjustment"))

	for e in emp_ids:
		out[e]["full"] = full_map.get(e, 0.0)
		out[e]["part"] = part_map.get(e, 0.0)
	return out


# -----------------------------------------------------------------------------
# Per-employee calculation
# -----------------------------------------------------------------------------
def _calculate_employee(emp, from_date, to_date, days_in_period,
                        att, approved_leave_dates, holiday_dates,
                        balance, tds, advance):
	gross = flt(emp.get("gross_salary"))
	basic = flt(emp.get("basic_salary"))
	staff_type = emp.get("staff_type")
	location = emp.get("location")

	is_worker_site = (staff_type == "Worker" and location == "Site")
	is_worker_haridwar = (staff_type == "Worker" and location == "Haridwar")
	is_worker_noida_or_hwr = (staff_type == "Worker" and location in ("Noida", "Haridwar"))
	# Late tracking is N/A for Worker/Field at Site (per business rule).
	skip_late_metrics = (staff_type in ("Worker", "Field") and location == "Site")

	present_dates = att.get("present_dates", set())
	half_day_dates = att.get("half_day_dates", set())
	late_count = 0 if skip_late_metrics else att.get("late_count", 0)
	extra_late_half_days = 0 if skip_late_metrics else att.get("extra_late_half_days", 0)
	full_checkin_days = att.get("full_checkin_days", 0)
	checkin_seconds = att.get("checkin_seconds", 0)

	# ---- Col G: AL Generated --------------------------------------------------
	al_generated = 0
	if is_worker_site:
		# Holidays where employee was present on the day before AND day after
		for h in holiday_dates:
			if (h - timedelta(days=1)) in present_dates and (h + timedelta(days=1)) in present_dates:
				al_generated += 1

	# ---- Col H: Days Worked ---------------------------------------------------
	# Present (& half) on non-holiday days
	non_holiday_present = sum(
		1 for d in present_dates if d not in holiday_dates
	) + 0.5 * sum(1 for d in half_day_dates if d not in holiday_dates)

	if is_worker_site:
		days_worked = non_holiday_present + al_generated
	else:
		# Holidays where present at least once in the 3 preceding & 3 following days
		holiday_count = 0
		for h in holiday_dates:
			before = any(
				(h - timedelta(days=k)) in present_dates for k in (1, 2, 3)
			)
			after = any(
				(h + timedelta(days=k)) in present_dates for k in (1, 2, 3)
			)
			if before and after:
				holiday_count += 1
		days_worked = non_holiday_present + holiday_count

	# Spec row 15: each false attendance deducts 2 days from days worked.
	false_attendance_count = att.get("false_attendance_count", 0)
	days_worked -= 2 * false_attendance_count
	days_worked = max(days_worked, 0)

	# ---- Col K: Late deduction days ------------------------------------------
	if late_count >= 5:
		late_deduction = 1 + (late_count - 5) * 0.5
	elif late_count >= 3:
		late_deduction = 0.5
	else:
		late_deduction = 0
	late_deduction += extra_late_half_days * 0.5

	# ---- Col L: Absent w/o info (counts twice) -------------------------------
	absent_no_info = 0
	for r in att.get("_rows", []):
		if r["date"] in approved_leave_dates:
			continue
		if not r["checkin_time"] and not r["checkout_time"]:
			absent_no_info += 2

	# ---- Col M / N: Adjusted from CL / AL ------------------------------------
	cl_balance = flt(balance.get("cl_balance") or balance.get("year_opening_cl") or 0)
	al_balance = flt(balance.get("al_balance") or balance.get("year_opening_al") or 0)
	approved_leave_in_period = len(approved_leave_dates)

	if approved_leave_in_period >= 2:
		adj_cl = min(2, cl_balance)
	elif cl_balance > 0:
		adj_cl = min(approved_leave_in_period, cl_balance)
	else:
		adj_cl = 0

	# AL bank is only maintained for Worker @ Site (spec note in row 3).
	if is_worker_site:
		remaining_leave = max(0, approved_leave_in_period - adj_cl)
		adj_al = min(remaining_leave, al_balance)
		closing_al = al_balance + al_generated - adj_al
	else:
		adj_al = 0
		closing_al = 0

	# ---- Col O / P: Balances --------------------------------------------------
	balance_cl = cl_balance - adj_cl

	# ---- Col Q: Payable Days --------------------------------------------------
	payable_days = days_worked - late_deduction - absent_no_info + adj_cl + adj_al
	payable_days = max(payable_days, 0)

	# ---- Col R: Salary Amount -------------------------------------------------
	per_day = (gross / days_in_period) if days_in_period else 0
	salary_amount = per_day * payable_days

	# ---- Col S: OT/HRA/Petrol -------------------------------------------------
	ot_hra_petrol = 0.0
	if is_worker_noida_or_hwr and full_checkin_days and gross:
		ot_hours = (checkin_seconds / 3600.0) - (full_checkin_days * STD_HOURS_PER_DAY)
		hourly_rate = gross / (days_in_period * SALARY_HOURS_PER_DAY) if days_in_period else 0
		ot_hra_petrol = ot_hours * hourly_rate

	# ---- Col T: Incentive -----------------------------------------------------
	incentive = 0.0
	present_count = len(present_dates) + 0.5 * len(half_day_dates)
	if is_worker_haridwar and present_count >= days_in_period:
		incentive = WORKER_HARIDWAR_INCENTIVE

	# ---- Col U: Total Salary Due ---------------------------------------------
	total_salary_due = salary_amount + ot_hra_petrol + incentive

	# ---- Col V: PF Employee --------------------------------------------------
	pf_employee = 0.0
	if emp.get("uan_no") and basic and days_in_period:
		pf_employee = (basic / days_in_period) * payable_days * PF_EMPLOYEE_RATE

	# ---- Col W: ESIC Employee ------------------------------------------------
	esic_employee = 0.0
	if emp.get("esic_no") and gross and gross < ESIC_GROSS_LIMIT and days_in_period:
		esic_employee = (gross / days_in_period) * payable_days * ESIC_EMPLOYEE_RATE

	# ---- Col Y / Z: Employer shares -------------------------------------------
	pf_employer = pf_employee * PF_EMPLOYER_FACTOR
	esic_employer = esic_employee * ESIC_EMPLOYER_FACTOR

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
		"extra_late_half_days": extra_late_half_days,
		"late_deduction_days": flt(late_deduction, 2),
		"absent_no_info_days": absent_no_info,
		"adjusted_from_cl": flt(adj_cl, 2),
		"adjusted_from_al": flt(adj_al, 2),
		"balance_cl": flt(balance_cl, 2),
		"closing_al": flt(closing_al, 2),
		"payable_days": flt(payable_days, 2),
		"salary_amount": flt(salary_amount, 2),
		"ot_hra_petrol": flt(ot_hra_petrol, 2),
		"incentive": flt(incentive, 2),
		"total_salary_due": flt(total_salary_due, 2),
		"pf_employee_share": flt(pf_employee, 2),
		"esic_employee_share": flt(esic_employee, 2),
		"tds": flt(tds, 2),
		"pf_employer_share": flt(pf_employer, 2),
		"esic_employer_share": flt(esic_employer, 2),
		"full_advance_adjustment": flt(advance.get("full", 0.0), 2),
		"part_advance_adjustment": flt(advance.get("part", 0.0), 2),
		"expenses_balance": 0.0,
		"staff_type": staff_type,
		"location": location,
		"department": emp.get("department"),
	}
	# Net columns from the spec
	row["net_amount_payable"] = flt(
		row["total_salary_due"]
		- row["pf_employee_share"]
		- row["esic_employee_share"]
		- row["tds"]
		- row["full_advance_adjustment"]
		- row["part_advance_adjustment"],
		2,
	)
	row["net_amount_to_pay"] = flt(
		row["net_amount_payable"] - row["expenses_balance"], 2
	)
	return row


# -----------------------------------------------------------------------------
# Helpers (called from validate)
# -----------------------------------------------------------------------------
def _recompute_row_nets(row):
	row.net_amount_payable = flt(
		flt(row.total_salary_due)
		- flt(row.pf_employee_share)
		- flt(row.esic_employee_share)
		- flt(row.tds)
		- flt(row.full_advance_adjustment)
		- flt(row.part_advance_adjustment),
		2,
	)
	row.net_amount_to_pay = flt(
		flt(row.net_amount_payable) - flt(row.expenses_balance), 2
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
	doc.total_net_payable = t["net_pay"]
	doc.total_net_to_pay = t["net_to_pay"]


def _persist_leave_balances(doc):
	"""Roll the row's closing AL/CL into OTPL Employee Leave Balance."""
	for r in doc.employees:
		bal_name = frappe.db.get_value(
			"OTPL Employee Leave Balance", {"employee": r.employee}, "name"
		)
		if bal_name:
			frappe.db.set_value(
				"OTPL Employee Leave Balance",
				bal_name,
				{
					"al_balance": flt(r.closing_al),
					"cl_balance": flt(r.balance_cl),
					"as_on_date": doc.to_date,
				},
				update_modified=True,
			)
		else:
			bal = frappe.new_doc("OTPL Employee Leave Balance")
			bal.employee = r.employee
			bal.al_balance = flt(r.closing_al)
			bal.cl_balance = flt(r.balance_cl)
			bal.as_on_date = doc.to_date
			bal.flags.ignore_permissions = True
			bal.insert()
	frappe.db.commit()


# -----------------------------------------------------------------------------
# Calculation trace (used by the "View Calculation" dialog in the UI)
# -----------------------------------------------------------------------------
@frappe.whitelist()
def get_calculation_trace(doc, employee):
	"""Return a human-readable, step-by-step breakdown of how each column
	was computed for a single employee, using the same data sources as the
	main calculation.
	"""
	doc = frappe.parse_json(doc) if isinstance(doc, str) else doc
	from_date = getdate(doc.get("from_date"))
	to_date = getdate(doc.get("to_date"))
	if not from_date or not to_date:
		frappe.throw(_("From Date and To Date are required"))
	days_in_period = (to_date - from_date).days + 1

	emp = frappe.db.sql(
		"""
		SELECT
			e.name AS employee, e.employee_name, e.department, e.staff_type,
			e.location, e.sales_order, e.uan_no, e.esi_number AS esic_no,
			e.advance_to_be_deducted AS gross_salary, e.basic_salary,
			e.holiday_list, so.business_line
		FROM `tabEmployee` e
		LEFT JOIN `tabSales Order` so ON so.name = e.sales_order
		WHERE e.name = %(emp)s
		""",
		{"emp": employee},
		as_dict=True,
	)
	if not emp:
		frappe.throw(_("Employee {0} not found").format(employee))
	emp = emp[0]

	att_map = _fetch_attendance_aggregates([employee], from_date, to_date)
	leave_map = _fetch_approved_leaves([employee], from_date, to_date)
	holidays_by_emp = _fetch_holidays_per_employee([emp], from_date, to_date)
	balance_map = _fetch_leave_balances([employee])
	tds_map = _fetch_tds([employee], from_date)
	advance_map = _fetch_advance_balances([employee], to_date)

	att = att_map.get(employee, {})
	approved_leaves = leave_map.get(employee, set())
	holiday_dates = holidays_by_emp.get(employee, set())
	balance = balance_map.get(employee, {})
	tds = tds_map.get(employee, 0.0)
	advance = advance_map.get(employee, {"full": 0.0, "part": 0.0})

	gross = flt(emp.get("gross_salary"))
	basic = flt(emp.get("basic_salary"))
	staff_type = emp.get("staff_type")
	location = emp.get("location")
	is_worker_site = (staff_type == "Worker" and location == "Site")
	is_worker_haridwar = (staff_type == "Worker" and location == "Haridwar")
	is_worker_noida_or_hwr = (staff_type == "Worker" and location in ("Noida", "Haridwar"))
	skip_late_metrics = (staff_type in ("Worker", "Field") and location == "Site")

	present_dates = att.get("present_dates", set())
	half_day_dates = att.get("half_day_dates", set())
	all_marked = att.get("all_marked_dates", set())
	late_count = 0 if skip_late_metrics else att.get("late_count", 0)
	extra_late_half_days = 0 if skip_late_metrics else att.get("extra_late_half_days", 0)
	full_checkin_days = att.get("full_checkin_days", 0)
	checkin_seconds = att.get("checkin_seconds", 0)
	false_count = att.get("false_attendance_count", 0)

	non_holiday_present = sum(
		1 for d in present_dates if d not in holiday_dates
	) + 0.5 * sum(1 for d in half_day_dates if d not in holiday_dates)

	# AL Generated
	al_generated = 0
	if is_worker_site:
		for h in holiday_dates:
			if (h - timedelta(days=1)) in present_dates and (h + timedelta(days=1)) in present_dates:
				al_generated += 1

	# Days Worked
	if is_worker_site:
		days_worked_pre = non_holiday_present + al_generated
		dw_explain = ("Worker @ Site: non-holiday present ({0}) + AL Generated ({1}) = {2}"
		              .format(non_holiday_present, al_generated, days_worked_pre))
	else:
		hc = 0
		for h in holiday_dates:
			if any((h - timedelta(days=k)) in present_dates for k in (1, 2, 3)) \
			   and any((h + timedelta(days=k)) in present_dates for k in (1, 2, 3)):
				hc += 1
		days_worked_pre = non_holiday_present + hc
		dw_explain = ("Non-(Worker@Site): non-holiday present ({0}) + qualifying holidays ({1}) = {2}"
		              .format(non_holiday_present, hc, days_worked_pre))
	days_worked = max(days_worked_pre - 2 * false_count, 0)

	# Late deduction
	if late_count >= 5:
		late_ded = 1 + (late_count - 5) * 0.5
		k_explain = "L>=5 ⇒ 1 + (L-5)*0.5 = 1 + ({0}-5)*0.5 = {1}".format(late_count, late_ded)
	elif late_count >= 3:
		late_ded = 0.5
		k_explain = "3 ≤ L < 5 ⇒ 0.5"
	else:
		late_ded = 0
		k_explain = "L < 3 ⇒ 0"
	late_ded += extra_late_half_days * 0.5

	# Absent w/o info
	absent_no_info = 0
	for r in att.get("_rows", []):
		if r["date"] in approved_leaves:
			continue
		if not r["checkin_time"] and not r["checkout_time"]:
			absent_no_info += 2

	# Leaves
	cl_balance = flt(balance.get("cl_balance") or balance.get("year_opening_cl") or 0)
	al_balance = flt(balance.get("al_balance") or balance.get("year_opening_al") or 0)
	approved_n = len(approved_leaves)
	if approved_n >= 2:
		adj_cl = min(2, cl_balance)
	elif cl_balance > 0:
		adj_cl = min(approved_n, cl_balance)
	else:
		adj_cl = 0
	remaining = max(0, approved_n - adj_cl)
	if is_worker_site:
		adj_al = min(remaining, al_balance)
		closing_al = al_balance + al_generated - adj_al
	else:
		adj_al = 0
		closing_al = 0

	balance_cl = cl_balance - adj_cl

	payable_days = max(days_worked - late_ded - absent_no_info + adj_cl + adj_al, 0)
	per_day = (gross / days_in_period) if days_in_period else 0
	salary_amount = per_day * payable_days

	ot = 0.0
	ot_explain = "N/A (only Worker @ Noida/Haridwar)"
	if is_worker_noida_or_hwr and full_checkin_days and gross:
		ot_hours = (checkin_seconds / 3600.0) - (full_checkin_days * STD_HOURS_PER_DAY)
		hourly_rate = gross / (days_in_period * SALARY_HOURS_PER_DAY)
		ot = ot_hours * hourly_rate
		ot_explain = ("OT hrs = {0:.2f}h - ({1} days * {2}h) = {3:.2f}h; rate = {4:.2f}/h ⇒ {5:.2f}"
		              .format(checkin_seconds / 3600.0, full_checkin_days,
		                      STD_HOURS_PER_DAY, ot_hours, hourly_rate, ot))

	incentive = 0.0
	pcount = len(present_dates) + 0.5 * len(half_day_dates)
	if is_worker_haridwar and pcount >= days_in_period:
		incentive = WORKER_HARIDWAR_INCENTIVE

	total_due = salary_amount + ot + incentive

	pf_emp = 0.0
	if emp.get("uan_no") and basic and days_in_period:
		pf_emp = (basic / days_in_period) * payable_days * PF_EMPLOYEE_RATE

	esic_emp = 0.0
	if emp.get("esic_no") and gross and gross < ESIC_GROSS_LIMIT and days_in_period:
		esic_emp = (gross / days_in_period) * payable_days * ESIC_EMPLOYEE_RATE

	pf_er = pf_emp * PF_EMPLOYER_FACTOR
	esic_er = esic_emp * ESIC_EMPLOYER_FACTOR

	full_adv = flt(advance.get("full", 0.0))
	part_adv = flt(advance.get("part", 0.0))
	net_payable = total_due - pf_emp - esic_emp - tds - full_adv - part_adv

	def _fmt(v):
		return "{0:.2f}".format(flt(v))

	steps = [
		{
			"section": "Source",
			"items": [
				("Employee", "{0} ({1})".format(emp.get("employee_name"), employee)),
				("Sales Order / Business", "{0} / {1}".format(emp.get("sales_order") or "-", emp.get("business_line") or "-")),
				("Staff Type / Location", "{0} / {1}".format(staff_type or "-", location or "-")),
				("Period", "{0} → {1} ({2} days)".format(from_date, to_date, days_in_period)),
				("UAN No / ESIC No", "{0} / {1}".format(emp.get("uan_no") or "-", emp.get("esic_no") or "-")),
				("Gross (Rate of Wages)", _fmt(gross)),
				("Basic Salary", _fmt(basic)),
				("Holiday list dates in period", str(len(holiday_dates))),
			],
		},
		{
			"section": "Attendance",
			"items": [
				("Attendances submitted (excl. false)", str(len(all_marked))),
				("Present days", str(len(present_dates))),
				("Half days", str(len(half_day_dates))),
				("Late marks (custom_late_mark)", str(late_count)),
				("Half days with late_entry", str(extra_late_half_days)),
				("Days w/ both check-in & check-out", str(full_checkin_days)),
				("Total checked-in hours", "{0:.2f}".format(checkin_seconds / 3600.0)),
				("False attendances", str(false_count) + " (deducts 2 days each)"),
				("Approved leave dates", str(approved_n)),
			],
		},
		{
			"section": "Computed Columns",
			"items": [
				("(G) AL Generated",
				 "{0}  —  {1}".format(al_generated,
				                      "Worker@Site only: holidays where present on day before AND after"
				                      if is_worker_site else "N/A (only Worker@Site)")),
				("(H) Days Worked",
				 "{0}  —  {1}; minus 2×{2} false = {3}"
				 .format(_fmt(days_worked), dw_explain, false_count, _fmt(days_worked))),
				("(I) Late count (L)",
				 "{0}  \u2014  {1}".format(late_count,
				                          "N/A for Worker/Field @ Site" if skip_late_metrics else "from custom_late_mark")),
				("(J) Extra-late half days",
				 "{0}  \u2014  {1}".format(extra_late_half_days,
				                          "N/A for Worker/Field @ Site" if skip_late_metrics else "Half Day with late_entry")),
				("(K) Late deduction days",
				 "{0}  —  {1}; + J*0.5 = {2}*0.5".format(_fmt(late_ded), k_explain, extra_late_half_days)),
				("(L) Absent w/o info (×2)",
				 "{0}  —  marked days w/o leave & both check-in/out NULL, counted ×2".format(absent_no_info)),
				("(M) Adjusted from CL",
				 "{0}  —  approved={1}, CL bal={2}".format(_fmt(adj_cl), approved_n, _fmt(cl_balance))),
				("(N) Adjusted from AL",
				 "{0}  \u2014  {1}".format(_fmt(adj_al),
				                          "remaining leaves={0}, AL bal={1}".format(remaining, _fmt(al_balance))
				                          if is_worker_site else "N/A (only Worker@Site)")),
				("(O) Balance CL", "{0} = {1} \u2212 {2}".format(_fmt(balance_cl), _fmt(cl_balance), _fmt(adj_cl))),
				("(P) Closing AL",
				 "{0}  \u2014  {1}".format(_fmt(closing_al),
				                          "{0} + {1} \u2212 {2}".format(_fmt(al_balance), al_generated, _fmt(adj_al))
				                          if is_worker_site else "N/A (only Worker@Site)")),
				("(Q) Payable Days",
				 "{0} = H({1}) − K({2}) − L({3}) + M({4}) + N({5})"
				 .format(_fmt(payable_days), _fmt(days_worked), _fmt(late_ded),
				         absent_no_info, _fmt(adj_cl), _fmt(adj_al))),
				("(R) Salary Amount",
				 "{0} = (Gross {1} / {2}) × Q {3} = {4} × {3}"
				 .format(_fmt(salary_amount), _fmt(gross), days_in_period,
				         _fmt(payable_days), _fmt(per_day))),
				("(S) OT/HRA/Petrol", "{0}  —  {1}".format(_fmt(ot), ot_explain)),
				("(T) Incentive",
				 "{0}  —  {1}".format(_fmt(incentive),
				                      "Worker@Haridwar present every day ⇒ ₹200"
				                      if is_worker_haridwar else "N/A")),
				("(U) Total Salary Due", "{0} = R + S + T".format(_fmt(total_due))),
				("(V) PF Employee",
				 "{0}  —  {1}".format(_fmt(pf_emp),
				                      "(Basic {0}/{1}) × Q × 12%".format(_fmt(basic), days_in_period)
				                      if emp.get("uan_no") else "0 (no UAN)")),
				("(W) ESIC Employee",
				 "{0}  —  {1}".format(_fmt(esic_emp),
				                      "(Gross/{0}) × Q × 0.75%".format(days_in_period)
				                      if (emp.get("esic_no") and gross < ESIC_GROSS_LIMIT)
				                      else ("0 (Gross ≥ {0})".format(ESIC_GROSS_LIMIT)
				                            if emp.get("esic_no") else "0 (no ESIC)"))),
				("(X) TDS", "{0}  —  from OTPL Employee Investment".format(_fmt(tds))),
				("(Y) PF Employer", "{0} = V × 13/12".format(_fmt(pf_er))),
				("(Z) ESIC Employer", "{0} = W × 3.25/0.75".format(_fmt(esic_er))),
				("(AA) Full Advance Salary Adjustment",
				 "{0}  —  GL balance on 'full_advance_salary_adjustment' account (per OTPL Accounting Settings) as on {1}".format(_fmt(full_adv), to_date)),
				("(AB) Part Advance Salary Adjustment",
				 "{0}  —  GL balance on 'part_advance_salary_adjustment' account (per OTPL Accounting Settings) as on {1}".format(_fmt(part_adv), to_date)),
				("(AC) Net Payable", "{0} = U − V − W − X − AA − AB".format(_fmt(net_payable))),
			],
		},
	]
	return {"steps": steps}

