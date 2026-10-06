# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

"""Re-apply the OTPL Leave Casual Leave rule to one employee's month.

OTPL Leave books the first 2 leave days of a calendar month as Casual Leave and
every further day as Leave Without Pay — but only while the employee HAS a CL
balance at approval time. When the balance is granted later (Leave Allocation,
OTPL Casual Leave Adjustment), days that should have been CL are already sitting
as LWP. This tool re-runs the rule for an employee + month against today's
balance and rebuilds the OTPL Leave's Leave Applications to match.

Days already on Casual Leave are kept on it first, and LWP days are then promoted
in date order while the monthly cap and the balance allow, so only the days that
really change get new Leave Applications. A Leave Application holding a changed
day is cancelled and rebuilt as a whole; days of it outside the month keep their
type. Cancelling the restructure puts every rebuilt day back on its old type.

It is a silent back-office correction: no leave status / approval email goes to
the employee or approver — only the records change.
"""

from __future__ import unicode_literals

import calendar
from collections import OrderedDict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate

from erpnext.hr.doctype.leave_application.leave_application import get_leave_balance_on
from employee_self_service.employee_self_service.utils.travelling_cl_credit import (
	CL_ENCASHMENT_ONLY_STAFF_TYPES,
)
from employee_self_service.employee_self_service.utils.system_user import as_system_user
from employee_self_service.employee_self_service.doctype.otpl_leave.otpl_leave import (
	mute_leave_application_notifications,
)

CASUAL_LEAVE = "Casual Leave"
LWP = "Leave Without Pay"
# Same company rule as OTPL Leave._create_regular_leave_applications.
MONTHLY_CL_CAP = 2.0
OTPL_STAMP = "Auto-created from OTPL Leave:"
MONTHS = list(calendar.month_name)[1:]


class OTPLLeaveRestructure(Document):
	def validate(self):
		self._set_period()
		self._validate_staff_type()
		self.build_plan()

	def before_submit(self):
		# Rebuilt at the last moment: a leave approved or a balance granted since
		# the preview changes the outcome.
		self.build_plan()
		if not self.changed_days:
			frappe.throw(_("Nothing to restructure: the leave days of {0} {1} already follow the "
				"Casual Leave rule.").format(self.month, self.year))
		if self.payroll_warning and not self.allow_after_payroll:
			frappe.throw(_("{0}<br><br>Tick <b>Restructure Even Though Payroll Is Submitted</b> to go ahead.")
				.format(self.payroll_warning), title=_("Payroll Already Submitted"))

	def on_submit(self):
		rows = [r for r in self.days if r.current_leave_application in self._applications_to_rebuild()]
		with as_system_user():
			created = _rebuild(self.employee, rows, "current_leave_application", "new_leave_type")
		for r in self.days:
			r.db_set("new_leave_application", created.get(r.leave_date) or r.current_leave_application,
				update_modified=False)
		self._log("Submitted: {0} Leave Application(s) cancelled, {1} created.".format(
			len({r.current_leave_application for r in rows}), len(set(created.values()))))
		self._comment_on_leaves(rows, "restructured by", "current_leave_type", "new_leave_type")

	def on_cancel(self):
		# Only days whose OTPL Leave is still Approved and whose rebuilt
		# application is still live are restored. An OTPL Leave cancelled or
		# deleted since then has no leave left to restore, and must never block
		# cancelling this record (nor this record block it — hence no Link fields).
		rows, skipped = [], set()
		for r in self.days:
			if r.new_leave_application == r.current_leave_application:
				continue
			if (frappe.db.get_value("OTPL Leave", r.otpl_leave, "status") == "Approved"
					and frappe.db.get_value("Leave Application", r.new_leave_application, "docstatus") == 1):
				rows.append(r)
			else:
				skipped.add(r.otpl_leave)
		created = {}
		if rows:
			with as_system_user():
				created = _rebuild(self.employee, rows, "new_leave_application", "current_leave_type")
		self._log("Cancelled: leave types restored, {0} Leave Application(s) created.{1}".format(
			len(set(created.values())),
			" Skipped (OTPL Leave no longer approved / deleted): {0}.".format(", ".join(sorted(skipped)))
			if skipped else ""))
		self._comment_on_leaves(rows, "restored by cancelling", "new_leave_type", "current_leave_type")

	# -------------------------------------------------------------------------
	# Plan
	# -------------------------------------------------------------------------
	def _set_period(self):
		if self.month not in MONTHS:
			frappe.throw(_("Please select a valid Month"))
		if not (2000 <= (self.year or 0) <= 2100):
			frappe.throw(_("Please enter a valid Year"))
		month = MONTHS.index(self.month) + 1
		self.from_date = getdate("{0}-{1:02d}-01".format(self.year, month))
		self.to_date = getdate("{0}-{1:02d}-{2:02d}".format(
			self.year, month, calendar.monthrange(self.year, month)[1]))

	def _validate_staff_type(self):
		staff_type = frappe.db.get_value("Employee", self.employee, "staff_type")
		if staff_type in CL_ENCASHMENT_ONLY_STAFF_TYPES:
			frappe.throw(_("{0} staff never spend Casual Leave on leave — their CL is kept for "
				"encashment, so OTPL Leave books every leave day as Leave Without Pay. There is "
				"nothing to restructure.").format(staff_type))

	def build_plan(self):
		"""Fill the day table and totals with the current vs. rule-based leave type."""
		start, end = getdate(self.from_date), getdate(self.to_date)
		days = _otpl_leave_days(self.employee, start, end)
		in_month = [d for d in days if not d["outside_month"]]

		self.monthly_cl_cap = MONTHLY_CL_CAP
		self.other_cl_in_month = _other_casual_leave_days(
			self.employee, start, end, {d["current_leave_application"] for d in days})

		current_cl = sum(d["day_weight"] for d in in_month if d["current_leave_type"] == CASUAL_LEAVE)
		balance = 0.0
		if in_month:
			# Over the whole allocation period, the way OTPL Leave reads it, so CL
			# already booked in later months is never taken away from them. This
			# month's own CL days are counted as used in it, so they are added back.
			balance = flt(get_leave_balance_on(
				employee=self.employee,
				leave_type=CASUAL_LEAVE,
				date=in_month[0]["leave_date"],
				consider_all_leaves_in_the_allocation_period=True,
			) or 0) + current_cl
		self.cl_available = balance

		# Days already on CL keep it first, then LWP days are promoted in date
		# order — the CL/LWP totals are the same as a plain date-order pass, but
		# no Leave Application is rebuilt just to swap two days.
		cap_left = MONTHLY_CL_CAP - flt(self.other_cl_in_month)
		order = sorted(in_month, key=lambda d: (d["current_leave_type"] != CASUAL_LEAVE, d["leave_date"]))
		for d in order:
			w = d["day_weight"]
			if cap_left >= w and balance >= w:
				d["new_leave_type"] = CASUAL_LEAVE
				cap_left -= w
				balance -= w
			else:
				d["new_leave_type"] = LWP
		for d in days:
			if d["outside_month"]:
				d["new_leave_type"] = d["current_leave_type"]
			d["changed"] = int(d["new_leave_type"] != d["current_leave_type"])

		self.set("days", [])
		for d in days:
			self.append("days", d)

		def total(field, leave_type):
			return sum(d["day_weight"] for d in in_month if d[field] == leave_type)
		self.current_cl_days = total("current_leave_type", CASUAL_LEAVE)
		self.current_lwp_days = total("current_leave_type", LWP)
		self.new_cl_days = total("new_leave_type", CASUAL_LEAVE)
		self.new_lwp_days = total("new_leave_type", LWP)
		self.changed_days = sum(d["day_weight"] for d in days if d["changed"])
		self.payroll_warning = _payroll_warning(self.employee, start, end)

	def _applications_to_rebuild(self):
		return {r.current_leave_application for r in self.days if r.changed}

	# -------------------------------------------------------------------------
	# Helpers
	# -------------------------------------------------------------------------
	def _log(self, line):
		log = "{0}\n{1}  {2}".format(self.processing_log or "", frappe.utils.now(), line).strip()
		self.db_set("processing_log", log, update_modified=False)

	def _comment_on_leaves(self, rows, verb, from_field, to_field):
		changed = [r for r in rows if r.changed]
		note = "Leave types {0} {1}: {2}".format(verb, self.name, ", ".join(
			"{0} {1} → {2}".format(r.leave_date, r.get(from_field), r.get(to_field)) for r in changed))
		for leave in sorted({r.otpl_leave for r in rows}):
			if not frappe.db.exists("OTPL Leave", leave):
				continue
			try:
				frappe.get_doc("OTPL Leave", leave).add_comment("Comment", text=note)
			except Exception:
				pass   # a comment is nice to have; never fail the restructure over it


def _otpl_leave_days(employee, start, end):
	"""One dict per leave day of every submitted CL / LWP Leave Application created
	by an Approved OTPL Leave and touching [start, end].

	Days of those applications that fall outside the month are included too
	(outside_month=1): if such an application is rebuilt, they have to be
	recreated with their current type.
	"""
	apps = frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee,
			"docstatus": 1,
			"leave_type": ["in", [CASUAL_LEAVE, LWP]],
			"from_date": ["<=", end],
			"to_date": [">=", start],
			"description": ["like", OTPL_STAMP + "%"],
		},
		fields=["name", "leave_type", "from_date", "to_date", "half_day", "half_day_date", "description"],
		order_by="from_date asc",
	)

	days = []
	for la in apps:
		otpl_leave = la.description.split(OTPL_STAMP)[-1].strip()
		if frappe.db.get_value("OTPL Leave", otpl_leave, "status") != "Approved":
			continue
		# A half day with no date is the first day (as ERPNext counts it).
		half_day_date = getdate(la.half_day_date or la.from_date) if la.half_day else None
		d = getdate(la.from_date)
		while d <= getdate(la.to_date):
			days.append({
				"leave_date": d,
				"day_weight": 0.5 if d == half_day_date else 1.0,
				"otpl_leave": otpl_leave,
				"current_leave_type": la.leave_type,
				"current_leave_application": la.name,
				"outside_month": int(not (start <= d <= end)),
			})
			d = add_days(d, 1)
	days.sort(key=lambda x: x["leave_date"])
	return days


def _other_casual_leave_days(employee, start, end, exclude):
	"""Casual Leave days in [start, end] from submitted applications not in ``exclude``."""
	total = 0.0
	for la in frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee,
			"docstatus": 1,
			"leave_type": CASUAL_LEAVE,
			"from_date": ["<=", end],
			"to_date": [">=", start],
		},
		fields=["name", "from_date", "to_date", "half_day", "half_day_date"],
	):
		if la.name in exclude:
			continue
		s, e = max(getdate(la.from_date), start), min(getdate(la.to_date), end)
		days = (e - s).days + 1
		if la.half_day and s <= getdate(la.half_day_date or la.from_date) <= e:
			days -= 0.5
		total += days
	return total


def _payroll_warning(employee, start, end):
	payrolls = frappe.db.sql_list("""
		select distinct p.name from `tabOTPL Payroll` p
		inner join `tabOTPL Payroll Detail` d on d.parent = p.name
		where p.docstatus = 1 and d.employee = %s and p.from_date <= %s and p.to_date >= %s
	""", (employee, end, start))
	if not payrolls:
		return ""
	return _("Submitted OTPL Payroll {0} already covers this month for this employee. Restructuring "
		"changes the leave records and the CL balance going forward, but not what that payroll paid."
		).format(", ".join(payrolls))


def _rebuild(employee, rows, from_field, type_field):
	"""Cancel the Leave Applications named in ``rows[*][from_field]`` and recreate
	their days with leave type ``rows[*][type_field]``.

	``rows`` must hold every day of each application being cancelled. Returns
	{date: new Leave Application name}.
	"""
	from employee_self_service.employee_self_service.utils.rerun_attendance import (
		cancel_and_delete_existing_attendance,
	)

	by_leave = OrderedDict()
	for r in sorted(rows, key=lambda r: getdate(r.leave_date)):
		by_leave.setdefault(r.otpl_leave, []).append(r)

	# 1. Cancel the old applications and drop them from their OTPL Leave. The
	#    OTPL Leave stays Approved, so the before_cancel guard must be told this
	#    is a deliberate detach.
	for leave_name, leave_rows in by_leave.items():
		old = {r.get(from_field) for r in leave_rows}
		for name in old:
			la = frappe.get_doc("Leave Application", name)
			mute_leave_application_notifications(la)
			la.flags.ignore_otpl_leave_link = True
			la.flags.ignore_permissions = True
			la.cancel()
		leave = frappe.get_doc("OTPL Leave", leave_name)
		refs = [x.strip() for x in (leave.leave_applications or "").split(",") if x.strip() and x.strip() not in old]
		leave.db_set("leave_applications", ", ".join(refs), update_modified=False)

	# 2. Clear the days' attendance (the cancelled applications leave it as
	#    cancelled On Leave rows); the new applications mark it again on submit.
	for d in sorted({getdate(r.leave_date) for r in rows}):
		cancel_and_delete_existing_attendance(employee, d, commit=False)

	# 3. One application per run of consecutive days with the same type, never
	#    crossing a month — the same shape OTPL Leave itself creates.
	created = {}
	for leave_name, leave_rows in by_leave.items():
		leave = frappe.get_doc("OTPL Leave", leave_name)
		leave.flags.mute_leave_notifications = True
		segments = []
		for r in leave_rows:
			d, leave_type = getdate(r.leave_date), r.get(type_field)
			prev = segments[-1] if segments else None
			if (prev and prev["leave_type"] == leave_type and prev["rows"][-1][0] == add_days(d, -1)
					and (prev["rows"][-1][0].year, prev["rows"][-1][0].month) == (d.year, d.month)):
				prev["rows"].append((d, flt(r.day_weight)))
			else:
				segments.append({"leave_type": leave_type, "rows": [(d, flt(r.day_weight))]})

		for seg in segments:
			half = [d for d, w in seg["rows"] if w < 1]
			name = leave.make_leave_application(
				leave_type=seg["leave_type"],
				from_date=seg["rows"][0][0],
				to_date=seg["rows"][-1][0],
				total_days=sum(w for _d, w in seg["rows"]),
				half_day=1 if half else 0,
				half_day_date=half[0] if half else None,
			)
			for d, _w in seg["rows"]:
				created[d] = name
	return created


@frappe.whitelist()
def get_plan(doc):
	"""Preview for the form's Get Leaves button — builds the plan without saving."""
	if not frappe.has_permission("OTPL Leave Restructure", "create"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	doc = frappe.get_doc(frappe.parse_json(doc))
	doc._set_period()
	doc._validate_staff_type()
	doc.build_plan()
	return doc.as_dict()

