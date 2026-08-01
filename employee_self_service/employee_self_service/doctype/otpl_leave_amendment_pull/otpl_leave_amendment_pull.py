# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OTPLLeaveAmendmentPull(Document):
	def on_update(self):
		"""When the external manager approves / rejects here, push the decision back
		to the source ERP's original OTPL Leave Amendment (which runs the cascade)."""
		try:
			from employee_self_service.employee_self_service.utils.erp_sync import (
				push_leave_amendment_status_to_source,
			)
			push_leave_amendment_status_to_source(self)
		except Exception:
			frappe.log_error(
				title="OTPL Leave Amendment Pull status sync failed: {0}".format(self.name),
				message=frappe.get_traceback(),
			)
