import frappe
from frappe.utils import getdate


# Period to repair (inclusive).
FROM_DATE = "2026-04-01"
TO_DATE = "2026-06-30"


def execute():
	"""One-off repair for OTPL Leaves approved in May 2026 that never got their
	Leave Application(s) created.

	Historically an error during Leave Application creation (attendance already
	marked, leave overlap, etc.) could leave an OTPL Leave marked "Approved"
	without any linked Leave Application. This patch finds those records for the
	01-05-2026..31-05-2026 window and, for each:

	  1. Deletes any existing Attendance in the leave's approved date range, so
	     the Leave Application can be (re)created and re-generate attendance for
	     those days as leave.
	  2. Re-runs the same creation logic used on approval and stores the
	     Leave Application reference back on the OTPL Leave.

	Each record is processed in its own transaction: a failure on one record is
	logged and skipped without aborting the rest. The patch is idempotent — an
	OTPL Leave that already has a (non-cancelled) auto-created Leave Application
	is skipped, so it is safe to re-run.
	"""
	otpl_leaves = frappe.get_all(
		"OTPL Leave",
		filters={
			"status": "Approved",
			"from_date": ["between", [FROM_DATE, TO_DATE]],
		},
		fields=["name"],
		order_by="from_date asc",
	)

	repaired = 0
	skipped = 0
	failed = 0

	for row in otpl_leaves:
		name = row.name

		# Idempotency: skip if a non-cancelled Leave Application already exists
		# for this OTPL Leave (make_leave_application stamps this description).
		if frappe.db.exists(
			"Leave Application",
			{
				"description": "Auto-created from OTPL Leave: {0}".format(name),
				"docstatus": ["<", 2],
			},
		):
			skipped += 1
			continue

		try:
			doc = frappe.get_doc("OTPL Leave", name)

			# Short Leave never creates a Leave Application (the employee stays
			# present for the day). These records are correct as-is — nothing to
			# repair, and we must not touch their actual attendance.
			if doc.short_leave:
				skipped += 1
				continue

			start, end = _approved_range(doc)
			if not (start and end):
				print(
					"Skipping {0}: no approved date range to repair".format(name)
				)
				skipped += 1
				continue

			deleted = _delete_attendance_in_range(doc.employee, start, end)

			# Reuse the exact creation logic used by the approval flow. This
			# inserts + submits the Leave Application(s) and persists the
			# reference back onto the OTPL Leave via add_leave_application_reference().
			doc._create_regular_leave_applications()

			frappe.db.commit()
			repaired += 1
			print(
				"Repaired {0} ({1} -> {2}): created Leave Application(s), "
				"deleted {3} attendance record(s)".format(name, start, end, deleted)
			)
		except Exception:
			frappe.db.rollback()
			failed += 1
			frappe.log_error(
				title="Backfill May 2026 OTPL Leave Application failed: {0}".format(name),
				message=frappe.get_traceback(),
			)
			print("FAILED {0}: see Error Log".format(name))

	print(
		"OTPL Leave May-2026 backfill complete: "
		"{0} repaired, {1} skipped, {2} failed (of {3} approved in window)".format(
			repaired, skipped, failed, len(otpl_leaves)
		)
	)


def _approved_range(doc):
	"""Date range the Leave Application(s) cover, mirroring create logic."""
	start = doc.approved_from_date or doc.from_date
	end = doc.approved_to_date or doc.to_date
	return start, end


def _delete_attendance_in_range(employee, start, end):
	"""Cancel (if submitted) and delete every Attendance for `employee` whose
	attendance_date falls within [start, end]. Returns the count deleted."""
	attendances = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"attendance_date": ["between", [getdate(start), getdate(end)]],
		},
		fields=["name", "docstatus"],
	)

	for att in attendances:
		if att.docstatus == 1:
			att_doc = frappe.get_doc("Attendance", att.name)
			att_doc.flags.ignore_permissions = True
			att_doc.cancel()
		frappe.delete_doc(
			"Attendance", att.name, force=True, ignore_permissions=True
		)

	return len(attendances)
