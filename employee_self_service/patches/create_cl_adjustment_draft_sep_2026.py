import frappe
from frappe.utils import flt


# Casual Leave opening balances as on 01-09-2026, supplied by HR. Creates ONE
# draft OTPL Casual Leave Adjustment — HR reviews and submits it by hand, so this
# patch never posts anything to the leave ledger itself.
EFFECTIVE_DATE = "2026-09-01"
VALID_TILL = "2026-12-31"

BALANCES = [
	("EMP/0072", 5), ("EMP/0104", 14), ("EMP/0014", 16.5), ("EMP/0125", 15),
	("EMP/0060", 12), ("EMP/0244", 8.5), ("EMP/0035", 5), ("EMP/00740", 4),
	("EMP/00763", 4), ("EMP/00711", 4), ("EMP/00728", 4), ("EMP/0004", 6),
	("EMP/00384", 5), ("EMP/0013", 5.5), ("EMP/00573", 5.5), ("EMP/00766", 7.5),
	("EMP/0120", 4), ("EMP/0111", 6.5), ("EMP/00872", 6), ("EMP/00491", 4),
	("EMP/0015", 12), ("EMP/0055", 8), ("EMP/0005", 7.5), ("EMP/00919", 5),
	("EMP/00911", 5.5), ("EMP/00936", 5), ("EMP/00960", 5), ("EMP/00984", 5),
	("EMP/0118", 17), ("EMP/0271", 21.5), ("EMP/0113", 5), ("EMP/00648", 13),
	("EMP/00747", 15), ("EMP/00725", 10), ("EMP/0089", 11), ("EMP/0266", 6),
	("EMP/0079", 8), ("EMP/00575", 7), ("EMP/00385", 15), ("EMP/0205", 8),
	("EMP/01024", 3.5),
]


def execute():
	frappe.reload_doc("employee_self_service", "doctype", "otpl_casual_leave_adjustment_detail")
	frappe.reload_doc("employee_self_service", "doctype", "otpl_casual_leave_adjustment")

	# Idempotent: a draft or submitted adjustment for this date means it was
	# already created (by this patch or by hand) — never make a second one.
	existing = frappe.db.get_value(
		"OTPL Casual Leave Adjustment",
		{"effective_date": EFFECTIVE_DATE, "docstatus": ["<", 2]},
		"name",
	)
	if existing:
		print("CL adjustment for {0} already exists ({1}); skipped.".format(EFFECTIVE_DATE, existing))
		return

	rows, missing = [], []
	for employee, balance in BALANCES:
		if frappe.db.get_value("Employee", employee, "status") == "Active":
			rows.append({"employee": employee, "new_balance": flt(balance)})
		else:
			missing.append(employee)

	if not rows:
		print("CL adjustment for {0}: none of the employees are active here; skipped.".format(EFFECTIVE_DATE))
		return

	doc = frappe.get_doc({
		"doctype": "OTPL Casual Leave Adjustment",
		"company": frappe.db.get_value("Employee", rows[0]["employee"], "company"),
		"effective_date": EFFECTIVE_DATE,
		"allocation_to_date": VALID_TILL,
		"employees": rows,
	})
	doc.flags.ignore_permissions = True
	doc.insert()

	print("Created draft {0}: {1} employee(s), total adjustment {2}.".format(
		doc.name, doc.total_employees, doc.total_adjustment))
	if missing:
		print("Not added (missing or inactive): {0}".format(", ".join(missing)))
