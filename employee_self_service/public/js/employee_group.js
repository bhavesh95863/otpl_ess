frappe.listview_settings['Employee Group'] = {
	onload(listview) {
		frappe.set_route('List', 'OTPL Employee Group');
	}
};
