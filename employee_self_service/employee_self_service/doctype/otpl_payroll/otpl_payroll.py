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
from datetime import timedelta, datetime, time
from decimal import Decimal

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, flt, getdate, get_last_day, get_datetime

from employee_self_service.employee_self_service.utils.daily_attendance import (
	normalize_half_day_period,
)
from employee_self_service.employee_self_service.doctype.otpl_tds.otpl_tds import MONTHS


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

# --- Holiday qualifying ("sandwich") rule -----------------------------------
# A holiday is earned when the employee was present around it. The window is
# counted in WORKING days, NOT calendar days: OTHER HOLIDAYS are stepped over
# rather than consuming a slot, so the walk always lands on three days the
# employee was rostered to work.
#
# Holidays are the ONLY thing skipped. A day of leave is a working day the
# employee did not work: it uses up one of the three slots and does not count as
# presence — so three straight days of leave next to a holiday disqualify it.
QUALIFY_WORKING_DAYS = 3
# Hard cap on how far (in calendar days) the walk may travel to collect those
# working days. It doubles as the margin of neighbouring-month attendance and
# holiday data that is fetched, so a holiday at the edge of the period is still
# judged against real data rather than an empty window. Only holidays are
# skipped, so this only has to clear the longest run of consecutive holidays.
QUALIFY_MARGIN_DAYS = 21

# --- Driver OT rule (hardcoded per business spec) --------------------------
# Duty ends at 19:30 and OT is banded by the hour the driver PUNCHES OUT in —
# the hour is paid as soon as it is entered, not once it is completed. So a
# checkout at 19:31 already earns the first ₹100, where the old
# completed-hours rule paid nothing until 20:30.
#
#     19:30 – 20:30 -> ₹100
#     20:30 – 21:30 -> ₹200
#     21:30 – 22:30 -> ₹300
#     22:30 – 23:30 -> ₹400
#     after 23:30   -> ₹700 flat, and the hourly bands do NOT apply on top
#
# The bands apply whether the driver was local or out of station. A holiday the
# driver actually worked is a flat ₹700 instead of the day's bands, again local
# or out of station.
DRIVER_OT_FLAT = 700.0
# Flat OT for a holiday the driver actually worked. NOTE: this is paid for EVERY
# worked holiday, not only the ones that pass the qualifying (sandwich) rule —
# longstanding behaviour, unchanged here. Only the QUALIFYING ones are netted
# back out of Days Worked, since only those were counted into it.
DRIVER_HOLIDAY_OT = 700.0
# Value of each hourly band entered past the duty end.
DRIVER_OT_PER_HOUR = 100.0
# Flat allowance for a WORKING day the driver spent out of station. It is an
# allowance for being away, not overtime, so it stacks ON TOP of whatever the
# checkout earned that day — including the flat ₹700 for a post-23:30 punch-out
# (the "after 11.30 pm" rule cancels the hourly bands, not this). Holidays are
# out of scope: a worked holiday already pays its own flat rate, local or away.
DRIVER_OUT_OF_STATION_OT = 200.0
DRIVER_DUTY_END = time(19, 30)
# Absolute clock time past which the flat ₹700 applies, replacing the bands.
DRIVER_OT_MAX_AFTER = time(23, 30)
# Upper edge of each hourly band, in order. Entering a band pays for it.
DRIVER_OT_BAND_ENDS = (time(20, 30), time(21, 30), time(22, 30), time(23, 30))


def _driver_checkout_ot(checkout_dt, att_date):
	"""Driver OT for a single day's checkout time (hardcoded bands).

	₹100 for each hourly band the punch-out falls in, counted from the 19:30
	duty end. The hour is paid on ENTRY, not on completion:

	    <= 19:30 -> ₹0      (not past duty end)
	    <= 20:30 -> ₹100    (punched out in the first hour)
	    <= 21:30 -> ₹200
	    <= 22:30 -> ₹300
	    <= 23:30 -> ₹400
	    >  23:30 -> ₹700    (flat; the bands do not apply as well)

	Applies to local and out-of-station days alike.

	Boundaries are computed as datetimes on ``att_date`` so a checkout after
	midnight (next-day timestamp) correctly lands in the after-23:30 band."""
	if not checkout_dt:
		return 0.0
	base = getdate(att_date)
	c = get_datetime(checkout_dt)

	def at(t):
		return get_datetime(datetime.combine(base, t))

	# Tested first: a next-day (post-midnight) timestamp is past every boundary.
	if c > at(DRIVER_OT_MAX_AFTER):
		return DRIVER_OT_FLAT
	if c <= at(DRIVER_DUTY_END):
		return 0.0
	for i, band_end in enumerate(DRIVER_OT_BAND_ENDS):
		if c <= at(band_end):
			return DRIVER_OT_PER_HOUR * (i + 1)
	return DRIVER_OT_PER_HOUR * len(DRIVER_OT_BAND_ENDS)


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
		_sync_order_allocations(self)

	def on_submit(self):
		"""Persist the closing AL/CL into OTPL Employee Leave Balance so it
		becomes the opening for the next payroll run, then book the payroll
		into accounting.
		"""
		_persist_leave_balances(self)
		create_salary_entries(self)

	def on_cancel(self):
		"""Reverse the booking: cancel the journal entries this payroll posted
		and drop the draft payment requests raised from it."""
		# The vouchers point back here via otpl_ref_name, which would otherwise
		# block cancelling the payroll itself.
		self.ignore_linked_doctypes = ("Journal Entry", "Salary Payable Request", "GL Entry",
		                               "OTPL TDS", "OTPL TDS Detail")
		_cancel_payroll_bookings(self)
		_cancel_payroll_tds_entries(self)


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


def _business_line_sql():
	"""SQL expression resolving an employee's Business Line from their own
	``business_vertical`` (then ``external_business_vertical``), the same source
	the OTPL Leave approver flow uses. Sales Order is not used as a source.
	References the ``e`` alias, so callers must join Employee as ``e``.
	"""
	terms = []
	if frappe.db.has_column("Employee", "business_vertical"):
		terms.append("NULLIF(e.business_vertical, '')")
	if frappe.db.has_column("Employee", "external_business_vertical"):
		terms.append("NULLIF(e.external_business_vertical, '')")
	if not terms:
		return "NULL"
	return "COALESCE({0})".format(", ".join(terms))


def _select_employees(where_sql, where_values):
	"""Internal helper: run the canonical employee SELECT used by payroll
	calculation. ``where_sql`` is appended after ``e.status='Active'``-style
	conditions already enforced by ``_build_employee_filter`` (or a raw
	predicate when fetching by explicit IDs).
	"""
	dummy_expr = "e.dummy_employee" if frappe.db.has_column("Employee", "dummy_employee") else "NULL"
	business_line_expr = _business_line_sql()
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
			-- PF/ESIC wage bands: for location='Site' these are sourced from a
			-- fixed ESS Location (esl_site) instead of the employee's own
			-- location, which has no wage band. Sales Order OPAUT-00003 -> use
			-- "Haridwar" bands; any other sales order -> use "Noida" bands.
			COALESCE(CASE WHEN e.location = 'Site' THEN esl_site.min_wages     ELSE esl.min_wages     END, 0) AS min_wages,
			COALESCE(CASE WHEN e.location = 'Site' THEN esl_site.max_wage_pf   ELSE esl.max_wage_pf   END, 0) AS max_wage_pf,
			COALESCE(CASE WHEN e.location = 'Site' THEN esl_site.max_wage_esic ELSE esl.max_wage_esic END, 0) AS max_wage_esic,
			COALESCE(esl.late_count_for_half_day, 3) AS late_count_for_half_day,
			COALESCE(esl.late_count_for_full_day, 5) AS late_count_for_full_day,
			COALESCE(esl.treat_late_as_half_day_after, 5) AS treat_late_as_half_day_after,
			COALESCE(e.no_validation_base_salary, 0) AS no_validation_base_salary,
			{tada_expr}                         AS daily_tada,
			{hra_expr}                          AS hra_amount,
			{conv_expr}                         AS conveyance_amount,
			{tel_expr}                          AS telephone_amount,
			{dummy_expr}                        AS dummy_employee,
			{business_line_expr}                AS business_line
		FROM `tabEmployee` e
		LEFT JOIN `tabESS Location` esl
			ON esl.name = e.location
		-- Fixed ESS Location used ONLY to source PF/ESIC wage bands for
		-- location='Site' employees (see the min_wages/max_wage_* CASEs above).
		-- OPAUT-00003 -> Haridwar; anything else (incl. NULL) -> Noida.
		LEFT JOIN `tabESS Location` esl_site
			ON esl_site.name = CASE
				WHEN e.sales_order = 'OPAUT-00003' THEN 'Haridwar'
				ELSE 'Noida'
			END
		WHERE {where}
		ORDER BY e.employee_name ASC
		""".format(
			where=where_sql,
			tada_expr="COALESCE(e.daily_tada, 0)" if frappe.db.has_column("Employee", "daily_tada") else "0",
			hra_expr="COALESCE(e.hra_amount, 0)" if frappe.db.has_column("Employee", "hra_amount") else "0",
			conv_expr="COALESCE(e.conveyance_amount, 0)" if frappe.db.has_column("Employee", "conveyance_amount") else "0",
			tel_expr="COALESCE(e.telephone_amount, 0)" if frappe.db.has_column("Employee", "telephone_amount") else "0",
			dummy_expr=dummy_expr,
			business_line_expr=business_line_expr,
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


def _fetch_latest_gross_salary(emp_ids, as_on_date):
	"""Latest Employee Gross Salary (with date <= as_on_date) per employee.

	Returns ``{employee: {"amount": float, "date": date}}`` for employees that
	have such a record. Employees without one are absent from the dict, so the
	caller falls back to the Employee-level gross figure.
	"""
	if not emp_ids or not frappe.db.table_exists("Employee Gross Salary"):
		return {}

	rows = frappe.db.sql(
		"""
		SELECT employee, gross_salary_amount, `date`
		FROM `tabEmployee Gross Salary`
		WHERE employee IN %(emp_ids)s
		  AND `date` <= %(as_on)s
		  AND docstatus < 2
		ORDER BY `date` DESC, modified DESC
		""",
		{"emp_ids": tuple(emp_ids), "as_on": as_on_date},
		as_dict=True,
	)

	out = {}
	for r in rows:
		# First row per employee is the latest (date DESC, then most recently
		# modified as a tiebreaker for same-date records).
		if r.employee not in out:
			out[r.employee] = {"amount": flt(r.gross_salary_amount), "date": r.date}
	return out



def _apply_gross_override(employees, gross_override_map):
	"""Apply Employee Gross Salary records over the Employee-master figures.

	Basic salary has to move with the gross. The Employee Gross Salary form
	records only a gross amount, and the master's own convention is basic =
	gross / 2 (it holds for every active employee), so an override that raised
	the gross used to leave basic behind at the stale master figure. Basic is
	not merely displayed: it is the PF and ESIC base (within the ESS Location
	wage bands, and unless the employee carries no_validation), so a stale
	basic silently mis-states those deductions as well as the breakdown.
	"""
	for e in employees:
		if not e:
			continue
		override = gross_override_map.get(e["employee"])
		if not override:
			continue
		e["gross_salary"] = override["amount"]
		e["basic_salary"] = flt(override["amount"]) / 2.0


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

	# Gross salary override: prefer the latest Employee Gross Salary record with
	# date <= from_date; otherwise keep the Employee-level figure.
	gross_override_map = _fetch_latest_gross_salary(all_ids, from_date)
	_apply_gross_override(all_emps, gross_override_map)

	# Pull every dependency once, in O(N) grouped queries
	att_map = _fetch_attendance_aggregates(all_ids, from_date, to_date)
	lookahead_map = _fetch_lookahead_presentish(all_ids, to_date)
	lookbehind_map = _fetch_lookbehind_presentish(all_ids, from_date)
	leave_map = _fetch_approved_leaves(all_ids, from_date, to_date)
	travelling_map = _fetch_travelling_dates(all_ids, from_date, to_date)
	holidays_by_emp = _fetch_holidays_per_employee(all_emps, from_date, to_date)
	# Same holidays plus a margin either side. The qualifying walk steps OVER
	# holidays, so it must know about the ones just outside the period too —
	# otherwise a neighbouring-month holiday is mistaken for a working day.
	holiday_margin_by_emp = _fetch_holidays_per_employee(
		all_emps,
		from_date - timedelta(days=QUALIFY_MARGIN_DAYS),
		to_date + timedelta(days=QUALIFY_MARGIN_DAYS))
	balance_map = _fetch_leave_balances(all_ids)
	cl_balance_map = _fetch_cl_balances(all_ids, from_date)
	cl_generated_map = _fetch_holiday_cl_credits(all_ids, from_date, to_date)
	lwp_map = _fetch_lwp_leave_dates(all_ids, from_date, to_date)
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
			lookahead_presentish=lookahead_map.get(emp_id, set()),
			lookbehind_presentish=lookbehind_map.get(emp_id, set()),
			leaves=leave_map.get(emp_id, {"full_leave_dates": set(), "half_leave_dates": set(), "short_leave_count": 0}),
			holiday_dates=holidays_by_emp.get(emp_id, set()),
			balance=balance_map.get(emp_id, {}),
			cl_balance=cl_balance_map.get(emp_id, 0.0),
			cl_generated=cl_generated_map.get(emp_id, 0.0),
			lwp_dates=lwp_map.get(emp_id, set()),
			neighbour_holidays=holiday_margin_by_emp.get(emp_id, set()),
			travelling_dates=travelling_map.get(emp_id, set()),
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
				lookahead_presentish=lookahead_map.get(eid, set()),
				lookbehind_presentish=lookbehind_map.get(eid, set()),
				leaves=leave_map.get(eid, {"full_leave_dates": set(), "half_leave_dates": set(), "short_leave_count": 0}),
				holiday_dates=holidays_by_emp.get(eid, set()),
				balance=balance_map.get(eid, {}),
				cl_balance=cl_balance_map.get(eid, 0.0),
				cl_generated=cl_generated_map.get(eid, 0.0),
				lwp_dates=lwp_map.get(eid, set()),
				neighbour_holidays=holiday_margin_by_emp.get(eid, set()),
				travelling_dates=travelling_map.get(eid, set()),
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

	# Sales-order wise split of each row's payable days / salary, driven by
	# the order stamped on each day's Employee Checkin.
	order_days_map = _fetch_order_days(emp_ids, from_date, to_date)
	allocations = _build_order_allocations(rows, order_days_map, log_lines)

	return {"rows": rows, "log": log_lines, "allocations": allocations}


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
		conditions.append("{0} = %(business_line)s".format(_business_line_sql()))
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
		late_count               int      - # days flagged late_entry or early_exit
		late_entry_count         int      - # days with late_entry checked
		early_exit_count         int      - # days with early_exit checked
		extra_late_entry_count   int      - # days with extra_late_entry checked
		extra_early_exit_count   int      - # days with extra_early_exit checked
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
	# Extra Late Entry / Extra Early Exit are newer custom checkboxes and may not
	# exist on every site; fall back to 0 when absent.
	has_extra_late = frappe.db.has_column("Attendance", "extra_late_entry")
	extra_late_expr = "COALESCE(a.extra_late_entry, 0)" if has_extra_late else "0"
	has_extra_early = frappe.db.has_column("Attendance", "extra_early_exit")
	extra_early_expr = "COALESCE(a.extra_early_exit, 0)" if has_extra_early else "0"

	rows = frappe.db.sql(
		"""
		SELECT
			a.employee,
			a.attendance_date,
			a.status,
			{late_mark_expr}                  AS late_mark,
			COALESCE(a.late_entry, 0)         AS late_entry,
			COALESCE(a.early_exit, 0)         AS early_exit,
			{extra_late_expr}                 AS extra_late_entry,
			{extra_early_expr}                AS extra_early_exit,
			{working_hours_expr}              AS working_hours,
			a.checkout_time                   AS checkout_time,
			COALESCE(a.false_attendance, 0)   AS false_attendance
		FROM `tabAttendance` a
		WHERE a.employee IN %(emp_ids)s
		  AND a.attendance_date BETWEEN %(from_date)s AND %(to_date)s
		  AND a.docstatus = 1
		""".format(late_mark_expr=late_mark_expr, working_hours_expr=working_hours_expr,
		           extra_late_expr=extra_late_expr, extra_early_expr=extra_early_expr),
		{"emp_ids": tuple(emp_ids), "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)

	out = defaultdict(lambda: {
		"processed_dates": set(),
		"present_dates": set(),
		"half_day_dates": set(),
		"absent_dates": set(),
		"late_count": 0,
		"late_entry_count": 0,
		"early_exit_count": 0,
		"extra_late_entry_count": 0,
		"extra_early_exit_count": 0,
		"working_hours": 0.0,
		"false_attendance_count": 0,
		# date -> checkout datetime, for the Driver checkout-tier OT rule.
		"checkout_by_date": {},
	})

	for r in rows:
		if cint(r.false_attendance):
			out[r.employee]["false_attendance_count"] += 1
			continue
		bucket = out[r.employee]
		d = getdate(r.attendance_date)
		bucket["processed_dates"].add(d)
		if r.get("checkout_time"):
			bucket["checkout_by_date"][d] = get_datetime(r.checkout_time)
		if r.status == "Present":
			bucket["present_dates"].add(d)
		elif r.status == "Half Day":
			bucket["half_day_dates"].add(d)
		elif r.status == "Absent":
			bucket["absent_dates"].add(d)
		# Late / early marks: counted separately (their total drives the ESS
		# Location count rule). late_count is the per-day tally kept for info.
		if cint(r.late_entry):
			bucket["late_entry_count"] += 1
		if cint(r.early_exit):
			bucket["early_exit_count"] += 1
		if cint(r.late_entry) or cint(r.early_exit):
			bucket["late_count"] += 1
		# Extra Late / Early check marks drive the extra-late half-day deduction.
		if cint(r.extra_late_entry):
			bucket["extra_late_entry_count"] += 1
		if cint(r.extra_early_exit):
			bucket["extra_early_exit_count"] += 1
		bucket["working_hours"] += flt(r.working_hours)

	return out


def _fetch_lookahead_presentish(emp_ids, to_date):
	"""Present-ish dates in the ``QUALIFY_MARGIN_DAYS`` calendar days AFTER ``to_date``.

	Used only to qualify holidays that fall at (or near) the end of the
	payroll period: their "3 working days following" window spills into the next
	month, so the attendance for those next-month days is needed to decide
	whether the holiday qualifies (Col G / Col H).

	The margin is wider than the 3 working days the rule asks for because the
	walk STEPS OVER holidays — a run of consecutive holidays right after the
	period pushes the third working day past the third calendar day.

	Returns dict employee -> set[date] (dates strictly after ``to_date``).
	"""
	return _fetch_presentish_in_window(
		emp_ids, to_date + timedelta(days=1), to_date + timedelta(days=QUALIFY_MARGIN_DAYS)
	)


def _fetch_lookbehind_presentish(emp_ids, from_date):
	"""Present-ish dates in the ``QUALIFY_MARGIN_DAYS`` calendar days BEFORE ``from_date``.

	The mirror image of ``_fetch_lookahead_presentish``: a holiday at (or near)
	the START of the payroll period has its "3 working days preceding" window in
	the previous month, so without this the employee's late-previous-month
	attendance is invisible and the holiday is wrongly disqualified (Col G /
	Col H).

	Returns dict employee -> set[date] (dates strictly before ``from_date``).
	"""
	return _fetch_presentish_in_window(
		emp_ids, from_date - timedelta(days=QUALIFY_MARGIN_DAYS), from_date - timedelta(days=1)
	)


def _fetch_travelling_dates(emp_ids, from_date, to_date):
	"""Dates in the period on which each employee was out of station, taken from
	approved Travelling CL requests (a request covers from_date..to_date).

	Only the Driver out-of-station allowance reads this today. A request that is
	still Pending — or was Rejected / Cancelled — is not out-of-station time: the
	allowance is paid on approval, like every other approved-leave-driven number
	in this file.

	Returns dict employee -> set[date].
	"""
	out = defaultdict(set)
	if not emp_ids:
		return out

	rows = frappe.db.sql(
		"""
		SELECT employee, from_date, to_date
		FROM `tabTravelling CL`
		WHERE employee IN %(emp_ids)s
		  AND status = 'Approved'
		  AND from_date IS NOT NULL
		  AND to_date IS NOT NULL
		  AND from_date <= %(to_date)s
		  AND to_date   >= %(from_date)s
		""",
		{"emp_ids": tuple(emp_ids), "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	for r in rows:
		d = max(getdate(r.from_date), from_date)
		end = min(getdate(r.to_date), to_date)
		while d <= end:
			out[r.employee].add(d)
			d += timedelta(days=1)

	return out


def _fetch_presentish_in_window(emp_ids, window_start, window_end):
	"""Present-ish dates for each employee within [window_start, window_end].

	Mirrors the ``presentish_dates`` composition in the main calc
	(Present + Half Day attendance + approved half-day leaves); full-day
	leaves are intentionally excluded so a holiday sandwiched in leave does
	not qualify.

	Returns dict employee -> set[date].
	"""
	out = defaultdict(set)
	if not emp_ids:
		return out

	# Present / Half Day attendance (excluding false attendance)
	rows = frappe.db.sql(
		"""
		SELECT employee, attendance_date
		FROM `tabAttendance`
		WHERE employee IN %(emp_ids)s
		  AND attendance_date BETWEEN %(start)s AND %(end)s
		  AND docstatus = 1
		  AND status IN ('Present', 'Half Day')
		  AND COALESCE(false_attendance, 0) = 0
		""",
		{"emp_ids": tuple(emp_ids), "start": window_start, "end": window_end},
		as_dict=True,
	)
	for r in rows:
		out[r.employee].add(getdate(r.attendance_date))

	# Approved half-day leaves in the window: the employee worked the other half,
	# so the day is "present-ish". UNLESS both halves were approved — then they
	# were away all day, which is a full leave and NOT present-ish.
	lrows = frappe.db.sql(
		"""
		SELECT employee, half_day_date, half_day_period
		FROM `tabOTPL Leave`
		WHERE employee IN %(emp_ids)s
		  AND status = 'Approved'
		  AND COALESCE(half_day, 0) = 1
		  AND half_day_date BETWEEN %(start)s AND %(end)s
		""",
		{"emp_ids": tuple(emp_ids), "start": window_start, "end": window_end},
		as_dict=True,
	)

	half_periods = defaultdict(lambda: defaultdict(set))
	for r in lrows:
		if r.half_day_date:
			hd = getdate(r.half_day_date)
			out[r.employee].add(hd)
			period = normalize_half_day_period(r.half_day_period)
			if period:
				half_periods[r.employee][hd].add(period)

	for employee, by_date in half_periods.items():
		for d, periods in by_date.items():
			if len(periods) >= 2:
				out[employee].discard(d)

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
		       COALESCE(half_day, 0) AS half_day, half_day_date, half_day_period,
		       COALESCE(short_leave, 0) AS short_leave
		FROM `tabOTPL Leave`
		WHERE employee IN %(emp_ids)s
		  AND status = 'Approved'
		  AND approved_from_date IS NOT NULL
		  AND approved_to_date IS NOT NULL
		  AND approved_from_date <= %(to_date)s
		  AND approved_to_date   >= %(from_date)s
		  AND (
		        -- A Half Day / Short Leave deliberately creates NO Leave
		        -- Application (it is tracked on the OTPL Leave and deducted
		        -- here), so requiring one would silently drop every such record
		        -- and Col K would stop deducting for them entirely.
		        COALESCE(half_day, 0) = 1
		        OR COALESCE(short_leave, 0) = 1
		        -- A full-day leave with no Leave Application never materialised
		        -- (creation failed); it is not counted, as before.
		        OR leave_applications IS NOT NULL
		      )
		""",
		{"emp_ids": tuple(emp_ids), "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)

	# employee -> date -> set of normalised half-day periods approved on it.
	# Two OPPOSITE halves on one date mean the employee was away the whole day.
	half_periods = defaultdict(lambda: defaultdict(set))

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
				period = normalize_half_day_period(r.half_day_period)
				if period:
					half_periods[r.employee][hd].add(period)
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

	# First Half + Second Half approved on the same date = a whole day away. It is
	# a FULL leave day (1 day of CL/AL, added back via adj_cl/adj_al), not a half
	# day (0.5 in Col K) — the half_leave_dates set would otherwise collapse the
	# two records into one date and only ever deduct half of it.
	#
	# Approving the second half now replaces the pair with a single full-day OTPL
	# Leave (otpl_leave.merge_half_day_pair), so this only fires for pairs approved
	# BEFORE that change — which it corrects with no data migration.
	for employee, by_date in half_periods.items():
		for d, periods in by_date.items():
			if len(periods) >= 2:
				out[employee]["half_leave_dates"].discard(d)
				out[employee]["full_leave_dates"].add(d)

	return out


def _fetch_lwp_leave_dates(emp_ids, from_date, to_date):
	"""Dates in the period covered by an approved Leave Without Pay application.

	A full-day leave is normally added back to payable days out of the CL / AL
	balance (Col M / N). Leave Without Pay is by definition unpaid — OTPL Leave
	itself splits an application into CL + LWP once the CL balance runs out — so
	adjusting an LWP day from CL would both pay a day that was decided as unpaid
	and consume a CL the employee never spent. These dates are therefore removed
	from the adjustment count (they stay full-day leaves everywhere else).

	Returns dict employee -> set[date].
	"""
	out = defaultdict(set)
	if not emp_ids:
		return out
	rows = frappe.db.sql(
		"""
		SELECT la.employee, la.from_date, la.to_date
		FROM `tabLeave Application` la
		INNER JOIN `tabLeave Type` lt ON lt.name = la.leave_type
		WHERE la.employee IN %(emp_ids)s
		  AND la.docstatus = 1
		  AND COALESCE(lt.is_lwp, 0) = 1
		  AND la.from_date <= %(to_date)s
		  AND la.to_date   >= %(from_date)s
		""",
		{"emp_ids": tuple(emp_ids), "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	for r in rows:
		d = max(getdate(r.from_date), getdate(from_date))
		end = min(getdate(r.to_date), getdate(to_date))
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


def _fetch_holiday_cl_credits(emp_ids, from_date, to_date):
	"""Work-on-holiday Casual Leave credited to each employee WITHIN the period.

	``_fetch_cl_balances`` reads the balance as on the period's from_date, so CL
	earned by working a holiday mid-period is not in it — the credit ledger entry
	is dated on the holiday itself, and only shows up from the next period. This
	is the CL counterpart of ``al_generated``: it is added to Col O (Balance CL)
	so the leave earned this month is visible this month.

	Reverted credits are excluded. Returns dict employee -> leaves (1.0 per full
	day worked, 0.5 per half day).
	"""
	out = {e: 0.0 for e in emp_ids}
	if not emp_ids:
		return out
	if not frappe.db.table_exists("Travelling CL Holiday Credit"):
		return out
	rows = frappe.db.sql(
		"""
		SELECT employee, SUM(leaves) AS leaves
		FROM `tabTravelling CL Holiday Credit`
		WHERE employee IN %(emp_ids)s
		  AND status != 'Reverted'
		  AND holiday_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY employee
		""",
		{"emp_ids": tuple(emp_ids), "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	for r in rows:
		out[r.employee] = flt(r.leaves)
	return out


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
# -----------------------------------------------------------------------------
# Sales-order wise allocation
# -----------------------------------------------------------------------------
# Employee Salary Details carries this many sales_order_N / gross_salary_N pairs.
MAX_ORDER_SLOTS = 10


def _fetch_order_days(emp_ids, from_date, to_date):
	"""Return {employee: {sales_order: worked_days}} for the period.

	The per-day sales order lives on Employee Checkin (`order`), which is
	stamped on the IN punch. A date is counted once per order even if the
	employee punched several times, and a date split across two orders
	contributes half a day to each so the per-employee total still equals
	the number of distinct days actually worked.
	"""
	if not emp_ids:
		return {}
	if not frappe.db.has_column("Employee Checkin", "order"):
		return {}

	rows = frappe.db.sql(
		"""
		SELECT employee, DATE(time) AS att_date, `order` AS sales_order
		FROM `tabEmployee Checkin`
		WHERE employee IN %(ids)s
		  AND DATE(time) BETWEEN %(from_date)s AND %(to_date)s
		  AND IFNULL(`order`, '') != ''
		GROUP BY employee, DATE(time), `order`
		""",
		{"ids": tuple(emp_ids), "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)

	# employee -> date -> set(orders), so a day shared by two orders splits.
	by_emp_date = defaultdict(lambda: defaultdict(set))
	for r in rows:
		by_emp_date[r.employee][r.att_date].add(r.sales_order)

	out = {}
	for emp, dates in by_emp_date.items():
		tally = defaultdict(float)
		for _dt, orders in dates.items():
			share = 1.0 / len(orders)
			for so in orders:
				tally[so] += share
		out[emp] = dict(tally)
	return out


def _fetch_cost_centers_for_orders(sales_orders):
	"""Return {sales_order: cost_center} using the Cost Center.sales_order link."""
	if not sales_orders or not frappe.db.has_column("Cost Center", "sales_order"):
		return {}
	rows = frappe.db.sql(
		"""SELECT sales_order, name FROM `tabCost Center`
		   WHERE sales_order IN %(so)s AND IFNULL(sales_order, '') != ''""",
		{"so": tuple(sales_orders)},
		as_dict=True,
	)
	return {r.sales_order: r.name for r in rows}


def _split_amount(total, ratios):
	"""Split ``total`` across ``ratios`` (a list of floats summing to 1.0),
	rounded to 2dp, with the rounding remainder pushed onto the largest
	share so the parts always add back to ``total`` exactly.

	Exactness matters: Employee Salary.validate_total rejects a row whose
	order-wise gross amounts do not sum to the row's total.
	"""
	total = flt(total, 2)
	if not ratios:
		return []
	parts = [flt(total * r, 2) for r in ratios]
	drift = flt(total - sum(parts), 2)
	if drift:
		biggest = max(range(len(parts)), key=lambda i: abs(ratios[i]))
		parts[biggest] = flt(parts[biggest] + drift, 2)
	return parts


def _build_order_allocations(rows, order_days_map, log_lines=None):
	"""Turn each payroll row into one or more sales-order allocations.

	Payable days include paid leave and holidays, which belong to no single
	order, so the whole payable figure is apportioned in the same ratio as
	the days the employee actually checked in against each order. When
	there is no checkin order data at all, the employee's default sales
	order (Employee.sales_order) takes the full amount.
	"""
	# Resolve cost centers for every order we are about to reference.
	wanted = set()
	for r in rows:
		wanted.update(order_days_map.get(r["employee"], {}).keys())
		if r.get("sales_order"):
			wanted.add(r["sales_order"])
	cc_map = _fetch_cost_centers_for_orders(wanted)

	allocations = []
	for r in rows:
		emp = r["employee"]
		payable_days = flt(r.get("payable_days"))
		due = flt(r.get("total_salary_due"))
		salary_amount = flt(r.get("salary_amount"))

		day_tally = dict(order_days_map.get(emp) or {})
		# Drop non-positive tallies defensively.
		day_tally = {so: d for so, d in day_tally.items() if flt(d) > 0}

		if not day_tally:
			default_so = r.get("sales_order")
			if not default_so:
				if log_lines is not None:
					log_lines.append(
						"{0}: no checkin sales order and no default on Employee "
						"master - salary not allocated to any order.".format(emp)
					)
				continue
			allocations.append({
				"employee": emp,
				"employee_name": r.get("employee_name"),
				"sales_order": default_so,
				"cost_center": cc_map.get(default_so),
				"worked_days": 0.0,
				"allocated_days": flt(payable_days, 2),
				"allocation_ratio": 100.0,
				"salary_amount": flt(salary_amount, 2),
				"total_salary_due": flt(due, 2),
				"slot": 1,
				"source": "Employee Default",
			})
			continue

		ordered = sorted(day_tally.items(), key=lambda kv: (-kv[1], kv[0]))
		folded = False
		if len(ordered) > MAX_ORDER_SLOTS:
			# Keep the busiest orders; the tail's days are folded into them
			# pro-rata below, so no amount is lost.
			dropped = ordered[MAX_ORDER_SLOTS:]
			ordered = ordered[:MAX_ORDER_SLOTS]
			folded = True
			if log_lines is not None:
				log_lines.append(
					"{0}: worked on {1} sales orders, only {2} slots available - "
					"{3} folded pro-rata into the largest.".format(
						emp, len(day_tally), MAX_ORDER_SLOTS,
						", ".join(so for so, _ in dropped),
					)
				)

		total_days = sum(d for _so, d in ordered)
		ratios = [d / total_days for _so, d in ordered]
		due_parts = _split_amount(due, ratios)
		salary_parts = _split_amount(salary_amount, ratios)
		day_parts = _split_amount(payable_days, ratios)

		for i, (so, worked) in enumerate(ordered):
			allocations.append({
				"employee": emp,
				"employee_name": r.get("employee_name"),
				"sales_order": so,
				"cost_center": cc_map.get(so),
				"worked_days": flt(worked, 2),
				"allocated_days": day_parts[i],
				"allocation_ratio": flt(ratios[i] * 100.0, 2),
				"salary_amount": salary_parts[i],
				"total_salary_due": due_parts[i],
				"slot": i + 1,
				"source": "Folded" if folded else "Checkin",
			})

	return allocations


def _calculate_employee(emp, from_date, to_date, days_in_period,
                        att, leaves, holiday_dates,
                        balance, tds, advance,
                        cl_balance=0.0, cl_generated=0.0,
                        payable_balance=0.0, al_eligible=False,
                        payable_days_override=None,
                        payable_days_source=None,
                        lookahead_presentish=None,
                        lookbehind_presentish=None,
                        lwp_dates=None,
                        neighbour_holidays=None,
                        travelling_dates=None):
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

	# Field staff earn AL a different way: one AL per holiday they actually WORK
	# (the qualifying / sandwich rule is not used), and the balance is forfeited
	# the moment they take leave. No OTPL Employee Leave Balance row or AL-eligible
	# Business Line is required — the row is created on submit if missing.
	field_al_enabled = (staff_type == "Field")

	# Attendance aggregates -----------------------------------------------------
	present_dates = att.get("present_dates", set())
	half_day_dates = att.get("half_day_dates", set())
	absent_dates = att.get("absent_dates", set())
	processed_dates = att.get("processed_dates", set())
	late_count = 0 if skip_late_metrics else att.get("late_count", 0)
	late_entry_count = 0 if skip_late_metrics else att.get("late_entry_count", 0)
	early_exit_count = 0 if skip_late_metrics else att.get("early_exit_count", 0)
	# Total late/early marks — drives the ESS Location count rule (3/5/5).
	late_early_total = late_entry_count + early_exit_count
	extra_late_entry_count = 0 if skip_late_metrics else att.get("extra_late_entry_count", 0)
	extra_early_exit_count = 0 if skip_late_metrics else att.get("extra_early_exit_count", 0)
	# Extra-late half-days to deduct = (extra late entries + extra early exits) / 2.
	extra_late_half_days = (extra_late_entry_count + extra_early_exit_count) / 2.0
	working_hours = flt(att.get("working_hours", 0.0))
	false_attendance_count = att.get("false_attendance_count", 0)

	# Approved leaves (per observation #7 separated) ----------------------------
	full_leave_dates = leaves.get("full_leave_dates", set())
	half_leave_dates = leaves.get("half_leave_dates", set())

	# A full-day approved leave is added back through adj_cl / adj_al, so it must
	# NOT also be counted as a worked day. Normally it cannot be — a full-day leave
	# produces "On Leave" attendance, which is in neither set — so this is a no-op.
	# It matters for a date the employee was fully away on two half-day leaves but
	# whose Attendance still reads "Half Day" (pairs approved before they began
	# merging into one full-day leave). Without this the day would be counted twice:
	# once in days_worked and again in adj_cl.
	present_dates = present_dates - full_leave_dates
	half_day_dates = half_day_dates - full_leave_dates
	short_leave_count = leaves.get("short_leave_count", 0)
	approved_leaves_count = len(full_leave_dates)  # full-day only

	# "Present-ish" set for holiday qualification per observation #5
	# (half day counts as present, both from attendance and approved half leave).
	# Dates from the first few days of the NEXT month are folded in via
	# ``lookahead_presentish`` so that a holiday at (or near) the end of the
	# period can still qualify off attendance that lands in the next month, and
	# the last few days of the PREVIOUS month via ``lookbehind_presentish`` so a
	# holiday at (or near) the start of the period can qualify off attendance
	# that lands in the previous month.
	presentish_dates = (
		present_dates
		| half_day_dates
		| half_leave_dates
		| (lookahead_presentish or set())
		| (lookbehind_presentish or set())
	)

	# ---- Qualified holidays --------------------------------------------------
	# A holiday qualifies when the employee was present on AT LEAST ONE of the 3
	# WORKING days BEFORE it AND on AT LEAST ONE of the 3 WORKING days AFTER it.
	# One present day on each side is enough — not every day.
	#
	# The window is counted in WORKING days, not calendar days, and OTHER
	# HOLIDAYS are the ONLY thing stepped over: walking outwards from the
	# holiday, a date that is itself a holiday does not consume one of the three
	# slots, so the walk always lands on three days the employee was rostered to
	# work.
	#
	# e.g. Sat 15th is a holiday and so is Sun 16th: the three working days after
	# the 15th are Mon 17th, Tue 18th and Wed 19th.
	#
	# EVERY other day spends a slot, leave included. A day of approved leave is a
	# working day the employee did not work — it is not presence, and it is not
	# skipped either. So if all three working days on one side are leave days,
	# the holiday does NOT qualify; one present day among them is enough.
	#
	# Skipping holidays also makes the old "bunch consecutive holidays into one
	# block" step unnecessary: every holiday in a run steps over the rest of the
	# run and lands on exactly the same three working days either side, so a run
	# still qualifies (or fails) as one.
	#
	# "Present" here is present-ish: Present or Half Day attendance, or an
	# approved half-day leave (the other half was worked). An Absent, a full-day
	# leave, or a day with no attendance record all spend a slot without being
	# presence — none of them actively disqualifies, they just fail to support.
	#
	# Dates either side that fall outside the payroll period come from
	# lookbehind_presentish / lookahead_presentish, and the holidays to skip out
	# there from neighbour_holidays, so a holiday at the edge of the month is
	# judged on real data.
	#
	# This one rule drives BOTH Col H (Days Worked) and Col G (AL Generated).
	holidays_to_skip = holiday_dates | (neighbour_holidays or set())

	def _working_days_around(h, step):
		"""The next ``QUALIFY_WORKING_DAYS`` working dates from ``h``, walking
		backwards (step=-1) or forwards (step=+1) over holidays.

		The walk gives up after ``QUALIFY_MARGIN_DAYS`` calendar days — the span
		for which neighbouring holiday / attendance data was fetched — and
		returns whatever it found, so it can never run off into dates it has no
		data for.
		"""
		found = []
		d = h
		for _ in range(QUALIFY_MARGIN_DAYS):
			d = d + timedelta(days=step)
			if d in holidays_to_skip:
				continue
			found.append(d)
			if len(found) == QUALIFY_WORKING_DAYS:
				break
		return found

	def _holiday_window_qualifies(h):
		before = _working_days_around(h, -1)
		after = _working_days_around(h, 1)
		return (any(d in presentish_dates for d in before)
		        and any(d in presentish_dates for d in after))

	# A holiday can only be judged once the employee's attendance has actually
	# been processed that far, so the count stops at their last processed day.
	# Without this every remaining holiday of the month would qualify by default
	# in a mid-period run: the days around it have no attendance record yet, and
	# unknown days are ignored by the window check above. Taken per employee
	# rather than company-wide, so one employee's stray future-dated attendance
	# cannot pull everyone else's cutoff forward.
	holiday_cutoff = max(processed_dates) if processed_dates else None

	qualified_holidays = 0
	qualifying_holiday_dates = set()   # used to net Col L
	for h in holiday_dates:
		if holiday_cutoff is None or h > holiday_cutoff:
			continue
		# If the employee was on approved (full-day) leave on the holiday itself,
		# the holiday is neither "earned" nor worked: it must NOT generate AL
		# (Col G) and must NOT count as a worked holiday (Col H). It is already
		# accounted for via the leave adjustment (adj_cl / adj_al), so counting
		# it here too would both over-state AL Generated and double-count the day
		# in payable_days.
		if h in full_leave_dates:
			continue
		if _holiday_window_qualifies(h):
			qualified_holidays += 1
			qualifying_holiday_dates.add(h)

	# ---- Col G: AL Generated --------------------------------------------------
	# One AL per qualifying holiday. Only counted when AL is enabled for this
	# employee/business line.
	al_generated = qualified_holidays if al_enabled else 0

	# ---- Col H: Days Worked (Worked / Holidays / Leave Adjustment) -----------
	# Half days count as a full present day here (obs #5); the 0.5-day salary
	# impact is taken out separately via the Late Deduction column (Col K), so
	# counting half days as 1 here prevents a 1.5-day net loss for the employee.
	effective_present_dates = present_dates

	non_holiday_present = sum(
		1 for d in (effective_present_dates | half_day_dates | half_leave_dates)
		if d not in holiday_dates
	)

	# Driver rule: a holiday the driver actually WORKED gives a flat ₹500 OT
	# (Col S) instead of pay for the day.
	#
	# Gated on being PRESENT, not on the holiday qualifying. The qualifying
	# (sandwich) rule decides whether an UNWORKED holiday is earned; a day the
	# driver actually drove is earned by the work itself. This mirrors the
	# non-Driver side, where working a holiday credits CL with no qualifying
	# test (see utils/travelling_cl_credit.py).
	driver_worked_qh = (present_dates & holiday_dates) if is_driver else set()

	# Non-Driver Work-on-Holiday: a holiday the employee is Present (1) or Half Day
	# (0.5) on. Reported for information, and it drives the CL credit granted by the
	# work-on-holiday job, but it is NOT deducted from Days Worked.
	#
	# A monthly-salaried employee's holidays already sit inside the month's pay, so
	# deducting a worked holiday removed pay they would have received by staying at
	# home — working the day left them worse off by one day's salary per holiday
	# worked. The CL credit is a comp-off ON TOP of normal pay, not a substitute
	# for it.
	worked_holiday_full = (present_dates & holiday_dates) if not is_driver else set()
	worked_holiday_half = (half_day_dates & holiday_dates) if not is_driver else set()
	work_on_holiday = len(worked_holiday_full) + 0.5 * len(worked_holiday_half)

	# Drivers are still netted: a qualifying holiday the driver actually drove is
	# paid as a flat OT in Col S instead of as a day here.
	# Field staff: AL is earned by WORKING the holiday, not by the qualifying rule,
	# so Col G is the work-on-holiday count (1 per full day, 0.5 per half day).
	if field_al_enabled:
		al_generated = work_on_holiday

	effective_qualified_holidays = (
		qualified_holidays
		- len(driver_worked_qh & qualifying_holiday_dates)
	)

	days_worked = non_holiday_present + effective_qualified_holidays
	if is_worker_site:
		dw_explain = "Worker@Site: non-holiday present + qualifying holidays (OR rule)"
	else:
		dw_explain = "Non-(Worker@Site): non-holiday present + qualifying holidays (OR rule)"

	# Each false attendance still deducts 2 days from days worked.
	days_worked -= 2 * false_attendance_count
	days_worked = max(days_worked, 0)

	# ---- Col K: Late deduction days -----------------------------------------
	# K = approved-half-day-leaves / 2   (half-day LEAVE part; the only Half Day now)
	#   + late-mark deduction            (ESS Location count rule applied to the
	#                                      total late_entry + early_exit marks)
	#   + extra-late half-days           ((extra_late + extra_early) / 2, direct)
	approved_half_days = len(half_leave_dates)

	# Late-mark deduction from the ESS Location "Leave Deduction Rules" (defaults
	# 3 / 5 / 5) applied to the total late/early marks:
	#   total >= late_count_for_full_day  -> 1 day, plus 0.5 for each mark beyond
	#                                        treat_late_as_half_day_after
	#   total >= late_count_for_half_day  -> 0.5 day
	#   else                              -> 0
	late_count_for_half_day = cint(emp.get("late_count_for_half_day")) or 3
	late_count_for_full_day = cint(emp.get("late_count_for_full_day")) or 5
	treat_late_as_half_day_after = cint(emp.get("treat_late_as_half_day_after")) or 5
	late_mark_deduction = 0.0
	if late_early_total >= late_count_for_full_day:
		late_mark_deduction = 1.0
		if late_early_total > treat_late_as_half_day_after:
			late_mark_deduction += (late_early_total - treat_late_as_half_day_after) * 0.5
	elif late_early_total >= late_count_for_half_day:
		late_mark_deduction = 0.5

	late_deduction = approved_half_days / 2.0 + late_mark_deduction + extra_late_half_days

	# ---- Col L: Absent w/o info (observation #4) -----------------------------
	# Count of Attendance.status='Absent' (excluding false attendance), MINUS any
	# qualifying holiday (OR rule, Col H) that falls on an Absent-marked day. A
	# qualifying holiday is a paid/earned holiday (counted in Days Worked); if a
	# holiday date was also marked Absent, that Absent must not be double-counted
	# against the employee here.
	absent_on_qualifying_holiday = len(absent_dates & qualifying_holiday_dates)
	absent_count = len(absent_dates) - absent_on_qualifying_holiday

	# ---- Col M / N: Adjusted from CL / AL ------------------------------------
	# CL comes from the standard "Casual Leave" allocation (passed in by the
	# caller). AL comes from OTPL Employee Leave Balance.
	#
	# AL Bal is only available when AL is enabled (Worker@Site + opening row +
	# AL-eligible business line); otherwise it's treated as 0 for the M
	# formula.
	#
	# N = If(AL Bal >= approved, approved, AL Bal)
	# M = If(approved > AL Bal,
	#        If((approved - AL Bal) >= 2,
	#           If(CL Bal >= 2, 2, CL Bal),
	#           If(CL Bal > 0, approved - AL Bal, 0)),
	#        0)
	#
	# NOTE: Absent days (Col L) are NOT subtracted here. Approved leaves and
	# Absent attendance are independent (approved leave -> "On Leave"; absent ->
	# "Absent"); netting the two counts would wrongly forfeit a paid leave day
	# the employee has CL/AL balance for whenever they also have absences in the
	# same month. Col L is still deducted on its own in the payable_days formula.
	cl_balance = flt(cl_balance)
	cl_generated = flt(cl_generated)

	# Field staff are AL-only: their comp-off for working a holiday is AL, and they
	# neither draw on nor report Casual Leave. Zeroed here so Col M and Col O stay
	# empty even for a Field employee who still has a standing CL allocation.
	if field_al_enabled:
		cl_balance = 0.0
		cl_generated = 0.0
	al_balance = flt(balance.get("al_balance") or balance.get("year_opening_al") or 0)

	# CL available to absorb THIS period's leave = the opening balance plus any
	# work-on-holiday CL earned during the period. The opening balance alone is
	# taken as on from_date, so CL earned mid-month (its ledger entry is dated on
	# the holiday) would otherwise sit unusable until the next payroll.
	cl_available = cl_balance + cl_generated

	# Field staff draw on opening AL PLUS the AL earned this period — a comp-off
	# earned by working a Sunday must be usable for leave taken in the same month.
	if al_enabled:
		effective_al = al_balance
	elif field_al_enabled:
		effective_al = al_balance + al_generated
	else:
		effective_al = 0
	# Leave Without Pay is never adjusted from CL / AL — it was decided as unpaid.
	adjusted_leaves = len(full_leave_dates - (lwp_dates or set()))

	# Field staff only: their AL is a comp-off for holidays worked, so it pays ANY
	# approved full-day leave — Leave Without Pay included. (Everywhere else LWP is
	# excluded from the adjustment: it was decided as unpaid because no balance was
	# left. A Field employee who banked days by working Sundays does have a balance,
	# so those LWP days are paid out of it.)
	al_adjustable_leaves = len(full_leave_dates) if field_al_enabled else adjusted_leaves

	# Col N: Adjusted from AL
	if al_enabled or field_al_enabled:
		adj_al = (al_adjustable_leaves if effective_al >= al_adjustable_leaves
		          else effective_al)
		if field_al_enabled:
			# Use-it-or-lose-it: taking ANY approved leave in the period — Leave
			# Without Pay included — wipes the whole Field AL balance, not just the
			# days consumed.
			applied_for_leave = bool(full_leave_dates or half_leave_dates)
			closing_al = 0.0 if applied_for_leave else (effective_al - adj_al)
		else:
			closing_al = al_balance + al_generated - adj_al
	else:
		adj_al = 0
		closing_al = 0

	# Col M: Adjusted from CL — whatever AL did not already cover.
	# For Field staff AL may have been spent on LWP days, so the CL side is judged
	# against the AL actually consumed, not the whole AL balance.
	al_used_against_leave = adj_al if field_al_enabled else effective_al
	if field_al_enabled:
		# AL-only: no CL adjustment for Field staff, whatever leave remains.
		adj_cl = 0
	elif adjusted_leaves > al_used_against_leave:
		uncovered = adjusted_leaves - al_used_against_leave
		if uncovered >= 2:
			adj_cl = 2 if cl_available >= 2 else max(cl_available, 0)
		else:
			adj_cl = uncovered if cl_available > 0 else 0
	else:
		adj_cl = 0

	# ---- Col O / P: Balances --------------------------------------------------
	# CL earned by working a holiday during THIS period is added back here, the
	# same way Col P adds al_generated. Without it the credit only surfaces from
	# the next period, since the opening balance is taken as on from_date and the
	# credit ledger entry is dated on the holiday.
	balance_cl = cl_available - adj_cl

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
	# OT hours = [working_hours + (QUALIFYING holidays * 8)] - (days_worked * 8)
	# OT amount = OT hours * (gross / (days_in_month * 8))
	# Only QUALIFYING holidays (OR rule, the same ones counted in Col H Days
	# Worked) contribute their 8 hours — not every holiday-list date. A holiday
	# the employee did not earn (no present-ish neighbour) is not part of days
	# worked, so it must not inflate OT hours either.
	ot_hra_petrol = 0.0
	ot_hours = 0.0
	driver_out_of_station_days = 0
	if is_driver:
		# Driver OT (hardcoded rupee rule) — REPLACES the hours-based OT:
		#   * Worked holiday  -> flat ₹700 (local or out of station)
		#   * Working day     -> checkout band (local or out of station),
		#                        plus ₹200 if the day was spent out of station
		checkout_by_date = att.get("checkout_by_date", {})
		travel_dates = travelling_dates or set()
		driver_ot = 0.0
		for d in driver_worked_qh:
			driver_ot += DRIVER_HOLIDAY_OT
		for d in present_dates:
			if d in holiday_dates:
				continue   # worked holiday handled above
			driver_ot += _driver_checkout_ot(checkout_by_date.get(d), d)
			# Out of station: a flat allowance on top of whatever the punch-out
			# earned. Holidays are excluded — they took the flat holiday rate
			# above, which already covers local and out-of-station alike.
			if d in travel_dates:
				driver_ot += DRIVER_OUT_OF_STATION_OT
				driver_out_of_station_days += 1
		ot_hra_petrol = driver_ot
	elif ot_eligible and gross and days_in_month:
		ot_hours = (
			working_hours
			+ (qualified_holidays * STD_HOURS_PER_DAY)
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
		# Col H component breakdown (surfaced in the calculation trace UI)
		"non_holiday_present": non_holiday_present,
		"qualified_holidays": qualified_holidays,
		"work_on_holiday": flt(work_on_holiday, 2),
		# Driver out-of-station working days (surfaced in the calculation trace UI)
		"driver_out_of_station_days": driver_out_of_station_days,
		"false_attendance_count": false_attendance_count,
		"late_count": late_count,
		"late_entry_count": late_entry_count,
		"early_exit_count": early_exit_count,
		"late_early_total": late_early_total,
		"late_mark_deduction": flt(late_mark_deduction, 2),
		"approved_half_days": approved_half_days,
		"extra_late_entry_count": extra_late_entry_count,
		"extra_early_exit_count": extra_early_exit_count,
		"extra_late_half_days": flt(extra_late_half_days, 2),
		"short_leaves_count": short_leave_count,
		"late_deduction_days": flt(late_deduction, 2),
		"absent_no_info_days": absent_count,
		"absent_on_qualifying_holiday": absent_on_qualifying_holiday,
		"adjusted_from_cl": flt(adj_cl, 2),
		"adjusted_from_al": flt(adj_al, 2),
		"balance_cl": flt(balance_cl, 2),
		# Work-on-holiday CL earned within the period (surfaced in the trace UI)
		"cl_generated": flt(cl_generated, 2),
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
			# Field staff earn AL without being seeded in this table, so create the
			# row on first submit — otherwise their closing AL could never become
			# next period's opening.
			if r.staff_type != "Field":
				continue
			bal = frappe.get_doc({
				"doctype": "OTPL Employee Leave Balance",
				"employee": r.employee,
				"employee_name": r.employee_name,
				"al_balance": 0,
			})
			bal.flags.ignore_permissions = True
			bal.insert(ignore_permissions=True)
			bal_name = bal.name
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

	# Gross salary override (same rule as calculate_payroll): latest Employee
	# Gross Salary record with date <= from_date, else the Employee field.
	gross_override_map = _fetch_latest_gross_salary(ids_for_fetch, from_date)
	_apply_gross_override(emps_for_fetch, gross_override_map)

	att_map = _fetch_attendance_aggregates(ids_for_fetch, from_date, to_date)
	lookahead_map = _fetch_lookahead_presentish(ids_for_fetch, to_date)
	lookbehind_map = _fetch_lookbehind_presentish(ids_for_fetch, from_date)
	leave_map = _fetch_approved_leaves(ids_for_fetch, from_date, to_date)
	travelling_map = _fetch_travelling_dates(ids_for_fetch, from_date, to_date)
	holidays_by_emp = _fetch_holidays_per_employee(emps_for_fetch, from_date, to_date)
	holiday_margin_by_emp = _fetch_holidays_per_employee(
		emps_for_fetch,
		from_date - timedelta(days=QUALIFY_MARGIN_DAYS),
		to_date + timedelta(days=QUALIFY_MARGIN_DAYS))
	balance_map = _fetch_leave_balances(ids_for_fetch)
	cl_balance_map = _fetch_cl_balances(ids_for_fetch, from_date)
	cl_generated_map = _fetch_holiday_cl_credits(ids_for_fetch, from_date, to_date)
	lwp_map = _fetch_lwp_leave_dates(ids_for_fetch, from_date, to_date)
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
	cl_gen = cl_generated_map.get(employee, 0.0)
	lwp = lwp_map.get(employee, set())
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
			lookahead_presentish=lookahead_map.get(parent_id, set()),
			lookbehind_presentish=lookbehind_map.get(parent_id, set()),
			leaves=leave_map.get(parent_id, {"full_leave_dates": set(), "half_leave_dates": set(), "short_leave_count": 0}),
			holiday_dates=holidays_by_emp.get(parent_id, set()),
			balance=balance_map.get(parent_id, {}),
			cl_balance=cl_balance_map.get(parent_id, 0.0),
			cl_generated=cl_generated_map.get(parent_id, 0.0),
			lwp_dates=lwp_map.get(parent_id, set()),
			neighbour_holidays=holiday_margin_by_emp.get(parent_id, set()),
			travelling_dates=travelling_map.get(parent_id, set()),
			tds=tds_map.get(parent_id, 0.0),
			advance=advance_map.get(parent_id, {"full": 0.0, "part": 0.0}),
			payable_balance=payable_balance_map.get(parent_id, 0.0),
			al_eligible=(parent_id in al_eligible_emps and parent_emp.get("business_line") in al_eligible_bls),
		)
		payable_days_override = parent_row["payable_days"]

	row = _calculate_employee(
		emp, from_date=from_date, to_date=to_date,
		days_in_period=days_in_period, att=att, leaves=leaves,
		lookahead_presentish=lookahead_map.get(employee, set()),
		lookbehind_presentish=lookbehind_map.get(employee, set()),
		holiday_dates=holiday_dates, balance=balance, tds=tds,
		advance=advance, cl_balance=cl_bal, cl_generated=cl_gen, lwp_dates=lwp,
		neighbour_holidays=holiday_margin_by_emp.get(employee, set()),
		travelling_dates=travelling_map.get(employee, set()),
		payable_balance=payable_balance,
		al_eligible=al_eligible,
		payable_days_override=payable_days_override,
		payable_days_source=parent_id,
	)

	# --- Pretty-print helpers -------------------------------------------------
	def _f(v):
		return "{0:.2f}".format(flt(v))

	# Whatever Col H subtracted beyond the additive terms (Drivers only now).
	_h_resid = (row["non_holiday_present"] + row["qualified_holidays"]
	            - 2 * flt(att.get("false_attendance_count", 0)) - row["days_worked"])

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

	# Raw (pre-dedup) non-holiday component counts, for the Col H breakdown.
	half_leave_dates = leaves.get("half_leave_dates", set())
	full_leave_dates = leaves.get("full_leave_dates", set())
	nh_present = len([d for d in present_dates if d not in holiday_dates])
	nh_half = len([d for d in half_day_dates if d not in holiday_dates])
	nh_half_leave = len([d for d in half_leave_dates if d not in holiday_dates])

	cl_balance = flt(cl_bal)
	al_balance = flt(balance.get("al_balance") or balance.get("year_opening_al") or 0)
	full_adv = flt(advance.get("full", 0.0))
	part_adv = flt(advance.get("part", 0.0))

	is_field_al = (staff_type == "Field")
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
				("Gross (Rate of Wages)",
				 "{0}  —  {1}".format(
					_f(emp.get("gross_salary")),
					"from Employee Gross Salary dated {0}".format(gross_override_map[employee]["date"].strftime("%d-%b-%Y"))
					if gross_override_map.get(employee)
					else "from Employee master (no Employee Gross Salary on/before {0})".format(from_date.strftime("%d-%b-%Y")))),
				("Basic Salary",
				 "{0}  —  {1}".format(
					_f(emp.get("basic_salary")),
					"half of the Employee Gross Salary amount"
					if gross_override_map.get(employee)
					else "from Employee master")),
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
				("AL Calculation",
				 "ENABLED (Field staff rule: AL per holiday worked, forfeited on any leave)"
				 if is_field_al else
				 ("ENABLED" if al_eligible else "DISABLED — " + ", ".join(al_reason))),
			],
		},
		{
			"section": "Attendance",
			"items": [
				("Attendance Processed (excl. false)", str(len(processed_dates))),
				("Present days", str(len(present_dates))),
				("Work on Holiday (CL credited, pay unaffected)",
				 "{0}  —  {1}".format(
				     _f(row.get("work_on_holiday", 0)),
				     ("holiday(s) worked (Present=1 / Half Day=0.5). NOT deducted from Days Worked — "
				      "the day is paid as part of the month; the Casual Leave credit is a comp-off on top"
				      if staff_type != "Field" else
				      "holiday(s) worked (Present=1 / Half Day=0.5). Field staff earn NO work-on-holiday "
				      "Casual Leave; the day is paid as part of the month")
				     if not is_driver else "N/A (Driver — flat OT instead)")),
				("Out of station (approved Travelling CL)",
				 "{0}  —  {1}".format(
				     row.get("driver_out_of_station_days", 0),
				     "working day(s) spent out of station; each adds a flat ₹{0:.0f} on top of that day's "
				     "checkout band (worked holidays excluded — they take the flat holiday rate)"
				     .format(DRIVER_OUT_OF_STATION_OT)
				     if is_driver else "N/A (Driver rule only)")),
				("Half Days (status = Half Day) — leave half-days only", str(len(half_day_dates))),
				("Absent days", str(len(absent_dates))),
				("Late Entry marks", str(row.get("late_entry_count", 0))),
				("Early Exit marks", str(row.get("early_exit_count", 0))),
				("Late + Early total (for the count rule)", str(row.get("late_early_total", 0))),
				("Extra Late Entry marks", str(row.get("extra_late_entry_count", 0))),
				("Extra Early Exit marks", str(row.get("extra_early_exit_count", 0))),
				("Extra Late + Extra Early total", "{0}  →  ÷2 = {1} day(s)".format(
					row.get("extra_late_entry_count", 0) + row.get("extra_early_exit_count", 0),
					_f(row.get("extra_late_half_days", 0)))),
				("Total working hours (Attendance.working_hours)", "{0:.2f}".format(working_hours)),
				("Present-ish in next month (first ≤3 days, for end-of-period holidays)",
				 str(len(lookahead_map.get(employee, set())))),
				("Present-ish in previous month (last ≤3 days, for start-of-period holidays)",
				 str(len(lookbehind_map.get(employee, set())))),
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
				 "{0}  —  {1}".format(
				     row["al_generated"],
				     "Field staff: one AL per holiday WORKED (Present=1 / Half Day=0.5); "
				     "the qualifying/sandwich rule is not used"
				     if is_field_al else
				     ("one per qualifying holiday (same rule as Col H)"
				      if al_eligible else "0 (AL disabled)"))),
				("(H) Days Worked",
				 "{dw} = non-holiday present-ish {nhp} + qualifying holidays {qh}{drv} − 2×{fc} false attendance"
				 .format(dw=_f(row["days_worked"]), nhp=row["non_holiday_present"],
				         qh=row["qualified_holidays"],
				         drv=(" − driver holidays paid as flat OT {0}".format(_f(_h_resid))
				              if _h_resid else ""),
				         fc=false_count)),
				("    ↳ non-holiday present-ish ({0})".format(row["non_holiday_present"]),
				 "present {p} + half-day attendance {h} + approved half-day leave {hl}, de-duplicated by date = {nhp}"
				 "  (a Half Day counts as a FULL day here; its 0.5-day impact is taken separately in Col K)"
				 .format(p=nh_present, h=nh_half, hl=nh_half_leave, nhp=row["non_holiday_present"])),
				("    ↳ qualifying holidays ({0})".format(row["qualified_holidays"]),
				 "{qh} of {th} holiday(s) qualify — the employee must be present on AT LEAST ONE of the "
				 "{n} WORKING days BEFORE the holiday AND on AT LEAST ONE of the {n} WORKING days AFTER it "
				 "(Present / Half Day, or an approved half-day leave; days in the adjacent month are "
				 "included). The window counts WORKING days: other HOLIDAYS are SKIPPED OVER and do not use "
				 "up a slot, so the walk lands on the next {n} days the employee was rostered to work. "
				 "Holidays are the ONLY thing skipped — a leave day, an Absent day, or a day with no "
				 "attendance marked all USE UP a slot without counting as presence, so {n} straight leave "
				 "days on one side disqualify the holiday. "
				 "This employee's attendance is processed up to {cut}, so holidays after that date are not "
				 "counted at all."
				 .format(qh=row["qualified_holidays"], th=len(holiday_dates),
				         n=QUALIFY_WORKING_DAYS,
				         cut=(max(processed_dates).strftime("%d-%b-%Y")
				              if processed_dates else "(nothing processed)"))),
				("(I) Late + Early marks (drives the count rule)",
				 "{tot} = Late Entry {le} + Early Exit {ee}  →  rule (half≥{h}, full≥{f}, +0.5 beyond {t}) = {lmv} day(s)"
				 .format(tot=row.get("late_early_total", 0),
				         le=row.get("late_entry_count", 0), ee=row.get("early_exit_count", 0),
				         h=cint(emp.get("late_count_for_half_day")) or 3,
				         f=cint(emp.get("late_count_for_full_day")) or 5,
				         t=cint(emp.get("treat_late_as_half_day_after")) or 5,
				         lmv=_f(row.get("late_mark_deduction", 0)))),
				("(J) Extra Late + Extra Early marks",
				 "{tot} = Extra Late Entry {el} + Extra Early Exit {ee}  →  ÷2 = {xh} day(s)"
				 .format(el=row.get("extra_late_entry_count", 0),
				         ee=row.get("extra_early_exit_count", 0),
				         tot=row.get("extra_late_entry_count", 0) + row.get("extra_early_exit_count", 0),
				         xh=_f(row.get("extra_late_half_days", 0)))),
				("(K) Total days deducted",
				 "{total} day(s) = half-day leaves ({ah}×0.5={ahv}) + late/early rule ({lmv}) + extra late/early ({xh})"
				 .format(total=_f(row["late_deduction_days"]),
				         ah=approved_half, ahv=_f(approved_half * 0.5),
				         lmv=_f(row.get("late_mark_deduction", 0)),
				         xh=_f(row.get("extra_late_half_days", 0)))),
				("(L) Absent w/o info",
				 "{0}  —  Absent (excl. false) − qualifying holidays on absent days ({1})".format(
				     row["absent_no_info_days"], row.get("absent_on_qualifying_holiday", 0))),
				("(M) Adjusted from CL",
				 "0.00  —  Field staff are AL-only: no Casual Leave is drawn or reported"
				 if is_field_al else
				 ("{0}  —  adjustable={1} (approved full-day {2} − Leave Without Pay {3}), AL Bal={4}, "
				  "CL available={5} (opening {6} + earned this period {7}); CL covers up to 2 of "
				  "(adjustable−AL Bal). Absent (Col L) is NOT netted here."
				  .format(_f(row["adjusted_from_cl"]), approved_full - len(lwp & full_leave_dates),
				          approved_full, len(lwp & full_leave_dates),
				          _f(al_balance if al_eligible else 0),
				          _f(cl_balance + row.get("cl_generated", 0)), _f(cl_balance),
				          _f(row.get("cl_generated", 0))))),
				("(N) Adjusted from AL",
				 "{0}  —  {1}".format(
				     _f(row["adjusted_from_al"]),
				     "min(AL available {0} = opening {1} + earned {2}, leave days {3} incl. LWP)".format(
				         _f(flt(al_balance) + flt(row["al_generated"])), _f(al_balance),
				         row["al_generated"], approved_full)
				     if is_field_al else
				     ("min(AL Bal {0}, approved {1})".format(_f(al_balance), approved_full)
				      if al_eligible else "0 (AL disabled)"))),
				("(O) Balance CL",
				 "0.00  —  Field staff are AL-only: no Casual Leave balance is carried"
				 if is_field_al else
				 "{0} = opening {1} + work-on-holiday CL earned this period {2} − adjusted {3}"
				 .format(_f(row["balance_cl"]), _f(cl_balance),
				         _f(row.get("cl_generated", 0)), _f(row["adjusted_from_cl"]))),
				("(P) Closing AL",
				 "{0}  —  {1}".format(
				     _f(row["closing_al"]),
				     ("0.00 — Field staff forfeit the WHOLE AL balance in any period they take "
				      "approved leave (use-it-or-lose-it); opening {0} + earned {1} was available"
				      .format(_f(al_balance), row["al_generated"])
				      if (leaves.get("full_leave_dates") or leaves.get("half_leave_dates"))
				      else "{0} + {1} − {2} (no leave taken, balance carries forward)".format(
				          _f(al_balance), row["al_generated"], _f(row["adjusted_from_al"])))
				     if is_field_al else
				     ("{0} + {1} − {2}".format(_f(al_balance), row["al_generated"], _f(row["adjusted_from_al"]))
				      if al_eligible else "0 (AL disabled)"))),
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
				                      ("Driver rule (₹): holidays actually worked × ₹{hol:.0f} "
				                       "+ checkout bands (₹{hr:.0f} per hour ENTERED after 19:30, so ≤19:30 ₹0 / "
				                       "≤20:30 ₹100 / ≤21:30 ₹200 / ≤22:30 ₹300 / ≤23:30 ₹400; after 23:30 "
				                       "flat ₹{flat:.0f} with no bands on top) — all of that local or out of "
				                       "station alike — + ₹{oos:.0f} × {oosd} out-of-station working day(s), "
				                       "stacked on top of the band."
				                       .format(hol=DRIVER_HOLIDAY_OT, hr=DRIVER_OT_PER_HOUR,
				                               flat=DRIVER_OT_FLAT, oos=DRIVER_OUT_OF_STATION_OT,
				                               oosd=row.get("driver_out_of_station_days", 0))
				                       if is_driver else
				                       "OT hours = [working_hours({0:.2f}) + qualifying-holidays({1}) × 8] − (H({2}) × 8) ; amount = OT × Gross/({3}×8)"
				                       .format(working_hours, row["qualified_holidays"], _f(row["days_worked"]), days_in_month))
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


# -----------------------------------------------------------------------------
# Hand-off to accounting (Employee Salary -> Journal Entries)
# -----------------------------------------------------------------------------
def _sync_order_allocations(doc):
	"""Refresh the order allocation table so it always matches the rows.

	Kept in validate (rather than only at Calculate time) because the user
	may hand-edit salary figures afterwards; the JEs must follow whatever
	the sheet actually says.
	"""
	if not doc.get("employees"):
		doc.set("order_allocations", [])
		return

	emp_ids = [r.employee for r in doc.employees if r.employee]
	order_days_map = _fetch_order_days(emp_ids, getdate(doc.from_date), getdate(doc.to_date))
	rows = [
		{
			"employee": r.employee,
			"employee_name": r.employee_name,
			"sales_order": r.sales_order,
			"payable_days": r.payable_days,
			"salary_amount": r.salary_amount,
			"total_salary_due": r.total_salary_due,
		}
		for r in doc.employees
	]
	allocations = _build_order_allocations(rows, order_days_map)

	doc.set("order_allocations", [])
	for a in allocations:
		doc.append("order_allocations", a)


def _resolve_salary_due_base_data(business_vertical):
	"""Find the Employee Salary Base Data row that books salary expense.

	A vertical has several "Salary Due" base rows (earnest money, inter-company
	transfers, ...). The one that posts wages is the one crediting that
	vertical's Payroll Payable from a "Salary and Wages" expense head, which is
	also the pair Salary Payable Request later reads back off the ledger.

	Returns (base_data_name, error_message); exactly one is set.
	"""
	payroll_payable = frappe.db.get_value("Business Line", business_vertical, "payroll_payable")
	if not payroll_payable:
		return None, _("Business Line {0} has no Payroll Payable account set.").format(business_vertical)

	name = frappe.db.get_value(
		"Employee Salary Base Data",
		{
			"business_vertical": business_vertical,
			"purpose": "Salary Due",
			"cr_ledger": payroll_payable,
			"dr_ledger": ("like", "Salary and Wages%"),
		},
		"name",
	)
	if not name:
		return None, _(
			"No 'Salary Due' Employee Salary Base Data for {0} crediting {1} "
			"from a 'Salary and Wages' account."
		).format(business_vertical, payroll_payable)
	return name, None


def _preflight_cost_centers(by_vertical, alloc_by_emp, default_so):
	"""Every sales order about to be booked must resolve to a Cost Center.

	The cost centre is fetched from the sales order, via Cost Center.sales_order
	(Sales Order itself carries no cost centre field). When an order has none,
	the journal entry would silently fall back to the employee's location or,
	for Site and Lucknow staff, to the company default - booking site wages to a
	Noida unit with no warning. Blocking here forces the missing Cost Centers to
	be created instead of quietly mis-costing the payroll.
	"""
	wanted = set()
	for rows in by_vertical.values():
		for row in rows:
			parts = alloc_by_emp.get(row.employee)
			if parts:
				wanted.update(so for so, _amt, _d, _cc in parts)
			elif row.sales_order:
				wanted.add(row.sales_order)
	# The settings-level catch-all is deliberately exempt: employees who worked
	# no order at all are office overhead, and their wages belong on their
	# location's cost center, not on whichever project that default points at.
	wanted.discard(default_so)
	if not wanted:
		return

	covered = {
		r.sales_order for r in frappe.db.sql(
			"""SELECT DISTINCT sales_order FROM `tabCost Center`
			   WHERE sales_order IN %(so)s AND IFNULL(sales_order, '') != ''""",
			{"so": tuple(wanted)}, as_dict=True,
		)
	}
	missing = sorted(wanted - covered)
	if missing:
		frappe.throw(
			_("Cost Center not available for the sales order:")
			+ "<br><br>" + "<br>".join("\u2022 " + so for so in missing),
			title=_("Cost Center Not Available"),
		)


def _preflight_verticals(verticals):
	"""Resolve every vertical's base data up front.

	Booking is all-or-nothing: discovering a misconfigured vertical halfway
	through would leave some verticals posted and the rest not, on a payroll
	already marked as booked. Collect every problem and report them together so
	the whole configuration can be fixed in one pass.
	"""
	resolved = {}
	problems = []
	for vertical in verticals:
		name, error = _resolve_salary_due_base_data(vertical)
		if error:
			problems.append(error)
		else:
			resolved[vertical] = name
	if problems:
		frappe.throw(
			_("Cannot book this payroll until these are configured:")
			+ "<br><br>" + "<br>".join("\u2022 " + p for p in problems),
			title=_("Salary Accounts Not Configured"),
		)
	return resolved


@frappe.whitelist()
def create_salary_entries(payroll):
	"""Book a submitted payroll straight into the ledger.

	One Journal Entry per business vertical carries the whole month: salary
	expense debited per cost center (so the sales-order wise split lands on the
	right order), the payable credited per employee, and the PF/ESIC employee
	and employer legs on the same voucher. Booking employee-by-employee produced
	hundreds of near-identical vouchers for a single payroll run; this keeps one
	reviewable document per vertical.

	Salary Payable Request then reads the balance back off the ledger exactly as
	before - it matches on party, posting date, the payroll payable account and
	purpose 'Salary Due', all of which these vouchers still carry.

	Called automatically from on_submit; also exposed as a button so a run that
	failed on configuration can be retried once the configuration is fixed.
	"""
	doc = frappe.get_doc("OTPL Payroll", payroll) if isinstance(payroll, str) else payroll
	if doc.docstatus != 1:
		frappe.throw(_("Submit the payroll before creating salary entries."))
	if doc.get("salary_entries_created"):
		frappe.throw(
			_("Salary entries already created for this payroll: {0}").format(
				doc.get("employee_salary_entries") or "")
		)

	posting_date = getdate(doc.to_date)
	settings = frappe.get_doc("OTPL Accounting Settings", "OTPL Accounting Settings")
	default_so = settings.get("default_sales_order")

	# employee -> [(sales_order, amount, worked_days, cost_center), ...]
	alloc_by_emp = defaultdict(list)
	for a in doc.get("order_allocations") or []:
		if a.sales_order and flt(a.total_salary_due):
			alloc_by_emp[a.employee].append(
				(a.sales_order, flt(a.total_salary_due, 2), flt(a.worked_days), a.cost_center)
			)

	by_vertical = defaultdict(list)
	skipped = []
	for row in doc.employees:
		due = flt(row.total_salary_due, 2)
		if due <= 0:
			# Nothing to book: these employees owe the company for the period.
			skipped.append("{0} (due {1})".format(row.employee, due))
			continue
		if not row.business_line:
			skipped.append("{0} (no business line)".format(row.employee))
			continue
		by_vertical[row.business_line].append(row)

	# Fail before creating anything if any sales order lacks a Cost Center.
	_preflight_cost_centers(by_vertical, alloc_by_emp, default_so)

	# Cost follows the sales order's own vertical, not the employee's.
	order_vertical = _fetch_order_verticals(
		{so for parts in alloc_by_emp.values() for so, _a, _d, _c in parts}
		| {row.sales_order for rows in by_vertical.values() for row in rows if row.sales_order}
	)
	# Every vertical that will bear cost needs its own accounts, not just the
	# ones the employees sit under.
	bearing = set(by_vertical) | {
		v for so, v in order_vertical.items() if so != default_so
	}
	base_data_by_vertical = _preflight_verticals(sorted(bearing))

	fallback_used = []
	created = _build_journal_entries(
		doc, by_vertical, alloc_by_emp, settings, default_so, posting_date,
		fallback_used, order_vertical, base_data_by_vertical,
	)

	if not created:
		frappe.throw(_("Nothing to book: no employee had a positive Total Salary Due."))

	# TDS is deliberately NOT posted here. It stays a manual step - the
	# Process TDS Entry button, or a voucher keyed by hand - so the payroll
	# never books a deduction on the user's behalf. The payment request keeps
	# up regardless: it re-reads the ledger on every save and at submit.

	# Payment side: draft only. Releasing money needs the approval role, the
	# bucket and the PE naming series, which are human decisions by design.
	payable_requests, payable_error = _create_salary_payable_requests(
		doc, sorted(by_vertical.keys()), posting_date)

	log = [_("Journal Entries: {0}").format(", ".join(created))]
	if payable_requests:
		log.append(_("Salary Payable Request (draft): {0}").format(", ".join(payable_requests)))
	if payable_error:
		log.append(_("Salary Payable Request not created: {0}").format(payable_error))
	if skipped:
		log.append(_("Skipped: {0}").format(", ".join(skipped)))
	if fallback_used:
		log.append(_("Booked to the default sales order (no attendance order, no "
		             "Employee master order): {0}").format(", ".join(fallback_used)))

	doc.db_set("salary_entries_created", 1)
	doc.db_set("employee_salary_entries", "\n".join(log))

	return {
		"created": created,
		"payable_requests": payable_requests,
		"payable_error": payable_error,
		"skipped": skipped,
		"fallback_used": fallback_used,
	}



# -----------------------------------------------------------------------------
# TDS entries (posted from the OTPL TDS register, separate from the salary JE)
# -----------------------------------------------------------------------------
TDS_PURPOSE = "TDS Due"


@frappe.whitelist()
def create_tds_entries(payroll):
	"""Post the month's TDS for every employee that has an OTPL TDS record.

	The month comes from the payroll's To Date; the amount and the posting date
	(the last day of that month) come from that month's row on the employee's
	OTPL TDS document. Employees with no TDS record - or no amount for the month
	- are simply passed over, and a month already posted is left alone, so the
	button can be pressed again safely.
	"""
	doc = frappe.get_doc("OTPL Payroll", payroll) if isinstance(payroll, str) else payroll
	if doc.docstatus != 1:
		frappe.throw(_("Submit the payroll before posting TDS entries."))

	result = _post_tds_entries(doc)

	# Re-read the ledger into the payment requests: TDS debits the same payable
	# the salary entry credited, so anything already raised is now out of date.
	result["refreshed"] = _refresh_payable_requests(doc)

	if not result["created"] and not result["skipped"]:
		frappe.throw(_("No employee in this payroll has an OTPL TDS amount for {0} {1}.")
		             .format(result["month"], result["fiscal_year"]))
	return result


def _post_tds_entries(doc):
	"""Post this month's TDS vouchers for the payroll's employees.

	Returns quietly when nobody has a TDS amount, so the payroll submit can call
	it unconditionally; the button wraps this and complains instead.
	"""
	posting_date = getdate(doc.to_date)
	month = MONTHS[posting_date.month - 1]
	fiscal_year = _fiscal_year_for(posting_date)
	settings = frappe.get_doc("OTPL Accounting Settings", "OTPL Accounting Settings")

	created, skipped = [], []
	for row in doc.employees:
		tds_name = frappe.db.get_value(
			"OTPL TDS", {"employee": row.employee, "fiscal_year": fiscal_year}, "name")
		if not tds_name:
			continue

		tds_doc = frappe.get_doc("OTPL TDS", tds_name)
		detail = next((d for d in tds_doc.tds_details
		               if d.month == month and flt(d.amount) > 0), None)
		if not detail:
			continue
		if _je_is_live(detail.journal_entry):
			skipped.append(_("{0}: {1} already posted in {2}").format(
				row.employee, month, detail.journal_entry))
			continue

		jv = _build_tds_journal_entry(
			doc, tds_doc, row, detail, getdate(detail.posting_date or doc.to_date), settings)
		detail.db_set("otpl_payroll", doc.name, update_modified=False)
		detail.db_set("journal_entry", jv, update_modified=False)
		created.append({
			"employee": row.employee,
			"employee_name": row.employee_name,
			"amount": flt(detail.amount, 2),
			"journal_entry": jv,
		})

	return {"month": month, "fiscal_year": fiscal_year,
	        "created": created, "skipped": skipped}


def _refresh_payable_requests(doc):
	"""Re-read the ledger into this payroll's draft payment requests.

	Submitted requests are left alone: their payment entries are already out.
	"""
	refreshed = []
	for d in frappe.get_all("Salary Payable Request",
	                        {"otpl_payroll": doc.name, "docstatus": 0}, ["name"]):
		try:
			spr = frappe.get_doc("Salary Payable Request", d.name)
			spr.flags.ignore_permissions = True
			spr.refresh_from_ledger()
			refreshed.append(d.name)
		except Exception:
			frappe.log_error(
				title="OTPL Payroll {0}: could not refresh {1}".format(doc.name, d.name),
				message=frappe.get_traceback(),
			)
	return refreshed


def _build_tds_journal_entry(doc, tds_doc, row, detail, posting_date, settings):
	"""One standalone voucher: the employee's payable debited, TDS payable
	credited. Accounts come from Employee Salary Base Data exactly as Employee
	Salary picks them - the vertical's "TDS Due" row - and the cost center from
	the employee's sales order, falling back to their location.
	"""
	base = frappe.db.get_value(
		"Employee Salary Base Data",
		{"business_vertical": row.business_line, "purpose": TDS_PURPOSE},
		["dr_ledger", "cr_ledger", "employee_in_dr_or_cr_or_both"], as_dict=1,
	)
	if not base:
		frappe.throw(_("No '{0}' Employee Salary Base Data for {1} (employee {2}).").format(
			TDS_PURPOSE, row.business_line or _("(no business line)"), row.employee))

	cost_center = None
	if row.sales_order:
		cost_center = frappe.db.get_value("Cost Center", {"sales_order": row.sales_order}, "name")
	if not cost_center:
		cost_center = _location_cost_center(row.location, settings)

	amount = flt(detail.amount, 2)
	debit = {"account": base.dr_ledger, "debit_in_account_currency": amount,
	         "cost_center": cost_center}
	credit = {"account": base.cr_ledger, "credit_in_account_currency": amount,
	          "cost_center": cost_center}
	if base.employee_in_dr_or_cr_or_both in ("Dr", "Both"):
		debit["party_type"] = "Employee"
		debit["party"] = row.employee
	if base.employee_in_dr_or_cr_or_both in ("Cr", "Both"):
		credit["party_type"] = "Employee"
		credit["party"] = row.employee

	jv = frappe.new_doc("Journal Entry")
	jv.posting_date = posting_date
	jv.voucher_type = "Journal Entry"
	jv.company = frappe.db.get_value("Global Defaults", "Global Defaults", "default_company")
	jv.business_vertical = row.business_line
	jv.purpose = TDS_PURPOSE
	jv.user_remark = _("TDS for {0} - {1} ({2})").format(
		detail.month, row.employee_name or "", row.employee)
	jv.otpl_ref_doctype = tds_doc.doctype
	jv.otpl_ref_name = tds_doc.name
	jv.append("accounts", debit)
	jv.append("accounts", credit)
	jv.flags.ignore_mandatory = True
	jv.flags.ignore_permissions = True
	jv.insert()
	jv.submit()
	return jv.name


def _fiscal_year_for(posting_date):
	fy = frappe.db.sql(
		"""SELECT name FROM `tabFiscal Year`
		   WHERE %(d)s BETWEEN year_start_date AND year_end_date LIMIT 1""",
		{"d": posting_date},
	)
	if not fy:
		frappe.throw(_("No Fiscal Year covers {0}.").format(posting_date))
	return fy[0][0]


def _je_is_live(journal_entry):
	"""True while the voucher exists and is not cancelled."""
	if not journal_entry:
		return False
	docstatus = frappe.db.get_value("Journal Entry", journal_entry, "docstatus")
	return docstatus is not None and cint(docstatus) != 2


def _cancel_payroll_tds_entries(doc):
	"""TDS debits the same payable the salary entry credited, so the vouchers
	this payroll posted cannot outlive it. The rows are freed for a re-run."""
	rows = frappe.db.sql(
		"""SELECT name, journal_entry FROM `tabOTPL TDS Detail`
		   WHERE otpl_payroll = %s AND IFNULL(journal_entry, '') != ''""",
		doc.name, as_dict=True,
	)
	for row in rows:
		if _je_is_live(row.journal_entry):
			jv = frappe.get_doc("Journal Entry", row.journal_entry)
			jv.flags.ignore_permissions = True
			jv.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry", "Payment Ledger Entry")
			jv.cancel()
		frappe.db.set_value("OTPL TDS Detail", row.name, "journal_entry", None,
		                    update_modified=False)


def _cancel_payroll_bookings(doc):
	"""Cancel every journal entry this payroll posted, and remove any payment
	request still sitting in draft against it."""
	for d in frappe.get_all("Salary Payable Request",
	                        {"otpl_payroll": doc.name, "docstatus": 0}, ["name"]):
		frappe.delete_doc("Salary Payable Request", d.name, force=1, ignore_permissions=True)

	for d in frappe.get_all("Journal Entry",
	                        {"otpl_ref_doctype": doc.doctype, "otpl_ref_name": doc.name,
	                         "docstatus": 1}, ["name"]):
		jv = frappe.get_doc("Journal Entry", d.name)
		jv.flags.ignore_permissions = True
		jv.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry", "Payment Ledger Entry")
		jv.cancel()

	submitted = frappe.get_all("Salary Payable Request",
	                           {"otpl_payroll": doc.name, "docstatus": 1}, ["name"])
	if submitted:
		frappe.msgprint(
			_("These Salary Payable Requests were already submitted and were left "
			  "untouched; cancel them separately if the payments must be reversed: {0}")
			.format(", ".join(d.name for d in submitted))
		)
	doc.db_set("salary_entries_created", 0)



def _fetch_order_verticals(sales_orders):
	"""{sales_order: business_line} for the orders a payroll touches.

	An employee can work orders belonging to another vertical during the month
	(a USFD worker spending days on PAUT orders). The wages, the payable and the
	voucher itself all follow the vertical that owns the ORDER, so that
	vertical's P&L carries its own cost and no one else's.
	"""
	if not sales_orders:
		return {}
	rows = frappe.db.sql(
		"""SELECT name, business_line FROM `tabSales Order` WHERE name IN %(so)s""",
		{"so": tuple(sales_orders)}, as_dict=True,
	)
	return {r.name: r.business_line for r in rows if r.business_line}


def _location_cost_center(location, settings):
	"""Cost center for costs that belong to no sales order (office overhead).

	Only Noida and Haridwar have one; anything else returns None and ERPNext
	falls back to the company default at submit.
	"""
	if location == "Noida":
		return settings.get("noida_cost_center")
	if location == "Haridwar":
		return settings.get("haridwar_cost_center")
	return None


def _employee_order_parts(row, alloc_by_emp, default_so, fallback_used):
	"""[(sales_order, amount, cost_center)] for one employee, reconciled to the
	row's Total Salary Due."""
	due = flt(row.total_salary_due, 2)
	parts = [(so, amt, cc) for so, amt, _wd, cc in (alloc_by_emp.get(row.employee) or [])]
	if not parts:
		fallback = row.sales_order or default_so
		if not fallback:
			frappe.throw(
				_("Employee {0} has no sales order from attendance, no default on the "
				  "Employee master, and no Default Sales Order in OTPL Accounting "
				  "Settings.").format(row.employee)
			)
		if not row.sales_order:
			fallback_used.append("{0} -> {1}".format(row.employee, fallback))
		cc = frappe.db.get_value("Cost Center", {"sales_order": fallback}, "name")
		parts = [(fallback, due, cc)]

	# The allocation splits Total Salary Due exactly, but a hand-edit after
	# Calculate can leave a gap; push it onto the largest part.
	drift = flt(due - sum(p[1] for p in parts), 2)
	if drift:
		i = max(range(len(parts)), key=lambda k: abs(parts[k][1]))
		parts[i] = (parts[i][0], flt(parts[i][1] + drift, 2), parts[i][2])
	return parts


def _expense_vertical_for(sales_order, employee_vertical, order_vertical, default_so):
	"""Which vertical bears this slice of an employee's wages.

	The vertical that owns the SALES ORDER, because that is whose work was done.
	Two cases fall back to the employee's own vertical: an order with no business
	line of its own, and the settings-level catch-all order, which stands in for
	"no order at all" - an employee with no order is their own vertical's
	overhead, and charging them to whichever vertical that placeholder order
	happens to belong to would move real cost between P&Ls.
	"""
	if not sales_order or sales_order == default_so:
		return employee_vertical
	return order_vertical.get(sales_order) or employee_vertical


def _build_journal_entries(doc, by_vertical, alloc_by_emp, settings, default_so,
                           posting_date, fallback_used, order_vertical, base_data_by_vertical):
	"""One voucher per business vertical, internally consistent.

	Grouping is by the vertical that BEARS the cost, not the one the employee
	sits under, so a voucher tagged PAUT contains only PAUT's expense head, only
	PAUT's payable, and only PAUT's cost centers. An employee who worked orders
	across two verticals therefore appears on two vouchers - each carrying that
	vertical's share of their wages and, pro-rata, of their PF and ESIC.
	"""
	# vertical -> what that vertical's voucher owes
	expense = defaultdict(lambda: defaultdict(float))    # vertical -> (cc) -> amount
	credit = defaultdict(lambda: defaultdict(float))     # vertical -> employee -> amount
	pf_share = defaultdict(lambda: defaultdict(float))   # vertical -> employee -> pf
	esic_share = defaultdict(lambda: defaultdict(float))
	main_cc = defaultdict(dict)                          # vertical -> employee -> cost center
	order_rows = []
	employee_verticals = {}

	for employee_vertical, rows in by_vertical.items():
		for row in rows:
			parts = _employee_order_parts(row, alloc_by_emp, default_so, fallback_used)
			location_cc = _location_cost_center(row.location, settings)
			employee_verticals[row.employee] = employee_vertical

			# Split this employee's wages across the verticals that bear them.
			per_vertical = defaultdict(float)
			for sales_order, amount, cost_center in parts:
				cc = cost_center or location_cc
				vertical = _expense_vertical_for(
					sales_order, employee_vertical, order_vertical, default_so)
				if vertical not in base_data_by_vertical:
					vertical = employee_vertical
				expense[vertical][cc] += amount
				per_vertical[vertical] += amount
				main_cc[vertical].setdefault(row.employee, cc)
				order_rows.append((row.employee, sales_order, amount, cc, vertical))

			due = flt(row.total_salary_due, 2)
			for vertical, amount in per_vertical.items():
				credit[vertical][row.employee] += amount

			# PF and ESIC follow the wages that attracted them, so each voucher
			# carries the deduction belonging to the cost it booked.
			verticals = sorted(per_vertical, key=lambda v: (-per_vertical[v], v))
			ratios = [per_vertical[v] / due for v in verticals] if due else []
			for label, total, target in (("pf", flt(row.pf_employee_share, 2), pf_share),
			                             ("esic", flt(row.esic_employee_share, 2), esic_share)):
				if total <= 0 or not ratios:
					continue
				for vertical, part in zip(verticals, _split_amount(total, ratios)):
					if part:
						target[vertical][row.employee] += part

	created = []
	for vertical in sorted(set(expense) | set(credit)):
		name = _build_one_journal_entry(
			doc, vertical, base_data_by_vertical[vertical], expense[vertical],
			credit[vertical], pf_share[vertical], esic_share[vertical],
			main_cc[vertical], settings, posting_date)
		created.append(name)
		_stamp_order_allocations(
			doc, [r for r in order_rows if r[4] == vertical], name)
	return created


def _build_one_journal_entry(doc, vertical, base_data_name, expense_by_cc, credit_by_emp,
                             pf_by_emp, esic_by_emp, main_cc, settings, posting_date):
	base = frappe.db.get_value(
		"Employee Salary Base Data", base_data_name,
		["dr_ledger", "cr_ledger"], as_dict=1)
	expense_account = base.dr_ledger
	payable_account = base.cr_ledger

	is_haridwar = vertical == "ATW"
	statutory_cc = settings.get("haridwar_cost_center" if is_haridwar else "noida_cost_center")
	epf_expense = settings.get("epf_haridwar" if is_haridwar else "epf_noida")
	esic_expense = settings.get("esic_haridwar" if is_haridwar else "esic_noida")

	accounts = [
		{"account": expense_account, "debit_in_account_currency": flt(amount, 2), "cost_center": cc}
		for cc, amount in sorted(expense_by_cc.items(), key=lambda kv: str(kv[0]))
		if flt(amount, 2)
	]
	for employee, amount in sorted(credit_by_emp.items()):
		if not flt(amount, 2):
			continue
		accounts.append({
			"account": payable_account,
			"credit_in_account_currency": flt(amount, 2),
			"party_type": "Employee", "party": employee,
			"cost_center": main_cc.get(employee),
		})

	epf_total = esic_total = 0.0
	for employee, amount in sorted(pf_by_emp.items()):
		epf_total += flt(amount, 2)
		accounts.append({
			"account": payable_account, "debit_in_account_currency": flt(amount, 2),
			"party_type": "Employee", "party": employee, "cost_center": main_cc.get(employee),
		})
	for employee, amount in sorted(esic_by_emp.items()):
		esic_total += flt(amount, 2)
		accounts.append({
			"account": payable_account, "debit_in_account_currency": flt(amount, 2),
			"party_type": "Employee", "party": employee, "cost_center": main_cc.get(employee),
		})

	if epf_total > 0:
		employer = flt((epf_total / 12) * 13, 2)
		accounts.append({"account": epf_expense, "debit_in_account_currency": employer,
		                 "cost_center": statutory_cc})
		accounts.append({"account": settings.get("epf_payable"),
		                 "credit_in_account_currency": flt(epf_total + employer, 2),
		                 "cost_center": statutory_cc})
	if esic_total > 0:
		employer = flt((esic_total / 0.75) * 3.25, 2)
		accounts.append({"account": esic_expense, "debit_in_account_currency": employer,
		                 "cost_center": statutory_cc})
		accounts.append({"account": settings.get("esic_payable"),
		                 "credit_in_account_currency": flt(esic_total + employer, 2),
		                 "cost_center": statutory_cc})

	jv = frappe.new_doc("Journal Entry")
	jv.posting_date = posting_date
	jv.voucher_type = "Journal Entry"
	jv.company = frappe.db.get_value("Global Defaults", "Global Defaults", "default_company")
	jv.business_vertical = vertical
	# Salary Payable Request keys off this purpose when reading the salary back.
	jv.purpose = "Salary Due"
	jv.user_remark = _("Salary for {0} to {1} ({2}) - {3}").format(
		doc.from_date, doc.to_date, vertical, doc.name)
	jv.otpl_ref_doctype = doc.doctype
	jv.otpl_ref_name = doc.name
	for line in accounts:
		jv.append("accounts", line)
	jv.flags.ignore_mandatory = True
	jv.flags.ignore_permissions = True
	jv.insert()
	jv.submit()
	return jv.name


def _stamp_order_allocations(doc, order_rows, jv_name):
	"""Write the voucher and the cost centre actually used back onto the order
	allocation rows, so the payroll shows where each order's salary was booked."""
	index = {}
	for a in doc.get("order_allocations") or []:
		index.setdefault((a.employee, a.sales_order), a)
	for employee, sales_order, _amount, cost_center, _vertical in order_rows:
		alloc = index.get((employee, sales_order))
		if not alloc:
			continue
		alloc.db_set("journal_entry", jv_name, update_modified=False)
		if cost_center and alloc.cost_center != cost_center:
			alloc.db_set("cost_center", cost_center, update_modified=False)


def _create_salary_payable_requests(doc, verticals, posting_date):
	"""Raise a draft Salary Payable Request per vertical.

	Best effort: the journal entries are the point of this run, so a payment
	request that cannot be built (missing base data, say) is reported rather
	than allowed to roll the whole posting back.
	"""
	made = []
	try:
		for vertical in verticals:
			spr = frappe.new_doc("Salary Payable Request")
			spr.business_vertical = vertical
			# Must match the journal entries' posting date: get_from_jv reads the
			# salary back by exact posting_date.
			spr.date_till_salary_to_calculate = posting_date
			spr.otpl_payroll = doc.name
			# Every employee the payroll covered should appear, including those
			# with nothing to pay this month; filtering by payout was dropping
			# people the payroll had booked.
			spr.due_greater_then_zero = 0
			spr.flags.ignore_permissions = True
			spr.populate_details()
			if not spr.get("salary_payable_request_details"):
				continue
			spr.insert()
			spr.populate_details()
			made.append(spr.name)
		return made, None
	except Exception:
		frappe.log_error(
			title="OTPL Payroll {0}: Salary Payable Request failed".format(doc.name),
			message=frappe.get_traceback(),
		)
		return made, _("see Error Log")


# -----------------------------------------------------------------------------
# Salary sheet download
# -----------------------------------------------------------------------------
_SKIP_FIELDTYPES = ("Section Break", "Column Break", "Tab Break", "HTML", "Button")


def _sheet_columns(doctype):
	"""(fieldname, label) for every real column of a child doctype, in the
	order the form shows them, so the export tracks the doctype definition."""
	return [
		(f.fieldname, f.label or f.fieldname)
		for f in frappe.get_meta(doctype).fields
		if f.fieldtype not in _SKIP_FIELDTYPES
	]


def _write_sheet(ws, columns, rows, numeric_from=None):
	from openpyxl.styles import Font, PatternFill
	from openpyxl.utils import get_column_letter

	header_fill = PatternFill("solid", fgColor="D9E1F2")
	bold = Font(bold=True)

	for c_idx, (_fn, label) in enumerate(columns, 1):
		cell = ws.cell(row=1, column=c_idx, value=label)
		cell.font = bold
		cell.fill = header_fill
	ws.freeze_panes = "A2"

	for r_idx, row in enumerate(rows, 2):
		for c_idx, (fn, _label) in enumerate(columns, 1):
			value = row.get(fn)
			if isinstance(value, Decimal):
				value = float(value)
			ws.cell(row=r_idx, column=c_idx, value=value)

	# Totals for the numeric columns, so the sheet foots.
	if rows and numeric_from:
		total_row = len(rows) + 2
		ws.cell(row=total_row, column=1, value="TOTAL").font = bold
		for c_idx, (fn, _label) in enumerate(columns, 1):
			if fn in numeric_from:
				col = get_column_letter(c_idx)
				cell = ws.cell(row=total_row, column=c_idx,
				               value="=SUM({0}2:{0}{1})".format(col, total_row - 1))
				cell.font = bold

	for c_idx, (_fn, label) in enumerate(columns, 1):
		width = max(10, min(32, len(str(label)) + 4))
		ws.column_dimensions[get_column_letter(c_idx)].width = width


@frappe.whitelist()
def download_salary_sheet(payroll):
	"""Emit the salary register as a two-sheet workbook: the per-employee
	sheet, and the sales-order wise split behind it."""
	from openpyxl import Workbook
	from io import BytesIO

	doc = frappe.get_doc("OTPL Payroll", payroll)
	doc.check_permission("read")

	wb = Workbook()

	detail_cols = _sheet_columns("OTPL Payroll Detail")
	numeric = {
		fn for fn, _l in detail_cols
		if frappe.get_meta("OTPL Payroll Detail").get_field(fn).fieldtype
		in ("Currency", "Float", "Int", "Percent")
	}
	ws = wb.active
	ws.title = "Salary Sheet"
	_write_sheet(ws, detail_cols, [r.as_dict() for r in doc.employees], numeric)

	alloc_cols = _sheet_columns("OTPL Payroll Order Allocation")
	alloc_numeric = {"worked_days", "allocated_days", "salary_amount", "total_salary_due"}
	ws2 = wb.create_sheet("Order Wise")
	_write_sheet(ws2, alloc_cols,
	             [r.as_dict() for r in (doc.get("order_allocations") or [])],
	             alloc_numeric)

	out = BytesIO()
	wb.save(out)

	frappe.response["type"] = "binary"
	frappe.response["filecontent"] = out.getvalue()
	frappe.response["filename"] = "Salary Sheet {0} {1} to {2}.xlsx".format(
		doc.name, doc.from_date, doc.to_date)
