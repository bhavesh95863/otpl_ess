import frappe
from frappe.utils import nowdate


def execute():
	"""Backfill `post_arrival_processed = 1` for all approved Travel Requests
	whose Date of Arrival is already in the past.

	The post-arrival employee state transition (e.g. setting
	`employee_availability = 'On Leave'` for a 'Going on Leave' request) is
	now a one-shot operation gated by `post_arrival_processed`. Without this
	backfill, the next daily run of `process_travel_requests` would re-apply
	the transition for every historical request and overwrite manual changes
	to `employee_availability` / `travelling`.
	"""
	if not frappe.db.has_column("Travel Request", "post_arrival_processed"):
		# Field added via doctype JSON; migrate should have created it. If not,
		# nothing to do here — re-running migrate will add it and re-run patch.
		print("Skipping: column `post_arrival_processed` not present on Travel Request")
		return

	frappe.db.sql(
		"""
		UPDATE `tabTravel Request`
		SET post_arrival_processed = 1
		WHERE status = %s
		  AND date_of_arrival < %s
		  AND IFNULL(post_arrival_processed, 0) = 0
		""",
		("Approved", nowdate()),
	)
	affected = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
	print("Backfilled post_arrival_processed=1 on {0} Travel Request(s)".format(affected))
