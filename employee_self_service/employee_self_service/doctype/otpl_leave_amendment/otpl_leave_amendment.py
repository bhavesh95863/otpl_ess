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


class OTPLLeaveAmendment(Document):
	"""Amend an already-approved OTPL Leave to a SHORTER range (e.g. the employee
	returned early). Goes to the manager for approval. On approval the original
	leave is Cancelled (which frees the returned days for attendance) and a NEW
	approved OTPL Leave is created for the amended range (creating fresh Leave
	Applications). Only full-day leaves may be amended — not Short Leave / Half Day.
	"""

	def validate(self):
		self.validate_original_leave()
		self.validate_amended_range()
		self.calculate_number_of_days()
		self.set_approver()
		self.set_contact_details()

	def on_update(self):
		self.apply_amendment_on_approve()
		# Sync to the remote ERP when the approver is external (same pattern as
		# Travelling CL / OTPL Leave). Guarded so a sync outage never blocks it.
		try:
			from employee_self_service.employee_self_service.utils.erp_sync import (
				push_leave_amendment_to_remote_erp,
			)
			push_leave_amendment_to_remote_erp(self)
		except Exception:
			frappe.log_error(
				title="OTPL Leave Amendment remote sync failed: {0}".format(self.name),
				message=frappe.get_traceback(),
			)

	# -- validation ----------------------------------------------------------
	def validate_original_leave(self):
		if not self.original_leave:
			return
		orig = frappe.get_doc("OTPL Leave", self.original_leave)
		if orig.employee != self.employee:
			frappe.throw(_("The original leave does not belong to this employee."))
		if orig.status != "Approved":
			frappe.throw(_("Only an Approved leave can be amended (current status: {0}).").format(orig.status))
		if orig.short_leave or orig.half_day:
			frappe.throw(_("Short Leave and Half Day leaves cannot be amended."))
		if not (orig.approved_from_date and orig.approved_to_date):
			frappe.throw(_("The original leave has no approved date range to amend."))
		self._orig = orig

	def validate_amended_range(self):
		orig = getattr(self, "_orig", None) or (self.original_leave and frappe.get_doc("OTPL Leave", self.original_leave))
		if not orig:
			return
		if not (self.amended_from_date and self.amended_to_date):
			return
		af, at = getdate(self.amended_from_date), getdate(self.amended_to_date)
		of, ot = getdate(orig.approved_from_date), getdate(orig.approved_to_date)
		if at < af:
			frappe.throw(_("Amended To Date cannot be before Amended From Date."))
		if af < of or at > ot:
			frappe.throw(_("The amended range must sit within the original leave ({0} to {1}).").format(of, ot))
		orig_days = date_diff(ot, of) + 1
		new_days = date_diff(at, af) + 1
		if new_days >= orig_days:
			frappe.throw(_("An amendment must shorten the leave. The original leave is {0} day(s); "
			              "the amended range is {1} day(s).").format(orig_days, new_days))

	def calculate_number_of_days(self):
		if self.amended_from_date and self.amended_to_date:
			self.number_of_days = date_diff(self.amended_to_date, self.amended_from_date) + 1

	def set_approver(self):
		"""Resolve Report To / External Report To like OTPL Leave / Travelling CL for
		non-Site-Worker staff — the amendment goes to the employee's manager."""
		if not self.employee:
			return
		employee_doc = frappe.get_doc("Employee", self.employee)
		on_date = getdate(nowdate())
		self.report_to = None
		self.has_external_report_to = 0
		self.external_report_to = None

		if getattr(employee_doc, "external_reporting_manager", 0) == 1:
			resolved = resolve_approver_chain(
				{"type": "external", "pull_name": employee_doc.external_report_to},
				on_date=on_date, strict=True,
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
				on_date=on_date, strict=True,
			)
			if resolved:
				if resolved.get("type") == "internal":
					self.report_to = resolved.get("employee")
				else:
					self.has_external_report_to = 1
					self.external_report_to = resolved.get("pull_name")

		if not self.report_to and not self.has_external_report_to:
			frappe.throw(_("No available approver found for this amendment. Please contact HR."))

	def set_contact_details(self):
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

	# -- approval cascade ----------------------------------------------------
	def apply_amendment_on_approve(self):
		"""On the Approved transition: cancel the original leave and create the new
		shorter approved leave. Idempotent — runs once, guarded by amended_leave."""
		if self.get("__islocal"):
			return
		before = self.get_doc_before_save()
		if not (before and before.status != "Approved" and self.status == "Approved"):
			return
		if self.amended_leave:
			return   # already applied

		orig = frappe.get_doc("OTPL Leave", self.original_leave)

		# 1. Cancel the original leave — cancels its Leave Applications and frees the
		#    returned days for attendance.
		if orig.status != "Cancelled":
			orig.flags.ignore_permissions = True
			orig.status = "Cancelled"
			orig.save(ignore_permissions=True)

		# 2. Create the amended (shorter) leave, Approved, so it builds fresh Leave
		#    Applications for the amended range. Insert as Pending with validation
		#    bypassed (the from_date is in the past and the approver is copied), then
		#    flip to Approved so OTPL Leave.create_leave_applications runs.
		new = frappe.new_doc("OTPL Leave")
		new.employee = self.employee
		new.employee_name = orig.employee_name
		new.applicant_mobile_no = orig.applicant_mobile_no
		new.alternate_mobile_no = orig.get("alternate_mobile_no")
		new.from_date = self.amended_from_date
		new.to_date = self.amended_to_date
		new.approved_from_date = self.amended_from_date
		new.approved_to_date = self.amended_to_date
		new.total_no_of_days = self.number_of_days
		new.total_no_of_approved_days = self.number_of_days
		new.reason = "Amended from {0}: {1}".format(orig.name, orig.get("reason") or "")
		new.approver = orig.get("approver")
		new.approver_name = orig.get("approver_name")
		new.approver_mobile_no = orig.get("approver_mobile_no")
		new.is_external_manager = orig.get("is_external_manager")
		new.external_manager = orig.get("external_manager")
		new.status = "Pending"
		new.flags.ignore_validate = True
		new.flags.ignore_permissions = True
		new.insert(ignore_permissions=True)

		new.status = "Approved"
		new.flags.ignore_validate = True
		new.flags.ignore_permissions = True
		new.save(ignore_permissions=True)

		self.db_set("amended_leave", new.name, update_modified=False)
		frappe.db.commit()

		note = ("Leave amendment {0} approved: original leave {1} ({2}..{3}) cancelled and "
		        "replaced by {4} ({5}..{6}).").format(
			self.name, orig.name, orig.approved_from_date, orig.approved_to_date,
			new.name, self.amended_from_date, self.amended_to_date)
		for doc in (self, orig, new):
			try:
				doc.add_comment("Comment", text=note)
			except Exception:
				pass
