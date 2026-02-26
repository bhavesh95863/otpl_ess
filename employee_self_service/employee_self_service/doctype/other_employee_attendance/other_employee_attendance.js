// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('Other Employee Attendance', {
	refresh: function(frm) {
		frm.trigger("reporting_manager_set_query");
		frm.trigger("employee_set_query");
    },
	reporting_manager: function(frm) {
		frm.set_value("employee", "");
		frm.trigger("employee_set_query");
	},
	reporting_manager_set_query: function(frm) {
		frm.set_query("reporting_manager", function() {
            return {
                filters: {
                    staff_type: "Worker",
                    location: "Site",
                    is_team_leader: 1
                }
            };
        });
	},
	employee_set_query: function(frm) {
		frm.set_query("employee", function() {
            return {
                filters: {
                    reports_to: frm.doc.reporting_manager,
                    phone_not_working: 1,
                    status: "Active"
                }
            };
        });
	}
});
