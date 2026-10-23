import frappe


def execute():
	"""Seed the Site check-in reminder time with the time the app hardcoded
	(Site has no ESS Location shift)."""
	frappe.reload_doc("employee_self_service", "doctype", "employee_self_service_settings")
	settings = "Employee Self Service Settings"
	if not frappe.db.get_single_value(settings, "site_check_in_reminder_time"):
		frappe.db.set_value(settings, settings, "site_check_in_reminder_time", "09:00:00")
