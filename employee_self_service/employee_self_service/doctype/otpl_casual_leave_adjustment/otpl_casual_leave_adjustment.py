# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

"""Casual Leave opening-balance reconciliation.

Works like Stock Reconciliation, but for Casual Leave: HR states the balance an
employee *should* have as on an effective date (01-08-2026 by default), and the
tool posts the difference against the current balance into the leave ledger.

Nothing dated on/after the effective date is touched. The stated balance is an
OPENING balance, so leave already booked on/after that date keeps being deducted
from it by the standard leave engine — set 6, with 2 days taken, and the system
reports 4.

The adjustment is posted as a Leave Ledger Entry of transaction_type
"Leave Allocation" starting on the effective date, rather than as a new Leave
Allocation document: ERPNext refuses a second Leave Allocation overlapping an
existing one (validate_allocation_overlap), and 33 employees already hold a
2026-01-01..2026-12-31 Casual Leave allocation. Balances are read from the
ledger, so a ledger entry moves them exactly like an allocation would.
"""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate

from erpnext.hr.doctype.leave_application.leave_application import (
	get_leave_allocation_records,
	get_leaves_for_period,
)

CASUAL_LEAVE = "Casual Leave"


class OTPLCasualLeaveAdjustment(Document):
	def validate(self):
		if getdate(self.allocation_to_date) < getdate(self.effective_date):
			frappe.throw(_("Valid Till cannot be before the Effective Date"))

		self.title = _("Casual Leave Adjustment as on {0}").format(
			frappe.utils.formatdate(self.effective_date)
		)

		self._validate_duplicate_employees()
		self._refresh_rows()
		self._set_totals()

	def before_submit(self):
		# Recomputed at the last possible moment: the balances shown may have been
		# fetched days ago, and a leave approved since then changes what is safe.
		self._refresh_rows()
		self._set_totals()
		self._validate_sufficient_balance()
		self._validate_no_overlapping_adjustment()

	def on_submit(self):
		created = self._ensure_leave_allocations()
		self._post_ledger_entries(created)

	def on_cancel(self):
		self._remove_ledger_entries()

	# -------------------------------------------------------------------------
	# Validation
	# -------------------------------------------------------------------------
	def _validate_duplicate_employees(self):
		seen = set()
		for row in self.employees:
			if row.employee in seen:
				frappe.throw(_("Employee {0} appears more than once (row {1})")
					.format(row.employee, row.idx))
			seen.add(row.employee)

	def _validate_sufficient_balance(self):
		"""Block the submit when the balance being set cannot cover the Casual
		Leave already booked on/after the effective date.

		This is the "no balance but leave exists" guard: posting anyway would drive
		the employee's ledger negative, and Casual Leave does not allow negative
		balances.
		"""
		offenders = []
		for row in self.employees:
			if flt(row.new_balance) < 0:
				frappe.throw(_("Row {0}: New Balance for {1} cannot be negative")
					.format(row.idx, row.employee))

			if flt(row.balance_after) < 0:
				offenders.append(_(
					"Row {0}: {1} ({2}) has {3} Casual Leave day(s) booked on/after {4} "
					"but the balance being set is only {5} — short by {6} day(s)."
				).format(
					row.idx, row.employee, row.employee_name,
					flt(row.leave_taken_after),
					frappe.utils.formatdate(self.effective_date),
					flt(row.new_balance),
					flt(-row.balance_after),
				))

		if offenders:
			frappe.throw(
				_("Casual Leave balance is short for {0} employee(s). Raise the New Balance, "
				  "or cancel the leave, before submitting.").format(len(offenders))
				+ "<br><br>" + "<br>".join(offenders),
				title=_("Insufficient Casual Leave Balance"),
			)

	def _validate_no_overlapping_adjustment(self):
		"""One submitted adjustment per employee per effective date. Submitting a
		second one would post the delta twice, since each is measured against the
		balance at the time it was fetched."""
		employees = [r.employee for r in self.employees]
		if not employees:
			frappe.throw(_("Add at least one employee"))

		existing = frappe.db.sql("""
			SELECT d.employee, d.parent
			FROM `tabOTPL Casual Leave Adjustment Detail` d
			INNER JOIN `tabOTPL Casual Leave Adjustment` p ON p.name = d.parent
			WHERE p.docstatus = 1
				AND p.name <> %(name)s
				AND p.effective_date = %(effective_date)s
				AND d.employee IN %(employees)s
		""", {
			"name": self.name,
			"effective_date": self.effective_date,
			"employees": employees,
		}, as_dict=1)

		if existing:
			frappe.throw(
				_("Casual Leave has already been adjusted as on {0} for:").format(
					frappe.utils.formatdate(self.effective_date))
				+ "<br>" + "<br>".join(
					"{0} — {1}".format(e.employee, e.parent) for e in existing[:20]
				),
				title=_("Duplicate Adjustment"),
			)

	# -------------------------------------------------------------------------
	# Row maintenance
	# -------------------------------------------------------------------------
	def _refresh_rows(self):
		for row in self.employees:
			_fill_row(row, self.effective_date, self.allocation_to_date)

	def _set_totals(self):
		self.total_employees = len(self.employees)
		self.total_adjustment = flt(sum(flt(r.adjustment) for r in self.employees), 2)

	# -------------------------------------------------------------------------
	# Leave Allocation
	# -------------------------------------------------------------------------
	def _ensure_leave_allocations(self):
		"""Give every employee in the table a submitted Casual Leave Leave
		Allocation covering the effective date, creating a 0-day one (effective
		date .. Valid Till) where none exists. Returns the names created.

		The balance itself still moves only through this tool's ledger entries;
		the allocation is the container other features need — the work-on-holiday
		CL credit attaches to the allocation document and skips employees who have
		none. The row's allocation_from_date cannot be used to decide this: it is
		read from the ledger, so an earlier adjustment's entries look like an
		allocation even when no Leave Allocation document exists."""
		created = []
		for row in self.employees:
			name = ensure_casual_leave_allocation(
				row.employee, self.effective_date,
				row.allocation_to_date or self.allocation_to_date,
				description=_("Auto-created by Casual Leave Adjustment {0}").format(self.name),
			)
			if name:
				created.append(name)
		return created

	# -------------------------------------------------------------------------
	# Ledger posting
	# -------------------------------------------------------------------------
	def _post_ledger_entries(self, created_allocations=()):
		posted = skipped = 0
		for row in self.employees:
			delta = flt(row.adjustment, 2)
			if not delta:
				skipped += 1
				continue

			ledger = frappe.get_doc({
				"doctype": "Leave Ledger Entry",
				"employee": row.employee,
				"employee_name": row.employee_name,
				"leave_type": CASUAL_LEAVE,
				# Balances are aggregated from allocation-type ledger rows only, so
				# the delta has to carry this type to count. transaction_name points
				# back at this document instead of a Leave Allocation, hence
				# ignore_links below.
				"transaction_type": "Leave Allocation",
				"transaction_name": self.name,
				"leaves": delta,
				"from_date": self.effective_date,
				"to_date": row.allocation_to_date or self.allocation_to_date,
				"is_carry_forward": 0,
				"is_expired": 0,
				"is_lwp": 0,
			})
			ledger.flags.ignore_permissions = True
			ledger.flags.ignore_links = True
			ledger.insert(ignore_permissions=True)
			ledger.flags.ignore_links = True
			ledger.submit()
			posted += 1

		log = _(
			"Posted {0} leave ledger entrie(s) effective {1}. {2} row(s) had no change."
		).format(posted, self.effective_date, skipped)
		if created_allocations:
			log += "\n" + _("Created {0} Casual Leave allocation(s) (0 days): {1}").format(
				len(created_allocations), ", ".join(created_allocations))
		self.db_set("processing_log", log, update_modified=False)

	def _remove_ledger_entries(self):
		"""Leave Ledger Entry.on_cancel refuses anything that is not an expiry
		entry, so the rows are removed the same way ERPNext's own
		delete_ledger_entry() does it — a direct delete."""
		count = frappe.db.sql("""
			SELECT COUNT(*) FROM `tabLeave Ledger Entry`
			WHERE transaction_type = 'Leave Allocation' AND transaction_name = %s
		""", self.name)[0][0]

		frappe.db.sql("""
			DELETE FROM `tabLeave Ledger Entry`
			WHERE transaction_type = 'Leave Allocation' AND transaction_name = %s
		""", self.name)

		# Allocations this document auto-created are deliberately left in place: they
		# hold 0 days, so they move no balance, and work-on-holiday CL credits may
		# already be attached to them.
		self.db_set("processing_log", _(
			"Cancelled: removed {0} leave ledger entrie(s). Leave Applications are "
			"unaffected — this tool never changed them. Any Casual Leave allocation it "
			"auto-created (0 days) is kept."
		).format(count), update_modified=False)


def ensure_casual_leave_allocation(employee, from_date, to_date, description=None):
	"""Make sure a submitted Casual Leave Leave Allocation covers ``from_date``
	for ``employee``. If none does, create and submit a 0-day one from
	``from_date`` to ``to_date`` — cut short the day before any later allocation
	so it never overlaps one. Returns the new allocation's name, or None if one
	already existed."""
	from_date, to_date = getdate(from_date), getdate(to_date)
	if frappe.db.exists("Leave Allocation", {
		"employee": employee, "leave_type": CASUAL_LEAVE, "docstatus": 1,
		"from_date": ["<=", from_date], "to_date": [">=", from_date],
	}):
		return None

	next_from = frappe.db.sql("""
		SELECT MIN(from_date) FROM `tabLeave Allocation`
		WHERE employee = %s AND leave_type = %s AND docstatus = 1
			AND from_date > %s AND from_date <= %s
	""", (employee, CASUAL_LEAVE, from_date, to_date))[0][0]
	if next_from:
		to_date = add_days(next_from, -1)

	alloc = frappe.get_doc({
		"doctype": "Leave Allocation",
		"employee": employee,
		"leave_type": CASUAL_LEAVE,
		"from_date": from_date,
		"to_date": to_date,
		"new_leaves_allocated": 0,
		"unused_leaves": 0,
		"total_leaves_allocated": 0,
		"carry_forward": 0,
		"description": description,
	})
	alloc.employee_name = frappe.db.get_value("Employee", employee, "employee_name")
	alloc.flags.ignore_permissions = True
	# Leave Allocation.validate refuses a 0-day allocation ("Total leaves allocated
	# is mandatory") — the one case this needs. The overlap check it would also run
	# is done above.
	alloc.flags.ignore_validate = True
	alloc.insert()
	alloc.submit()
	return alloc.name


# -----------------------------------------------------------------------------
# Balance helpers
# -----------------------------------------------------------------------------
def _casual_leave_state(employee, effective_date, fallback_to_date):
	"""Return (opening_balance, taken_on_or_after, alloc_from, alloc_to) for
	Casual Leave around ``effective_date``.

	``opening_balance`` mirrors what ERPNext itself would report on the day before
	the effective date: the allocation covering the date, less every Casual Leave
	day consumed before it. ``taken_on_or_after`` is what the standard engine will
	go on deducting from whatever new balance is set — this tool leaves those
	Leave Applications exactly as they are.
	"""
	effective_date = getdate(effective_date)
	alloc = (get_leave_allocation_records(employee, effective_date, CASUAL_LEAVE) or {}).get(
		CASUAL_LEAVE
	)

	if alloc and alloc.from_date and alloc.to_date:
		alloc_from, alloc_to = getdate(alloc.from_date), getdate(alloc.to_date)
		# get_leaves_for_period returns leave consumption as a NEGATIVE number,
		# which is why it is added rather than subtracted (same as
		# get_remaining_leaves does).
		opening = flt(alloc.total_leaves_allocated) + flt(
			get_leaves_for_period(employee, CASUAL_LEAVE, alloc_from, add_days(effective_date, -1))
		)
	else:
		# No allocation covers the effective date, so the employee has no Casual
		# Leave entitlement to open with. The adjustment itself becomes their
		# allocation, running to the document's Valid Till date.
		alloc_from, alloc_to = None, getdate(fallback_to_date)
		opening = 0.0

	taken_after = -flt(
		get_leaves_for_period(employee, CASUAL_LEAVE, effective_date, alloc_to)
	)

	return flt(opening, 2), flt(taken_after, 2), alloc_from, alloc_to


def _fill_row(row, effective_date, fallback_to_date):
	opening, taken_after, alloc_from, alloc_to = _casual_leave_state(
		row.employee, effective_date, fallback_to_date
	)

	if not row.employee_name:
		row.employee_name = frappe.db.get_value("Employee", row.employee, "employee_name")

	row.current_balance = opening
	row.leave_taken_after = taken_after
	row.allocation_from_date = alloc_from
	row.allocation_to_date = alloc_to
	row.adjustment = flt(flt(row.new_balance) - opening, 2)
	row.balance_after = flt(flt(row.new_balance) - taken_after, 2)


# -----------------------------------------------------------------------------
# Whitelisted actions (called from the form)
# -----------------------------------------------------------------------------
@frappe.whitelist()
def get_employees(effective_date, allocation_to_date, staff_type=None, location=None,
                  employee=None, include_zero_balance=1):
	"""Rows for every active employee matching the filters, with their current
	Casual Leave position already worked out. ``new_balance`` is pre-filled with
	the current balance so untouched rows post nothing."""
	include_zero_balance = frappe.utils.cint(include_zero_balance)

	filters = {"status": "Active"}
	if employee:
		filters["name"] = employee
	if staff_type:
		filters["staff_type"] = staff_type
	if location:
		filters["location"] = location

	employees = frappe.get_all(
		"Employee", filters=filters, fields=["name", "employee_name"], order_by="name asc"
	)

	rows = []
	for emp in employees:
		opening, taken_after, alloc_from, alloc_to = _casual_leave_state(
			emp.name, effective_date, allocation_to_date
		)
		if not include_zero_balance and not opening and not taken_after:
			continue

		rows.append({
			"employee": emp.name,
			"employee_name": emp.employee_name,
			"current_balance": opening,
			# Pre-filled so a row nobody edits produces a zero adjustment.
			"new_balance": opening,
			"adjustment": 0.0,
			"leave_taken_after": taken_after,
			"balance_after": flt(opening - taken_after, 2),
			"allocation_from_date": alloc_from,
			"allocation_to_date": alloc_to,
		})

	return rows


@frappe.whitelist()
def get_employee_balance(employee, effective_date, allocation_to_date):
	"""Current Casual Leave position for one employee, for the grid's employee
	field."""
	opening, taken_after, alloc_from, alloc_to = _casual_leave_state(
		employee, effective_date, allocation_to_date
	)
	return {
		"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
		"current_balance": opening,
		"leave_taken_after": taken_after,
		"allocation_from_date": alloc_from,
		"allocation_to_date": alloc_to,
	}
