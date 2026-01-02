# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe import _
from employee_self_service.employee_self_service.utils.erp_sync import push_leave_status_to_source

class LeavePull(Document):
	def on_update(self):
		"""
		Sync status updates back to source ERP when approved/rejected
		"""
		# Check if this record came from an external source
		if not self.source_erp:
			return
		
		# Check if status changed (only sync on status change)
		if self.has_value_changed('status'):
			push_leave_status_to_source(self)


@frappe.whitelist()
def approve_leave(docname, approved_from_date, approved_to_date):
	"""
	Approve a leave pull request with approved dates
	This will update the status and trigger reverse sync to source ERP
	"""
	try:
		doc = frappe.get_doc("Leave Pull", docname)
		
		# Validate dates
		if not approved_from_date or not approved_to_date:
			frappe.throw(_("Approved From Date and Approved To Date are required"))
		
		# Calculate approved days
		from frappe.utils import date_diff
		approved_days = date_diff(approved_to_date, approved_from_date) + 1
		
		# Update the document
		doc.status = "Approved"
		doc.approved_from_date = approved_from_date
		doc.approved_to_date = approved_to_date
		doc.total_no_of_approved_days = approved_days
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		
		frappe.msgprint(_("Leave approved successfully and synced to source ERP"))
		return {"success": True, "message": "Leave approved successfully"}
		
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error approving leave pull"
		)
		frappe.throw(_("Error approving leave: {0}").format(str(e)))
