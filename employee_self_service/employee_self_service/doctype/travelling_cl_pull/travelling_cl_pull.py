# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class TravellingCLPull(Document):
	def on_update(self):
		"""When the external manager approves / rejects here, push the decision
		back to the source ERP's original Travelling CL."""
		try:
			from employee_self_service.employee_self_service.utils.erp_sync import (
				push_travelling_cl_status_to_source,
			)
			push_travelling_cl_status_to_source(self)
		except Exception:
			frappe.log_error(
				title="Travelling CL Pull status sync failed: {0}".format(self.name),
				message=frappe.get_traceback(),
			)
