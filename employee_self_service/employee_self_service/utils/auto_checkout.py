import frappe
from frappe.utils import today, now_datetime, get_datetime


def auto_checkout_site_employees():
	"""
	Auto checkout employees at 9:00 PM if:
	1. Employee checked in today
	2. Employee location is "Site" in employee master
	3. Employee hasn't checked out yet
	"""
	try:
		# Get all employees with location = "Site"
		site_employees = frappe.get_all(
			"Employee",
			filters={"location": "Site", "status": "Active"},
			fields=["name", "employee_name"]
		)
		
		if not site_employees:
			frappe.logger().info("No site employees found for auto checkout")
			return
		
		employee_list = [emp.name for emp in site_employees]
		
		# Get today's check-in records for site employees
		today_date = today()
		
		checkins = frappe.db.sql("""
			SELECT 
				employee,
				MAX(time) as last_checkin_time,
				MAX(CASE WHEN log_type = 'IN' THEN time END) as last_in_time,
				MAX(CASE WHEN log_type = 'OUT' THEN time END) as last_out_time
			FROM `tabEmployee Checkin`
			WHERE 
				employee IN %(employees)s
				AND DATE(time) = %(today)s
			GROUP BY employee
		""", {
			"employees": employee_list,
			"today": today_date
		}, as_dict=True)
		
		checkout_count = 0
		
		for checkin in checkins:
			# Check if last check-in is "IN" and there's no checkout after it
			if checkin.last_in_time and (not checkin.last_out_time or checkin.last_in_time > checkin.last_out_time):
				# Create auto checkout entry
				try:
					checkout_doc = frappe.get_doc({
						"doctype": "Employee Checkin",
						"employee": checkin.employee,
						"log_type": "OUT",
						"time": now_datetime(),
						"reason": "Auto checkout at 9:00 PM for Site location employee"
					})
					checkout_doc.insert(ignore_permissions=True)
					checkout_count += 1
					
					frappe.logger().info(
						f"Auto checkout created for employee {checkin.employee}"
					)
				except Exception as e:
					frappe.logger().error(
						f"Error creating auto checkout for employee {checkin.employee}: {str(e)}"
					)
		
		if checkout_count > 0:
			frappe.log_error(title="Auto Checkout Completed",message=f"Auto checkout completed. {checkout_count} employees checked out automatically.")
		else:
			frappe.logger().info("No employees required auto checkout today")
			
	except Exception as e:
		frappe.log_error(title="Auto Checkout Error",message=frappe.get_traceback())
