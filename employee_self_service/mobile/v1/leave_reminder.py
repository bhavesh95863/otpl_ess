# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt
"""Mobile API for the last-day-of-leave reminder.

  * get_leave_end_reminder -> the logged-in employee's Approved OTPL Leave
    whose approved range ends on the given date (today by default).
  * respond_leave_end_reminder -> the employee's answer to that reminder (Back to
    Work / Apply New Leave / Remind Later), saved as a comment on the leave.
"""

import json
import frappe
from frappe.utils import add_days, getdate, nowdate

from employee_self_service.mobile.v1.api_utils import (
	gen_response,
	ess_validate,
	get_employee_by_user,
	exception_handler,
)

REMINDER_ACTIONS = ["Back to Work", "Apply New Leave", "Remind Later"]
# Once the employee has answered with one of these, the reminder is not shown again.
FINAL_ACTIONS = ["Back to Work", "Apply New Leave"]
COMMENT_PREFIX = "Leave end reminder response: "


@frappe.whitelist()
@ess_validate(methods=["GET"])
def get_leave_end_reminder(date=None):
	"""Reminder for any employee on the LAST day of an Approved full-day OTPL
	Leave. No reminder when another Approved leave starts the next day (the leave
	does not really end)."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")
		on_date = getdate(date or nowdate())
		empty = {"show_reminder": 0, "date": str(on_date), "leaves": []}

		rows = frappe.get_all(
			"OTPL Leave",
			filters={
				"employee": emp.get("name"),
				"status": "Approved",
				"short_leave": 0,
				"half_day": 0,
				"approved_to_date": on_date,
			},
			fields=["name", "approved_from_date", "approved_to_date",
			        "total_no_of_approved_days", "reason"],
			order_by="approved_from_date asc",
		)
		next_day = add_days(on_date, 1)
		continues = frappe.db.exists("OTPL Leave", {
			"employee": emp.get("name"),
			"status": "Approved",
			"short_leave": 0,
			"approved_from_date": ["<=", next_day],
			"approved_to_date": [">=", next_day],
		})
		rows = [r for r in rows if not _final_response(r.name)]
		if not rows or continues:
			return gen_response(200, "success", empty)

		leaves = [{
			"name": r.name,
			"from_date": str(r.approved_from_date),
			"to_date": str(r.approved_to_date),
			"number_of_days": r.total_no_of_approved_days,
			"reason": r.reason,
			"rejoin_date": str(next_day),
		} for r in rows]
		return gen_response(200, "success", {
			"show_reminder": 1,
			"date": str(on_date),
			"title": "Your leave ends today",
			"message": "Your approved leave ends today ({0}). Please resume work on {1}.".format(
				on_date.strftime("%d-%m-%Y"), getdate(next_day).strftime("%d-%m-%Y")),
			"leaves": leaves,
			"actions": REMINDER_ACTIONS,
		})
	except Exception as e:
		return exception_handler(e)


@frappe.whitelist()
@ess_validate(methods=["POST"])
def respond_leave_end_reminder(*args, **kwargs):
	"""Record the employee's answer to the leave end reminder as a comment on the
	OTPL Leave. Body: leave, action (Back to Work | Apply New Leave | Remind
	Later), remarks (optional)."""
	try:
		emp = get_employee_by_user(frappe.session.user)
		if not emp:
			return gen_response(500, "Employee does not exist")
		data = kwargs or json.loads(frappe.request.get_data() or "{}")
		leave, action = data.get("leave"), data.get("action")
		if action not in REMINDER_ACTIONS:
			return gen_response(500, "action must be one of: {0}".format(", ".join(REMINDER_ACTIONS)))
		if not leave or not frappe.db.exists("OTPL Leave", leave):
			return gen_response(500, "Leave does not exist")
		doc = frappe.get_doc("OTPL Leave", leave)
		if doc.employee != emp.get("name"):
			return gen_response(500, "You are not authorized to respond for this leave")
		if doc.status != "Approved":
			return gen_response(500, "Only an Approved leave has an end reminder (current status: {0})".format(doc.status))

		text = COMMENT_PREFIX + action
		if data.get("remarks"):
			text += " | Remarks: {0}".format(frappe.utils.escape_html(data.get("remarks")))
		comment = doc.add_comment("Comment", text=text)
		return gen_response(200, "Response recorded", {
			"leave": doc.name, "action": action, "comment": comment.name,
			"show_again": 0 if action in FINAL_ACTIONS else 1,
		})
	except Exception as e:
		return exception_handler(e)


def _final_response(leave):
	return frappe.db.sql("""select name from `tabComment`
		where reference_doctype='OTPL Leave' and reference_name=%s and comment_type='Comment'
		and ({0}) limit 1""".format(" or ".join(["content like %s"] * len(FINAL_ACTIONS))),
		[leave] + ["{0}{1}%".format(COMMENT_PREFIX, a) for a in FINAL_ACTIONS])
