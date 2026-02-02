// Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('ESS Location', {
	refresh: function(frm) {
		set_field_requirements(frm);
	},
	
	location_depend_team_leader: function(frm) {
		set_field_requirements(frm);
	}
});

function set_field_requirements(frm) {
	if (frm.doc.location_depend_team_leader == 1) {
		frm.set_df_property('latitude', 'reqd', 0);
		frm.set_df_property('longitude', 'reqd', 0);
		frm.set_df_property('radius', 'reqd', 0);
	} else {
		frm.set_df_property('latitude', 'reqd', 1);
		frm.set_df_property('longitude', 'reqd', 1);
		frm.set_df_property('radius', 'reqd', 1);
	}
}
