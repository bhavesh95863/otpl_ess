// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Attendance Discrepancy Report"] = {
	filters: [
		{
			fieldname: "date",
			label: __("Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "discrepancy_type",
			label: __("Discrepancy Type"),
			fieldtype: "Select",
			options: [
				"",
				"Absent - Missing Check-out",
				"Absent - Missing Check-in",
				"Absent Despite Check-in & Check-out",
				"Attendance Not Processed",
				"Attendance Creation Failed",
				"Pending Check-in Approval",
			],
		},
		{
			fieldname: "location",
			label: __("Location"),
			fieldtype: "Data",
		},
		{
			fieldname: "staff_type",
			label: __("Staff Type"),
			fieldtype: "Select",
			options: ["", "Worker", "Field", "Driver", "Staff"],
		},
	],
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "discrepancy_type" && data && data.discrepancy_type) {
			let color_map = {
				"Absent - Missing Check-out": "#d97706",
				"Absent - Missing Check-in": "#d97706",
				"Absent Despite Check-in & Check-out": "#dc2626",
				"Attendance Not Processed": "#dc2626",
				"Attendance Creation Failed": "#dc2626",
				"Pending Check-in Approval": "#ca8a04",
			};
			let color = color_map[data.discrepancy_type] || "gray";
			value = `<span style="color:${color};font-weight:600;">${value}</span>`;
		}
		return value;
	},
};
