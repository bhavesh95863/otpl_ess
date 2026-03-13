# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import math
import frappe
from frappe.utils import today, cint
from frappe.model.document import Document


class NoTeamLeaderError(Document):
	def before_save(self):
		if self.employee and not self.employee_name:
			self.employee_name = frappe.db.get_value("Employee", self.employee, "employee_name") or ""

	def after_insert(self):
		try:
			if self.employee and not self.employee_name:
				self.employee_name = frappe.db.get_value("Employee", self.employee, "employee_name") or ""
				frappe.db.set_value("No Team Leader Error", self.name, "employee_name", self.employee_name, update_modified=False)
			self._populate_reporting_manager_info()
			self._populate_nearest_team_leader_info()
		except Exception:
			frappe.log_error(title="No Team Leader Error - after_insert failed", message=frappe.get_traceback())

	# ------------------------------------------------------------------
	# Internal helpers
	# ------------------------------------------------------------------

	def _haversine(self, lat1, lon1, lat2, lon2):
		"""Return distance in metres between two coordinates."""
		R = 6371000
		lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
		dlat = lat2 - lat1
		dlon = lon2 - lon1
		a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
		c = 2 * math.asin(math.sqrt(a))
		return R * c

	def _parse_location(self, location_str):
		"""Parse 'lat,lon' string. Returns (lat, lon) floats or (None, None)."""
		try:
			parts = location_str.split(",")
			return float(parts[0].strip()), float(parts[1].strip())
		except (ValueError, IndexError, AttributeError):
			return None, None

	def _populate_reporting_manager_info(self):
		"""Find the reporting manager's most-recent location for today up to
		the error time and store distance + coords on this record."""
		try:
			emp_lat = float(self.latitude)
			emp_lon = float(self.longitude)
		except (ValueError, TypeError):
			return

		emp = frappe.db.get_value(
			"Employee",
			self.employee,
			["external_reporting_manager", "reports_to", "external_report_to"],
			as_dict=1,
		)
		if not emp:
			return

		is_external = cint(emp.external_reporting_manager) == 1

		if is_external:
			manager_id = emp.external_report_to
			if not manager_id:
				return
			# Fetch manager name from Employee doc if it exists, else fallback
			manager_name = frappe.db.get_value("Employee Pull", manager_id, "employee_name") or manager_id
			row = frappe.db.sql("""
				SELECT ll.location, ll.datetime AS checkin_time
				FROM `tabLeader Location` ll
				WHERE ll.employee = %(manager)s
				  AND ll.datetime >= %(today)s
				  AND ll.datetime <= %(error_time)s
				  AND ll.location IS NOT NULL
				  AND ll.location != ''
				ORDER BY ll.datetime DESC
				LIMIT 1
			""", {"manager": manager_id, "today": today(), "error_time": self.datetime}, as_dict=1)
		else:
			manager_id = emp.reports_to
			if not manager_id:
				return
			row = frappe.db.sql("""
				SELECT ec.location, ec.time AS checkin_time
				FROM `tabEmployee Checkin` ec
				WHERE ec.employee = %(manager)s
				  AND ec.time >= %(today)s
				  AND ec.time <= %(error_time)s
				  AND ec.location IS NOT NULL
				  AND ec.location != ''
				ORDER BY ec.time DESC
				LIMIT 1
			""", {"manager": manager_id, "today": today(), "error_time": self.datetime}, as_dict=1)

			manager_name = frappe.db.get_value("Employee", manager_id, "employee_name") or manager_id
		update = {
			"reporting_manager": manager_id,
			"reporting_manager_name": manager_name,
		}

		if row:
			m_lat, m_lon = self._parse_location(row[0].location)
			if m_lat is not None:
				distance = self._haversine(emp_lat, emp_lon, m_lat, m_lon)
				update.update({
					"reporting_manager_checkin": row[0].checkin_time,
					"reporting_manager_latitude": str(m_lat),
					"reporting_manager_longitude": str(m_lon),
					"reporting_manager_distance": str(round(distance, 2)) + " m",
				})

		frappe.db.set_value("No Team Leader Error", self.name, update, update_modified=False)

	def _populate_nearest_team_leader_info(self):
		"""Find the closest team leader (internal or external) for today up to
		the error time and store distance + coords on this record."""
		try:
			emp_lat = float(self.latitude)
			emp_lon = float(self.longitude)
		except (ValueError, TypeError):
			return

		best = None  # dict with distance, employee, employee_name, location, checkin_time

		# Internal: Employee Checkin
		internal_rows = frappe.db.sql("""
			SELECT ec.employee, e.employee_name, ec.location, ec.time AS checkin_time
			FROM `tabEmployee Checkin` ec
			INNER JOIN `tabEmployee` e ON e.name = ec.employee
			WHERE e.is_team_leader = 1
			  AND e.status = 'Active'
			  AND ec.time >= %(today)s
			  AND ec.time <= %(error_time)s
			  AND ec.location IS NOT NULL
			  AND ec.location != ''
			ORDER BY ec.time DESC
		""", {"today": today(), "error_time": self.datetime}, as_dict=1)

		seen = set()
		for row in internal_rows:
			if row.employee in seen:
				continue
			seen.add(row.employee)
			m_lat, m_lon = self._parse_location(row.location)
			if m_lat is None:
				continue
			dist = self._haversine(emp_lat, emp_lon, m_lat, m_lon)
			if best is None or dist < best["distance"]:
				best = {
					"distance": dist,
					"employee": row.employee,
					"employee_name": row.employee_name,
					"location": row.location,
					"checkin_time": row.checkin_time,
				}

		# External: Leader Location
		external_rows = frappe.db.sql("""
			SELECT ll.employee, ll.employee_name, ll.location, ll.datetime AS checkin_time
			FROM `tabLeader Location` ll
			WHERE ll.datetime >= %(today)s
			  AND ll.datetime <= %(error_time)s
			  AND ll.location IS NOT NULL
			  AND ll.location != ''
			ORDER BY ll.datetime DESC
		""", {"today": today(), "error_time": self.datetime}, as_dict=1)

		for row in external_rows:
			if row.employee in seen:
				continue
			seen.add(row.employee)
			m_lat, m_lon = self._parse_location(row.location)
			if m_lat is None:
				continue
			dist = self._haversine(emp_lat, emp_lon, m_lat, m_lon)
			if best is None or dist < best["distance"]:
				best = {
					"distance": dist,
					"employee": row.employee,
					"employee_name": row.employee_name,
					"location": row.location,
					"checkin_time": row.checkin_time,
				}

		if not best:
			return

		m_lat, m_lon = self._parse_location(best["location"])
		frappe.db.set_value("No Team Leader Error", self.name, {
			"nearest_team_leader": best["employee"],
			"nearest_team_leader_name": best.get("employee_name") or best["employee"],
			"nearest_team_leader_checkin": best["checkin_time"],
			"nearest_team_leader_latitude": str(m_lat),
			"nearest_team_leader_longitude": str(m_lon),
			"nearest_team_leader_distance": str(round(best["distance"], 2)) + " m",
		}, update_modified=False)


@frappe.whitelist()
def get_all_leaders_with_distance(docname):
	"""
	Fetch all internal (Employee Checkin) and external (Leader Location) team leaders
	that had records for today up to the error datetime, and calculate their distance
	from the stored employee location.
	"""
	doc = frappe.get_doc("No Team Leader Error", docname)

	try:
		user_lat = float(doc.latitude)
		user_lon = float(doc.longitude)
	except (ValueError, TypeError):
		frappe.throw("Invalid latitude or longitude stored in this record.")

	error_time = doc.datetime

	def haversine(lat1, lon1, lat2, lon2):
		"""Return distance in metres between two coordinates."""
		R = 6371000
		lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
		dlat = lat2 - lat1
		dlon = lon2 - lon1
		a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
		c = 2 * math.asin(math.sqrt(a))
		return R * c

	results = []
	seen = set()

	# Internal team leaders — most-recent checkin for today up to the error time
	internal_checkins = frappe.db.sql("""
		SELECT ec.employee, ec.location, ec.time, e.employee_name
		FROM `tabEmployee Checkin` ec
		INNER JOIN `tabEmployee` e ON e.name = ec.employee
		WHERE e.is_team_leader = 1
		  AND e.status = 'Active'
		  AND ec.time >= %(today)s
		  AND ec.time <= %(error_time)s
		  AND ec.location IS NOT NULL
		  AND ec.location != ''
		ORDER BY ec.time DESC
	""", {"today": today(), "error_time": error_time}, as_dict=1)

	for checkin in internal_checkins:
		if checkin.employee in seen:
			continue
		seen.add(checkin.employee)
		try:
			parts = checkin.location.split(",")
			c_lat = float(parts[0].strip())
			c_lon = float(parts[1].strip())
		except (ValueError, IndexError, AttributeError):
			c_lat = c_lon = None

		if c_lat is None:
			results.append({
				"employee": checkin.employee,
				"employee_name": checkin.employee_name,
				"checkin_time": str(checkin.time),
				"location": checkin.location or "",
				"distance": None,
				"within_range": False,
				"type": "Internal",
				"note": "Invalid location format",
			})
			continue

		distance = haversine(user_lat, user_lon, c_lat, c_lon)
		results.append({
			"employee": checkin.employee,
			"employee_name": checkin.employee_name,
			"checkin_time": str(checkin.time),
			"location": checkin.location,
			"distance": round(distance, 2),
			"within_range": distance <= 100,
			"type": "Internal",
			"note": "",
		})

	# External team leaders — most-recent Leader Location for today up to error time
	external_checkins = frappe.db.sql("""
		SELECT ll.employee, ll.location, ll.datetime, ll.employee_name
		FROM `tabLeader Location` ll
		WHERE ll.datetime >= %(today)s
		  AND ll.datetime <= %(error_time)s
		  AND ll.location IS NOT NULL
		  AND ll.location != ''
		ORDER BY ll.datetime DESC
	""", {"today": today(), "error_time": error_time}, as_dict=1)

	for checkin in external_checkins:
		if checkin.employee in seen:
			continue
		seen.add(checkin.employee)
		try:
			parts = checkin.location.split(",")
			c_lat = float(parts[0].strip())
			c_lon = float(parts[1].strip())
		except (ValueError, IndexError, AttributeError):
			c_lat = c_lon = None

		if c_lat is None:
			results.append({
				"employee": checkin.employee,
				"employee_name": checkin.employee_name,
				"checkin_time": str(checkin.datetime),
				"location": checkin.location or "",
				"distance": None,
				"within_range": False,
				"type": "External",
				"note": "Invalid location format",
			})
			continue

		distance = haversine(user_lat, user_lon, c_lat, c_lon)
		results.append({
			"employee": checkin.employee,
			"employee_name": checkin.employee_name,
			"checkin_time": str(checkin.datetime),
			"location": checkin.location,
			"distance": round(distance, 2),
			"within_range": distance <= 100,
			"type": "External",
			"note": "",
		})

	# Sort: within-range first, then by distance ascending
	results.sort(key=lambda x: (not x["within_range"], x["distance"] if x["distance"] is not None else float("inf")))
	return results
