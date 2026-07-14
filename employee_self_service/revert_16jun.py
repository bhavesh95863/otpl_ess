# -*- coding: utf-8 -*-
"""Undo the half-finished merge on 16-Jun-2026 for EMP/00491.

An earlier merge crashed part-way (the `merged_into` column did not exist yet)
and left:

    LEAVE00568   converted to a full-day leave, its Leave Application DELETED
    LEAVE00569   still an Approved half day,    its Leave Application DELETED
    16-Jun       Attendance = Absent (should have been a leave day)

This puts LEAVE00568 back to the half day it was, so the two halves are once
again a matched pair. The fixed merge then does the rest on the next attendance
re-run: it creates ONE NEW full-day OTPL Leave, cancels BOTH halves
(merged_into -> the new leave), and gives the new leave a real approved Leave
Application. Attendance for the day becomes "On Leave".

The two deleted half-day Leave Applications are deliberately NOT recreated: the
merge would only delete them again. The day's Casual Leave is consumed by the new
full-day leave's Leave Application instead.

Nothing else is touched. Dry run by default.

    bench --site <site> execute employee_self_service.revert_16jun.execute
    bench --site <site> execute employee_self_service.revert_16jun.execute \\
        --kwargs "{'dry_run': 0}"
"""

import frappe
from frappe.utils import getdate

EMPLOYEE = "EMP/00491"
DATE = "2026-06-16"
BROKEN = "LEAVE00568"        # the one the crashed merge mutated
PARTNER = "LEAVE00569"       # its other half, untouched
ORIGINAL_PERIOD = "First Half"


def execute(dry_run=1):
	dry_run = int(dry_run)
	tag = "[DRY RUN] " if dry_run else ""

	print("{}Undoing the half-finished merge on {} for {}\n".format(tag, DATE, EMPLOYEE))

	for name in (BROKEN, PARTNER):
		d = frappe.db.get_value(
			"OTPL Leave", name,
			["status", "half_day", "half_day_period", "half_day_date",
			 "total_no_of_days", "leave_applications"],
			as_dict=True,
		)
		print("  NOW  {}: status={} half_day={} period={} days={} LA={!r}".format(
			name, d.status, d.half_day, d.half_day_period,
			d.total_no_of_days, d.leave_applications))

	att = frappe.get_all(
		"Attendance",
		filters={"employee": EMPLOYEE, "attendance_date": DATE, "docstatus": ["<", 2]},
		fields=["name", "status"],
	)
	print("  NOW  Attendance on {}: {}\n".format(
		DATE, [(a.name, a.status) for a in att] or "(none)"))

	# --- guard: only act on the exact broken shape this repairs -------------
	broken = frappe.db.get_value(
		"OTPL Leave", BROKEN, ["status", "half_day", "leave_applications"], as_dict=True
	)
	if not (broken.status == "Approved" and not broken.half_day
			and not (broken.leave_applications or "").strip()):
		print("  ABORT: {} is not in the broken shape this script repairs "
			  "(Approved, half_day=0, no Leave Application). Nothing done.".format(BROKEN))
		return {"aborted": True}

	partner = frappe.db.get_value(
		"OTPL Leave", PARTNER, ["status", "half_day"], as_dict=True
	)
	if not (partner.status == "Approved" and partner.half_day):
		print("  ABORT: {} is no longer an Approved half day. Nothing done.".format(PARTNER))
		return {"aborted": True}

	# --- restore LEAVE00568 to the half day it was --------------------------
	print("  {}restore {} -> half_day=1, {}, {}, 0.5 days".format(
		tag, BROKEN, ORIGINAL_PERIOD, DATE))
	print("  {}leave the Absent attendance on {} alone — the re-run deletes and "
		  "rebuilds it".format(tag, DATE))

	if not dry_run:
		doc = frappe.get_doc("OTPL Leave", BROKEN)
		doc.flags.ignore_permissions = True
		doc.db_set("half_day", 1, update_modified=False)
		doc.db_set("half_day_period", ORIGINAL_PERIOD, update_modified=False)
		doc.db_set("half_day_date", getdate(DATE), update_modified=False)
		doc.db_set("total_no_of_days", 0.5, update_modified=False)
		doc.db_set("total_no_of_approved_days", 0.5, update_modified=False)
		doc.db_set("leave_applications", "", update_modified=False)
		doc.add_comment(
			"Comment",
			text="Restored to a half day: an earlier merge crashed part-way and had "
				 "converted this leave to a full day without ever creating its Leave "
				 "Application.",
		)
		frappe.db.commit()

	print()
	if dry_run:
		print("[DRY RUN] Nothing was changed. Re-run with dry_run=0 to apply.")
	else:
		print("Done. {} and {} are a matched half-day pair again.".format(BROKEN, PARTNER))
		print("Now re-run attendance for June — the merge will create ONE new full-day "
			  "OTPL Leave, cancel both halves, and give it an approved Leave Application.")

	return {"dry_run": dry_run}
