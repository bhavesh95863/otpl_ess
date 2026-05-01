// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('OTPL Payroll', {
	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__('Get Employees'), () => fetch_employees(frm));
			frm.add_custom_button(__('Calculate Salary'), () => calculate_salary(frm))
				.addClass('btn-primary');
		}
		frm.add_custom_button(__('View Calculation'), () => view_calculation(frm));

		if (frm.doc.from_date && frm.doc.to_date) {
			const d = frappe.datetime;
			const days = d.get_diff(frm.doc.to_date, frm.doc.from_date) + 1;
			frm.set_value('days_in_period', days);
		}
	},

	from_date: refresh_days,
	to_date: refresh_days,

	get_employees: fetch_employees,
	calculate_payroll: calculate_salary,
});

function refresh_days(frm) {
	if (frm.doc.from_date && frm.doc.to_date) {
		const days = frappe.datetime.get_diff(frm.doc.to_date, frm.doc.from_date) + 1;
		frm.set_value('days_in_period', days);
	}
}

function fetch_employees(frm) {
	if (!frm.doc.from_date || !frm.doc.to_date) {
		frappe.msgprint(__('Please set From Date and To Date first.'));
		return;
	}
	frappe.call({
		method: 'employee_self_service.employee_self_service.doctype.otpl_payroll.otpl_payroll.get_employees',
		args: { doc: frm.doc },
		freeze: true,
		freeze_message: __('Fetching Employees...'),
		callback(r) {
			if (!r.message || !r.message.length) {
				frappe.msgprint(__('No employees match the filters.'));
				return;
			}
			frm.clear_table('employees');
			r.message.forEach((emp) => {
				const child = frm.add_child('employees');
				Object.assign(child, emp);
			});
			frm.refresh_field('employees');
			frappe.show_alert({
				message: __('{0} employees fetched. Click Calculate Salary.', [r.message.length]),
				indicator: 'green',
			});
		},
	});
}

function calculate_salary(frm) {
	if (!frm.doc.from_date || !frm.doc.to_date) {
		frappe.msgprint(__('Please set From Date and To Date first.'));
		return;
	}
	frappe.call({
		method: 'employee_self_service.employee_self_service.doctype.otpl_payroll.otpl_payroll.calculate_payroll',
		args: { doc: frm.doc },
		freeze: true,
		freeze_message: __('Calculating Salary...'),
		callback(r) {
			if (!r.message) return;
			const { rows = [], log = [] } = r.message;
			frm.clear_table('employees');
			rows.forEach((row) => {
				const child = frm.add_child('employees');
				Object.assign(child, row);
			});
			frm.refresh_field('employees');
			if (log && log.length) {
				frm.set_value('processing_log', log.join('\n'));
			}
			// validate() refreshes nets and totals when the user saves.
			frappe.show_alert({
				message: __('Calculated for {0} rows. Review and Save.', [rows.length]),
				indicator: 'green',
			});
		},
	});
}

// ---------------------------------------------------------------------------
// View Calculation dialog
// ---------------------------------------------------------------------------
function view_calculation(frm, prefill_emp) {
if (!frm.doc.from_date || !frm.doc.to_date) {
frappe.msgprint(__('Please set From Date and To Date first.'));
return;
}
const choices = (frm.doc.employees || [])
		.map((r) => r.employee)
		.filter(Boolean);
	if (!choices.length) {
		frappe.msgprint(__('No employees in the table. Run Get Employees / Calculate first.'));
		return;
	}

	const d = new frappe.ui.Dialog({
		title: __('Calculation Breakdown'),
		size: 'large',
		fields: [
			{
				fieldname: 'employee', fieldtype: 'Link', label: __('Employee'),
				options: 'Employee',
				get_query: () => ({ filters: { name: ['in', choices] } }),
				default: prefill_emp || choices[0],
},
{ fieldname: 'output', fieldtype: 'HTML' },
],
});

const render = () => {
const emp = d.get_value('employee');
if (!emp) return;
d.fields_dict.output.$wrapper.html(
`<div class="text-muted" style="padding:12px">${__('Loading...')}</div>`
);
frappe.call({
method:
'employee_self_service.employee_self_service.doctype.otpl_payroll.otpl_payroll.get_calculation_trace',
args: { doc: frm.doc, employee: emp },
callback(r) {
if (!r.message || !r.message.steps) {
d.fields_dict.output.$wrapper.html(
`<div class="text-muted">${__('No data')}</div>`
);
return;
}
const html = r.message.steps
.map((sec) => {
const rows = sec.items
.map(
([k, v]) => `
<tr>
<td style="white-space:nowrap; vertical-align:top;
           padding:4px 12px 4px 0; font-weight:500;">
${frappe.utils.escape_html(k)}
</td>
<td style="vertical-align:top; padding:4px 0;">
${frappe.utils.escape_html(String(v))}
</td>
</tr>`
)
.join('');
return `
<h5 style="margin-top:14px; margin-bottom:6px">
${frappe.utils.escape_html(sec.section)}
</h5>
<table class="table table-sm" style="font-size:12px">
<tbody>${rows}</tbody>
</table>`;
})
.join('');
d.fields_dict.output.$wrapper.html(html);
},
});
};

d.fields_dict.employee.df.onchange = render;
d.show();
render();
}

// Per-row trigger: click the small "?" indicator inside a grid row to see
// the calculation for that employee.
frappe.ui.form.on('OTPL Payroll Detail', {
employee(frm, cdt, cdn) {
// no-op; just here so the child has a registered handler
},
});

// Add a button at the top of the child grid row form (when the user opens a row)
frappe.ui.form.on('OTPL Payroll', {
onload_post_render(frm) {
const grid = frm.fields_dict.employees && frm.fields_dict.employees.grid;
if (!grid) return;
grid.wrapper.on('click', '.btn-otpl-explain', function () {
const cdn = $(this).attr('data-cdn');
const row = locals['OTPL Payroll Detail'][cdn];
if (row && row.employee) view_calculation(frm, row.employee);
});
},
});
