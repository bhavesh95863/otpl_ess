# -*- coding: utf-8 -*-
# Copyright (c) 2025, Nesscale Solutions Private Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
from contextlib import contextmanager

import frappe


@contextmanager
def as_system_user(user="Administrator"):
	"""Run the enclosed block as ``user`` and restore the caller afterwards.

	For system side effects triggered by a user action — e.g. approving an
	OTPL Leave auto-creates Leave Applications, whose submit makes ERPNext
	insert Leave Ledger Entries WITHOUT ignore_permissions. An approver who
	holds no HR role then gets a PermissionError deep in ERPNext code we
	cannot pass flags into, and approvers must not be given those
	permissions on the desk.

	frappe.set_user() is not used directly because it mutates the live
	session in place (sid, session data, form_dict); instead the session
	object is swapped and the original put back untouched.
	"""
	local = frappe.local
	if local.session and local.session.user == user:
		yield
		return

	saved = {
		"session": local.session,
		"role_permissions": getattr(local, "role_permissions", {}),
		"user_perms": getattr(local, "user_perms", None),
		"new_doc_templates": getattr(local, "new_doc_templates", {}),
	}
	local.session = frappe._dict(user=user, sid=user, data=frappe._dict())
	local.role_permissions = {}
	local.user_perms = None
	local.new_doc_templates = {}
	try:
		yield
	finally:
		for key, value in saved.items():
			setattr(local, key, value)
