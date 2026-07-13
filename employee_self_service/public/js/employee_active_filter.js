// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt
//
// Restrict Employee link fields to Active employees.
//
// Frappe v12 has no `link_filters` DocField property (that arrived in v14), so a
// link filter can only be applied with frm.set_query(). Rather than scatter that
// across twenty .js files, every Employee picker is declared once in the map
// below and registered from this single file (loaded via app_include_js).
//
// This is a UI filter only: it narrows the search dropdown. Records that already
// point at a Left employee keep working and still display normally, and server
// side writes are unaffected.
//
// Deliberately NOT filtered — these must still be able to reach a Left employee:
//   OTPL Payroll.employee / OTPL Payroll Detail.employee  (trailing payroll)
//   Salary Payable Request Details.employee               (final settlement)
//   Payment Request Initiation Details.employee           (payment to a leaver)
//   Payment Request Supplier Initiation.employee
//   Initial Payment Entry.ret_from_employee               (recovery from a leaver)
//   Employee Gross Salary.employee, OTPL Employee Leave Balance.employee
//   Attendance Creation Failed Log / No Team Leader Error / Team Leader Location
//   Log (machine-written, never picked by hand)

frappe.provide("employee_self_service");

// doctype -> list of Employee link fields to restrict.
//   "fieldname"                      -> a field on the form itself
//   ["grid_fieldname", "fieldname"]  -> a field inside a child table grid
const EMPLOYEE_ACTIVE_FILTERS = {
	"Allowed Overtime": ["employee"],
	"ESS Documents": ["employee_no"],
	"ESS Location": ["reporting_manager"],
	"ESS Post": ["employee"],
	"Employee Device Registration": ["employee"],
	"Employee Location": ["employee"],
	"OTPL Attendance Mark": ["employee", ["employees", "employee"]],
	"OTPL Employee Investment": ["employee"],
	"OTPL Expense": ["sent_by", "transfer_to_employee", "approval_employee"],
	"OTPL Leave": ["employee"],
	"Travel Request": ["employee", "report_to"],
	"Visit": ["employee"],

	// Owned by the site_expense_management app, not this one. Registering it here
	// is safe and needs no change over there: frappe.ui.form.on() is global, and
	// if that app is ever uninstalled the handler simply never fires.
	"Skilled Additional Labor Fund Transfer": [
		"employee",
		"transfer_requested_by",
		"fund_transfer_approved_by",
	],

	// Child-table (grid) pickers are registered on the PARENT form.
	"OTPL Employee Group": [["employees", "employee"]],
	"Employee Update Tool": [
		["employee_update_non_team_leader", "employee"],
		["employee_update_non_team_leader", "report_to"],
		["employee_update_team_leader", "employee"],
	],

	// Notice Board.employees is a Table MultiSelect, NOT a Table. It has no
	// .grid, so the ["grid", "field"] form would never bind. Its control extends
	// ControlLink and IS the link field, so it takes the plain parent form and
	// the filter lands on the Employee link inside it.
	"Notice Board": ["employees"],
};

const ACTIVE_EMPLOYEE_QUERY = () => ({ filters: { status: "Active" } });

function apply_employee_active_filters(frm) {
	const entries = EMPLOYEE_ACTIVE_FILTERS[frm.doctype] || [];

	entries.forEach((entry) => {
		try {
			if (Array.isArray(entry)) {
				const [grid_fieldname, fieldname] = entry;

				// form.set_query()'s child-table branch dereferences
				// fields_dict[grid].grid.get_field(field) without guarding, so a
				// renamed or removed field would throw and break the whole form.
				const grid_field = frm.fields_dict[grid_fieldname];
				if (!grid_field || !grid_field.grid || !grid_field.grid.get_field(fieldname)) {
					return;
				}
				frm.set_query(fieldname, grid_fieldname, ACTIVE_EMPLOYEE_QUERY);
			} else {
				frm.set_query(entry, ACTIVE_EMPLOYEE_QUERY);
			}
		} catch (e) {
			// Never let a stale entry in the map break the form.
			console.warn(
				"employee_active_filter: could not apply filter",
				frm.doctype,
				entry,
				e
			);
		}
	});
}

// Exposed so the map can be inspected from the console and asserted in tests.
employee_self_service.EMPLOYEE_ACTIVE_FILTERS = EMPLOYEE_ACTIVE_FILTERS;
employee_self_service.apply_employee_active_filters = apply_employee_active_filters;

$(document).on("app_ready", function () {
	Object.keys(EMPLOYEE_ACTIVE_FILTERS).forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			onload: apply_employee_active_filters,
		});
	});
});
