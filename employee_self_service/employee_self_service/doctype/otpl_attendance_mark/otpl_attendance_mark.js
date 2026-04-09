// Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('OTPL Attendance Mark', {
    refresh: function (frm) {
        // Load dynamic options for staff_type and location
        load_field_options(frm);
        set_employee_query(frm);

        // Color-code rows
        if (frm.doc.employees && frm.doc.employees.length > 0) {
            apply_row_colors(frm);
        }
    },

    fetch_employees: function (frm) {
        fetch_employees(frm);
    },

    mark_all_present: function (frm) {
        set_all_status(frm, 'Present');
    },

    mark_all_absent: function (frm) {
        set_all_status(frm, 'Absent');
    },

    staff_type: function (frm) {
        frm.set_value('employees', []);
        set_employee_query(frm);
    },

    location: function (frm) {
        frm.set_value('employees', []);
        set_employee_query(frm);
    },

    date: function (frm) {
        frm.set_value('employees', []);
    },

    employee: function (frm) {
        frm.set_value('employees', []);
    }
});

function load_field_options(frm) {
    frappe.call({
        method: 'employee_self_service.employee_self_service.doctype.otpl_attendance_mark.otpl_attendance_mark.get_field_options',
        callback: function (r) {
            if (r.message) {
                var staff_options = [''].concat(r.message.staff_types);
                var location_options = [''].concat(r.message.locations);
                frm.set_df_property('staff_type', 'options', staff_options);
                frm.set_df_property('location', 'options', location_options);
            }
        }
    });
}

function set_employee_query(frm) {
    frm.set_query('employee', function () {
        var filters = { status: 'Active' };
        if (frm.doc.staff_type) filters.staff_type = frm.doc.staff_type;
        if (frm.doc.location) filters.location = frm.doc.location;
        if (frm.doc.company) filters.company = frm.doc.company;
        return { filters: filters };
    });
}

function fetch_employees(frm) {
    if (!frm.doc.date) {
        frappe.msgprint(__('Please select a Date'));
        return;
    }
    if (!frm.doc.staff_type && !frm.doc.employee) {
        frappe.msgprint(__('Please select Staff Type or Employee'));
        return;
    }
    if (!frm.doc.location && !frm.doc.employee) {
        frappe.msgprint(__('Please select Location or Employee'));
        return;
    }

    frappe.call({
        method: 'employee_self_service.employee_self_service.doctype.otpl_attendance_mark.otpl_attendance_mark.fetch_employees',
        args: {
            staff_type: frm.doc.staff_type || '',
            location: frm.doc.location || '',
            date: frm.doc.date,
            company: frm.doc.company || '',
            employee: frm.doc.employee || ''
        },
        freeze: true,
        freeze_message: __('Fetching employees...'),
        callback: function (r) {
            if (r.message) {
                frm.clear_table('employees');
                r.message.forEach(function (emp) {
                    var row = frm.add_child('employees');
                    row.employee = emp.employee;
                    row.employee_name = emp.employee_name;
                    row.department = emp.department;
                    row.designation = emp.designation;
                    row.current_status = emp.current_status;
                    row.current_attendance = emp.current_attendance;
                    row.new_status = '';
                });
                frm.refresh_field('employees');
                frm.dirty();
                apply_row_colors(frm);

                var total = r.message.length;
                var marked = r.message.filter(e => e.current_status !== 'Not Marked').length;
                var not_marked = total - marked;

                frappe.show_alert({
                    message: __('Found {0} employees. {1} already marked, {2} not marked. Save and Submit to mark attendance.', [total, marked, not_marked]),
                    indicator: 'blue'
                });
            }
        }
    });
}

function set_all_status(frm, status) {
    if (!frm.doc.employees || frm.doc.employees.length === 0) {
        frappe.msgprint(__('Please fetch employees first'));
        return;
    }

    frm.doc.employees.forEach(function (row) {
        if (row.current_status !== status) {
            frappe.model.set_value(row.doctype, row.name, 'new_status', status);
        }
    });
    frm.refresh_field('employees');
    frm.dirty();
    frappe.show_alert({
        message: __('Set all to {0}. Save and Submit to apply.', [status]),
        indicator: status === 'Present' ? 'green' : 'red'
    });
}

function apply_row_colors(frm) {
    setTimeout(function () {
        if (!frm.fields_dict.employees || !frm.fields_dict.employees.grid) return;
        frm.fields_dict.employees.grid.grid_rows.forEach(function (grid_row) {
            var status = grid_row.doc.current_status;
            var $row = $(grid_row.row);
            $row.css('background-color', '');

            if (status === 'Present') {
                $row.css('background-color', '#d4edda');
            } else if (status === 'Absent') {
                $row.css('background-color', '#f8d7da');
            } else if (status === 'Half Day') {
                $row.css('background-color', '#fff3cd');
            } else if (status === 'Not Marked') {
                $row.css('background-color', '#e2e3e5');
            }
        });
    }, 300);
}
