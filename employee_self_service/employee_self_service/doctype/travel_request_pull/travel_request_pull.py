# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff
from employee_self_service.employee_self_service.utils.erp_sync import push_travel_status_to_source


class TravelRequestPull(Document):
	def before_save(self):
		self.calculate_number_of_days()

	def calculate_number_of_days(self):
		if self.date_of_departure and self.date_of_arrival:
			self.number_of_days = date_diff(self.date_of_arrival, self.date_of_departure) + 1

	def on_update(self):
		"""Sync status updates back to source ERP when approved/rejected"""
		if not self.source_erp:
			return
		if self.has_value_changed('status'):
			push_travel_status_to_source(self)


@frappe.whitelist()
def approve_travel_request_pull(docname):
	"""Approve a travel request pull"""
	try:
		doc = frappe.get_doc("Travel Request Pull", docname)
		doc.status = "Approved"
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		frappe.msgprint(_("Travel Request approved successfully and synced to source ERP"))
		return {"success": True, "message": "Travel Request approved successfully"}

	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title="Error approving travel request pull"
		)
		frappe.throw(_("Error approving travel request: {0}").format(str(e)))
