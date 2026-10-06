from __future__ import unicode_literals

from employee_self_service.employee_self_service.utils.travelling_cl_credit import CASUAL_LEAVE


def allow_overlapping_casual_leave(doc, method=None):
	"""Leave Allocation before_validate: let the same employee hold more than one
	Casual Leave allocation over the same period.

	ERPNext v12 LeaveAllocation.validate_allocation_overlap() refuses a second
	allocation overlapping an existing one. Casual Leave is topped up during the
	year, so for that leave type only the check is switched off on this document;
	every other validation still runs. Balances are read from the Leave Ledger,
	which already sums every active allocation, so the extra allocation simply
	adds to the balance.
	"""
	if doc.leave_type == CASUAL_LEAVE:
		doc.validate_allocation_overlap = lambda: None
