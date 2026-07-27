# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, getdate, nowdate
from employee_self_service.employee_self_service.utils.leave_escalation import (
	resolve_approver_chain,
	get_employee_contact,
	get_employee_pull_contact,
)


class TravellingCL(Document):
	"""A travelling request that, once Approved, permits the employee to check in /
	check out from OUT OF LOCATION on the covered dates (from_date..to_date).

	Approver resolution mirrors OTPL Leave for non-Site-Worker staff: the request
	goes to the employee's Report To (internal) or External Report To (external),
	resolved through the escalation chain. Site Workers already have travelling
	activated and do not use this form.
	"""

	def validate(self):
		self.validate_dates()
		self.validate_date_conflict()
		self.calculate_number_of_days()
		self.set_approver()
		self.set_contact_details()

	def on_update(self):
		# The manager's decision cascades to the out-of-location check-ins the
		# request enabled: Approved -> approve them, Rejected -> reject them.
		self.auto_approve_linked_checkins_on_approve()
		self.auto_reject_linked_checkins_on_reject()

		# Sync to the remote ERP when the approver is external (same pattern as
		# Travel Request / OTPL Leave). Guarded so a sync outage never blocks the
		# request itself.
		try:
			from employee_self_service.employee_self_service.utils.erp_sync import (
				push_travelling_cl_to_remote_erp,
			)
			push_travelling_cl_to_remote_erp(self)
		except Exception:
			frappe.log_error(
				title="Travelling CL remote sync failed: {0}".format(self.name),
				message=frappe.get_traceback(),
			)

	def _linked_pending_checkins(self):
		"""Not-yet-actioned out-of-location check-ins / check-outs of this employee
		within the request's date range."""
		if not (self.employee and self.from_date and self.to_date):
			return []
		return frappe.get_all(
			"Employee Checkin",
			filters={
				"employee": self.employee,
				"approval_required": 1,
				"approved": 0,
				"rejected": 0,
				"time": ["between", [
					"{0} 00:00:00".format(getdate(self.from_date)),
					"{0} 23:59:59".format(getdate(self.to_date)),
				]],
			},
			fields=["name"],
		)

	def auto_approve_linked_checkins_on_approve(self):
		"""When the Travelling CL is Approved, approve the out-of-location check-ins
		it enabled — full approval (marks approved and processes attendance), same
		as the manager approving each check-in individually."""
		if self.get("__islocal"):
			return
		before = self.get_doc_before_save()
		if not (before and before.status != "Approved" and self.status == "Approved"):
			return

		from employee_self_service.employee_self_service.utils.otpl_attendance import approve_checkin
		checkins = self._linked_pending_checkins()
		for c in checkins:
			try:
				approve_checkin(c.name, ignore_permission=True)
			except Exception:
				# Never fail the Travelling CL approval over one check-in; at least
				# mark it approved so it is not left blocking.
				frappe.db.set_value("Employee Checkin", c.name, "approved", 1, update_modified=False)
				frappe.log_error(
					title="Travelling CL cascade approve failed: {0}".format(c.name),
					message=frappe.get_traceback(),
				)
		if checkins:
			frappe.db.commit()

	def auto_reject_linked_checkins_on_reject(self):
		"""When the Travelling CL is Rejected, every out-of-location check-in /
		check-out it was enabling is automatically rejected too (Phase 4). Only
		not-yet-approved punches in the request's date range are touched."""
		if self.get("__islocal"):
			return
		before = self.get_doc_before_save()
		if not (before and before.status != "Rejected" and self.status == "Rejected"):
			return

		checkins = self._linked_pending_checkins()
		for c in checkins:
			frappe.db.set_value("Employee Checkin", c.name, "rejected", 1, update_modified=False)
		if checkins:
			frappe.db.commit()

	def validate_dates(self):
		# "From Date must be today or later" — only on creation, so approving an
		# existing request whose from_date has since passed is not blocked.
		if self.is_new() and self.from_date:
			if getdate(self.from_date) < getdate(nowdate()):
				frappe.throw(_("From Date cannot be in the past. Please select today or a later date."))
		if self.from_date and self.to_date:
			if getdate(self.to_date) < getdate(self.from_date):
				frappe.throw(_("To Date cannot be before From Date"))

	def validate_date_conflict(self):
		if not (self.employee and self.from_date and self.to_date):
			return
		overlapping = frappe.get_all(
			"Travelling CL",
			filters={
				"employee": self.employee,
				"name": ["!=", self.name or ""],
				"status": ["not in", ["Cancelled", "Rejected"]],
				"from_date": ["<=", self.to_date],
				"to_date": [">=", self.from_date],
			},
			fields=["name", "from_date", "to_date"],
			limit=1,
		)
		if overlapping:
			r = overlapping[0]
			frappe.throw(
				_("Travelling CL dates overlap with existing request {0} ({1} to {2})").format(
					r.name, r.from_date, r.to_date
				)
			)

	def calculate_number_of_days(self):
		if self.from_date and self.to_date:
			self.number_of_days = date_diff(self.to_date, self.from_date) + 1

	def set_approver(self):
		"""Resolve Report To / External Report To exactly like OTPL Leave does for
		non-Site-Worker staff: external report-to chain when the employee has an
		external reporting manager, otherwise the internal reports_to chain."""
		if not self.employee:
			return
		employee_doc = frappe.get_doc("Employee", self.employee)

		on_date = getdate(self.from_date) if self.from_date else getdate(nowdate())

		# Reset before resolving — the chain may flip internal<->external.
		self.report_to = None
		self.has_external_report_to = 0
		self.external_report_to = None

		if getattr(employee_doc, "external_reporting_manager", 0) == 1:
			resolved = resolve_approver_chain(
				{"type": "external", "pull_name": employee_doc.external_report_to},
				on_date=on_date,
				strict=True,
			)
			if resolved:
				if resolved.get("type") == "external":
					self.has_external_report_to = 1
					self.external_report_to = resolved.get("pull_name")
				else:
					self.report_to = resolved.get("employee")
		elif employee_doc.reports_to:
			resolved = resolve_approver_chain(
				{"type": "internal", "employee": employee_doc.reports_to},
				on_date=on_date,
				strict=True,
			)
			if resolved:
				if resolved.get("type") == "internal":
					self.report_to = resolved.get("employee")
				else:
					self.has_external_report_to = 1
					self.external_report_to = resolved.get("pull_name")

		if not self.report_to and not self.has_external_report_to:
			frappe.throw(
				_("No available approver found — the employee has no Report To / External Report To, "
				  "or every manager in the escalation chain is on leave. Please contact HR.")
			)

	def set_contact_details(self):
		"""Populate applicant + approver contact details (name + mobile). Cannot use
		fetch_from because the approver may be an internal Employee or an external
		Employee Pull record."""
		applicant_name, applicant_mobile = get_employee_contact(self.employee)
		if applicant_name and not self.employee_name:
			self.employee_name = applicant_name
		self.applicant_mobile_no = applicant_mobile or ""

		approver_name = approver_mobile = None
		if self.has_external_report_to and self.external_report_to:
			approver_name, approver_mobile = get_employee_pull_contact(self.external_report_to)
		elif self.report_to:
			approver_name, approver_mobile = get_employee_contact(self.report_to)
		self.approver_name = approver_name or ""
		self.approver_mobile_no = approver_mobile or ""


def _travelling_cl_covering(employee, date, statuses):
	"""Name of the employee's Travelling CL in one of ``statuses`` covering
	``date``, or None. Most-recent first."""
	if not (employee and date):
		return None
	date = getdate(date)
	rows = frappe.get_all(
		"Travelling CL",
		filters={
			"employee": employee,
			"status": ["in", statuses],
			"from_date": ["<=", date],
			"to_date": [">=", date],
		},
		fields=["name"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0].name if rows else None


def has_active_travelling_cl(employee, date):
	"""True if the employee has an APPLIED (Pending or Approved) Travelling CL
	covering ``date``. This is the CREATION gate: an enabled staff type may only
	check in / out from out of location once a travelling request exists."""
	return bool(_travelling_cl_covering(employee, date, ["Pending", "Approved"]))


def get_active_travelling_cl(employee, date):
	"""The applied (Pending/Approved) Travelling CL name covering ``date``, or None."""
	return _travelling_cl_covering(employee, date, ["Pending", "Approved"])


def has_approved_travelling_cl(employee, date):
	"""True if the employee has an APPROVED Travelling CL covering ``date``. This
	is the APPROVAL gate: a manager may only approve the out-of-location check-in
	once the travelling request itself is approved (Phase 4)."""
	return bool(_travelling_cl_covering(employee, date, ["Approved"]))


def is_travel_enabled_staff_type(location, staff_type):
	"""True if ``staff_type`` is listed in the ESS Location's "Travelling Enabled
	Staff Types". Options are the Employee staff_type values. Site Workers are
	handled by the caller (always enabled, never gated)."""
	if not (location and staff_type):
		return False
	return bool(
		frappe.db.exists(
			"ESS Location Travel Staff Type",
			{"parent": location, "parenttype": "ESS Location", "staff_type": staff_type},
		)
	)
