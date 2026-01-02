# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from employee_self_service.employee_self_service.utils.erp_sync import push_expense_status_to_source

class ExpensePull(Document):
	def on_update(self):
		"""
		Sync status updates back to source ERP when approved/rejected
		"""
		# Check if this record came from an external source
		if not self.source_erp:
			return
		
		# Check if status or approval changed
		if self.has_value_changed('status') or self.has_value_changed('approved_by_manager'):
			push_expense_status_to_source(self)
