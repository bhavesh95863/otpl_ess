import frappe
import datetime
from frappe import _
from frappe.utils import flt, cint, cstr, getdate, get_datetime
from typing import Any, Literal, Optional, TypeVar


def get_timedelta(time=None):
	"""Convert time string or datetime.time to datetime.timedelta (v12 compatible)"""
	if not time:
		return datetime.timedelta()
	if isinstance(time, datetime.timedelta):
		return time
	if isinstance(time, datetime.time):
		return datetime.timedelta(hours=time.hour, minutes=time.minute, seconds=time.second, microseconds=time.microsecond)
	if isinstance(time, str):
		try:
			parts = time.split(":")
			hours = cint(parts[0]) if len(parts) > 0 else 0
			minutes = cint(parts[1]) if len(parts) > 1 else 0
			seconds = flt(parts[2]) if len(parts) > 2 else 0
			return datetime.timedelta(hours=hours, minutes=minutes, seconds=seconds)
		except Exception:
			return datetime.timedelta()
	return datetime.timedelta()


def get_info_based_on_role(role, field="email", ignore_permissions=False):
	"""Get information of all users that have been assigned this role"""
	users = frappe.get_list(
		"Has Role",
		filters={"role": role, "parenttype": "User"},
		parent_doctype="User",
		fields=["parent as user_name"],
		ignore_permissions=ignore_permissions,
	)

	return get_user_info(users, field)

def get_user_info(users, field="email"):
	"""Fetch details about users for the specified field"""
	info_list = []
	for user in users:
		user_info, enabled = frappe.db.get_value("User", user.get("user_name"), [field, "enabled"])
		if enabled and user_info not in ["admin@example.com", "guest@example.com"]:
			info_list.append(user_info)
	return info_list

def cast(fieldtype, value=None):
	"""Cast the value to the Python native object of the Frappe fieldtype provided.
	If value is None, the first/lowest value of the `fieldtype` will be returned.
	If value can't be cast as fieldtype due to an invalid input, None will be returned.

	Mapping of Python types => Frappe types:
	        * str => ("Data", "Text", "Small Text", "Long Text", "Text Editor", "Select", "Link", "Dynamic Link")
	        * float => ("Currency", "Float", "Percent")
	        * int => ("Int", "Check")
	        * datetime.datetime => ("Datetime",)
	        * datetime.date => ("Date",)
	        * datetime.time => ("Time",)
	"""
	if fieldtype in ("Currency", "Float", "Percent"):
		value = flt(value)

	elif fieldtype in ("Int", "Check"):
		value = cint(sbool(value))

	elif fieldtype in (
		"Data",
		"Text",
		"Small Text",
		"Long Text",
		"Text Editor",
		"Select",
		"Link",
		"Dynamic Link",
	):
		value = cstr(value)

	elif fieldtype == "Date":
		if value:
			value = getdate(value)
		else:
			value = datetime.datetime(1, 1, 1).date()

	elif fieldtype == "Datetime":
		if value:
			value = get_datetime(value)
		else:
			value = datetime.datetime(1, 1, 1)

	elif fieldtype == "Time":
		value = get_timedelta(value)

	return value

def sbool(x):
	"""Convert str object to Boolean if possible.

	Example:
	        "true" becomes True
	        "1" becomes True
	        "{}" remains "{}"

	Args:
	        x (str): String to be converted to Bool

	Return Boolean or x.
	"""
	try:
		val = x.lower()
		if val in ("true", "1"):
			return True
		elif val in ("false", "0"):
			return False
		return x
	except Exception:
		return x