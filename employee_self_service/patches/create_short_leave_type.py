import frappe


def execute():
	"""Create Short Leave leave type if it doesn't exist."""
	if frappe.db.exists("Leave Type", "Short Leave"):
		print("Leave Type 'Short Leave' already exists")
		return

	leave_type = frappe.new_doc("Leave Type")
	leave_type.leave_type_name = "Short Leave"
	leave_type.is_carry_forward = 0
	leave_type.include_holiday = 0
	leave_type.allow_negative = 0
	leave_type.insert(ignore_permissions=True)
	print("Created Leave Type: Short Leave")
