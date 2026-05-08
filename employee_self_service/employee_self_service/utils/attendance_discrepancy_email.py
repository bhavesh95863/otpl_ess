# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import re
import frappe
from frappe.utils import getdate, add_days, today, format_datetime

from employee_self_service.employee_self_service.report.attendance_discrepancy_report.attendance_discrepancy_report import (
	execute as run_discrepancy_report,
)


@frappe.whitelist()
def send_attendance_discrepancy_email(date=None):
	try:
		"""Build the Attendance Discrepancy Report for the given date (default: yesterday)
		and email it to the recipients configured in Employee Self Service Settings.
		Designed to run daily from the scheduler.
		"""
		target_date = getdate(date) if date else getdate(add_days(today(), -1))

		recipients = _get_recipients()
		if not recipients:
			frappe.logger().info(
				"Attendance Discrepancy Email skipped: no recipients configured."
			)
			return {"sent": False, "reason": "No recipients configured"}

		columns, data = run_discrepancy_report({"date": str(target_date)})

		subject = "Attendance Discrepancy Report - {0}".format(target_date)
		html = _build_email_html(target_date, columns, data)

		# CSV attachment for easy review
		attachments = []
		if data:
			csv_content = _build_csv(columns, data)
			attachments.append({
				"fname": "attendance_discrepancy_{0}.csv".format(target_date),
				"fcontent": csv_content,
			})

		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=html,
			attachments=attachments,
			reference_doctype="Report",
			reference_name="Attendance Discrepancy Report",
			now=True,
		)

		frappe.logger().info(
			"Attendance Discrepancy Email sent for {0} to {1} ({2} rows)".format(
				target_date, ", ".join(recipients), len(data)
			)
		)

		return {"sent": True, "date": str(target_date), "rows": len(data), "recipients": recipients}
	except Exception as e:
		frappe.log_error(title="Error sending Attendance Discrepancy Email", message=frappe.get_traceback())
		return {"sent": False, "reason": str(e)}

def _get_recipients():
	raw = frappe.db.get_single_value(
		"Employee Self Service Settings", "attendance_discrepancy_recipients"
	) or ""
	# Split on comma, semicolon or whitespace; keep only valid-looking emails
	tokens = [t.strip() for t in re.split(r"[,;\s]+", raw) if t.strip()]
	emails = [t for t in tokens if "@" in t and "." in t.split("@")[-1]]
	# De-duplicate while preserving order
	seen = set()
	result = []
	for e in emails:
		if e.lower() not in seen:
			seen.add(e.lower())
			result.append(e)
	return result


def _build_email_html(target_date, columns, data):
	if not data:
		return """
			<p>Hi,</p>
			<p>No attendance discrepancies were found for <b>{date}</b>. All attendance records look clean.</p>
			<p>Regards,<br>Employee Self Service</p>
		""".format(date=target_date)

	# Summary by discrepancy type
	summary = {}
	for row in data:
		summary[row["discrepancy_type"]] = summary.get(row["discrepancy_type"], 0) + 1

	summary_html = "".join(
		"<li><b>{0}</b>: {1}</li>".format(frappe.utils.escape_html(k), v)
		for k, v in sorted(summary.items())
	)

	# Build table
	header_cells = "".join(
		'<th style="border:1px solid #ccc;padding:6px;background:#f4f5f6;text-align:left;font-size:12px;">{0}</th>'.format(
			frappe.utils.escape_html(c["label"])
		)
		for c in columns
	)

	body_rows = []
	color_map = {
		"Attendance Creation Failed": "#fdecea",
		"Attendance Not Processed": "#fdecea",
		"Absent Despite Check-in & Check-out": "#fdecea",
		"Absent - Missing Check-out": "#fff4e5",
		"Absent - Missing Check-in": "#fff4e5",
		"Pending Check-in Approval": "#fffbe6",
	}
	for row in data:
		bg = color_map.get(row.get("discrepancy_type"), "#ffffff")
		cells = []
		for c in columns:
			val = row.get(c["fieldname"])
			if val is None:
				display = ""
			elif c.get("fieldtype") == "Datetime" and val:
				try:
					display = format_datetime(val)
				except Exception:
					display = str(val)
			else:
				display = str(val)
			cells.append(
				'<td style="border:1px solid #ddd;padding:6px;font-size:12px;vertical-align:top;">{0}</td>'.format(
					frappe.utils.escape_html(display)
				)
			)
		body_rows.append(
			'<tr style="background:{0};">{1}</tr>'.format(bg, "".join(cells))
		)

	table_html = """
		<table style="border-collapse:collapse;width:100%;font-family:Arial,sans-serif;">
			<thead><tr>{header}</tr></thead>
			<tbody>{rows}</tbody>
		</table>
	""".format(header=header_cells, rows="".join(body_rows))

	return """
		<div style="font-family:Arial,sans-serif;">
			<p>Hi,</p>
			<p>The following attendance discrepancies were detected for <b>{date}</b>.
			Please review and take corrective action where applicable.</p>
			<p><b>Total discrepancies:</b> {total}</p>
			<ul>{summary}</ul>
			{table}
			<p style="margin-top:16px;">A CSV copy is attached for offline review.</p>
			<p>Regards,<br>Employee Self Service</p>
		</div>
	""".format(date=target_date, total=len(data), summary=summary_html, table=table_html)


def _build_csv(columns, data):
	import csv
	import io

	buf = io.StringIO()
	writer = csv.writer(buf)
	writer.writerow([c["label"] for c in columns])
	for row in data:
		writer.writerow([
			"" if row.get(c["fieldname"]) is None else str(row.get(c["fieldname"]))
			for c in columns
		])
	return buf.getvalue()
