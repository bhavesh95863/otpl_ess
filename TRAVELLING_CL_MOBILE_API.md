# Travelling CL — Mobile Integration Guide

This document is the contract for the **mobile app** to implement the *Travelling CL /
out-of-location check-in* feature. The backend (doctypes, gating, approval
sequencing, auto-reject, holiday CL credit, external sync) is already built in the
`employee_self_service` app. The app only needs to call the endpoints below and
implement the described UX.

> **Changelog (latest revision)**
> - Travelling CL now has an optional **Purpose** (from the new *Travelling Purpose*
>   master — list it via `get_travelling_purposes`) and an optional **attachment**.
> - The old `reason` field is renamed to **`additional_notes`** (optional).
> - The manager may **edit the from/to dates** when approving (`set_travelling_cl_status`).
> - The **pending-approval count** now includes a `travelling_cl` key (in `total`).
> - New manager **"Approved" tab** endpoint: `get_travelling_cl_approved_list`.
> - Approving / rejecting a Travelling CL now **cascades to its out-of-location
>   check-ins** — they are auto-approved / auto-rejected server-side.

---

## 1. Concept

Certain **staff types** (configured per **ESS Location** — e.g. Manager, Staff) may
only **check in / check out from OUT OF LOCATION** after they have raised a
**Travelling CL** (a travelling request) and it is being / has been approved.

- **Site Workers are exempt** — they already have travelling activated; the app
  should keep their current behaviour unchanged.
- Staff types **not** enabled at a location are unaffected (existing behaviour).
- **Approving the Travelling CL auto-approves** its out-of-location check-ins
  (full approval + attendance processing) — the manager does **not** need to
  approve each check-in separately.
- If the Travelling CL is **rejected**, its out-of-location check-ins are
  **auto-rejected** by the backend.
- An approved out-of-location check-in on a **qualifying holiday** grants the
  employee **+1 Casual Leave** — handled entirely by a nightly backend job; the
  app does nothing for this.

---

## 2. Response envelope

All ESS mobile endpoints return the standard envelope:

```json
{ "http_status_code": 200, "message": "success", "data": <payload> }
```

- `http_status_code`: **200** success, **500** error.
- On error, `message` is the human-readable reason (show it to the user).
- `data` is the payload described per endpoint below (object or array).

All endpoints require the usual ESS mobile auth (same headers/token as other
`employee_self_service.mobile.v1.*` calls).

---

## 3. Endpoints

Base method path prefix: `employee_self_service.mobile.v1.travelling_cl.`
Call as: `POST /api/method/<full.method.path>` (or GET where noted).

### 3.1 `is_out_of_location_enabled` — GET
Show/hide the travelling flow for the logged-in employee.

**Request:** none.

**Response `data`:**
```json
{ "enabled": true, "site_worker": false, "staff_type": "Manager", "location": "Noida" }
```
- `enabled` — true if this employee may do out-of-location punches (either a
  Site Worker, or their staff type is travelling-enabled at their location).
- `site_worker` — true if they are a Site Worker (exempt; no Travelling CL needed).

**App behaviour:** if `enabled` is false, out-of-location check-in should not be
offered. If `enabled && !site_worker`, the Travelling CL flow applies.

### 3.2 `get_travelling_purposes` — GET
List the **Travelling Purpose** master values for the Purpose dropdown.

**Response `data`:** `[ { "purpose": "Client Visit" }, { "purpose": "Official Work" } ]`

### 3.3 `apply_travelling_cl` — POST
Raise a Travelling CL for the logged-in employee.

**Request body:**
```json
{
  "from_date": "2026-09-10",
  "to_date": "2026-09-12",
  "purpose": "Client Visit",
  "additional_notes": "Carry laptop",
  "attachment": "/files/ticket.pdf"
}
```
- Dates `YYYY-MM-DD`. `from_date` must be **today or later** (server enforced).
- `purpose` — **optional**; one of `get_travelling_purposes` (a Travelling Purpose).
- `additional_notes` — **optional** free text (this replaces the old `reason` field).
- `attachment` — **optional** file URL. Upload the file first via the app's existing
  file-upload endpoint, then pass the returned URL here.
- Approver (Report To / External Report To) is resolved **server-side** — the app
  does not send it.

**Response `data`:** `{ "name": "TCL00001", "status": "Pending" }`

**Errors (500):** past `from_date`, `to_date` before `from_date`, date overlap with
an existing non-rejected Travelling CL, or no approver found — show `message`.

### 3.4 `get_my_travelling_cl_list` — GET
The employee's own Travelling CLs (newest first).

**Query params (optional):** `start` (default 0), `page_length` (default 20),
`status` (`Pending`|`Approved`|`Rejected`|`Cancelled`).

**Response `data`:** array of
```json
{ "name": "TCL00001", "from_date": "2026-09-10", "to_date": "2026-09-12",
  "number_of_days": 3, "purpose": "Client Visit", "additional_notes": "...",
  "attachment": "/files/ticket.pdf", "status": "Pending",
  "approver_name": "Amit Oberoi", "approver_mobile_no": "98..." }
```

### 3.5 `get_travelling_cl_approvals` — GET  *(manager)*
Pending Travelling CLs awaiting the logged-in manager (internal approver). **This
is the queue the manager must clear before approving the related check-ins.**

**Query params (optional):** `start`, `page_length`.

**Response `data`:** array of
```json
{ "name": "TCL00001", "employee": "EMP/0004", "employee_name": "Nidhi ...",
  "applicant_mobile_no": "98...", "from_date": "2026-09-10", "to_date": "2026-09-12",
  "number_of_days": 3, "purpose": "Client Visit", "additional_notes": "...",
  "attachment": "/files/ticket.pdf", "status": "Pending" }
```

### 3.6 `set_travelling_cl_status` — POST  *(manager)*
Manager approves / rejects a Travelling CL. **The manager may also change the
travelling date range at approval** — pass `from_date` / `to_date` to override; omit
them to keep what the employee requested.

**Request body:**
```json
{ "name": "TCL00001", "status": "Approved", "remarks": "",
  "from_date": "2026-09-11", "to_date": "2026-09-11" }
```
- `status` must be `Approved` or `Rejected`.
- `from_date` / `to_date` — **optional**; default to the requested dates. When
  changed, `number_of_days` and the gated check-in date range update accordingly.
- Only the resolved internal approver (`report_to`) may act (server enforced).
- **Rejecting** auto-rejects the linked out-of-location check-ins (backend).

**Response `data`:**
```json
{ "name": "TCL00001", "status": "Approved", "from_date": "2026-09-11",
  "to_date": "2026-09-11", "number_of_days": 1 }
```

### 3.7 `get_travelling_cl_approved_list` — GET  *(manager)*
Travelling CLs the logged-in manager has **Approved** — the manager's separate
"Approved" tab (same idea as OTPL Leave / Expense / Travel approved lists). Includes
both internal Travelling CL and external Travelling CL Pull, newest first.

**Query params (optional):** `start` (default 0), `page_length` (default 10).

**Response `data`:** array of
```json
{ "name": "TCL00001", "doctype": "Travelling CL", "employee": "EMP/0004",
  "employee_name": "Nidhi ...", "department": "...", "from_date": "2026-09-10",
  "to_date": "2026-09-12", "number_of_days": 3, "purpose": "Client Visit",
  "additional_notes": "...", "attachment": "/files/ticket.pdf", "status": "Approved",
  "report_to": "EMP/0060", "remarks": "..." }
```
- `doctype` is `Travelling CL` (internal) or `Travelling CL Pull` (external). Pull
  rows have no `attachment`.

---

## 4. Out-of-location check-in — creation gate

When the app creates an out-of-location punch (the existing check-in create call,
`employee_self_service.mobile.v1.ess.create_employee_log`), the backend blocks it
for an enabled staff type **if no Travelling CL has been applied** for that date.

**Failure response (500):**
```
Out-of-location check-in / check-out is not allowed without an applied Travelling CL.
Please raise a Travelling CL for 2026-09-11 first.
```

**App behaviour:** on this error, route the user to **apply a Travelling CL**
(3.3) for that date, then let them retry the punch. "Applied" means a **Pending or
Approved** Travelling CL covering the date — the punch can be created as soon as the
request exists (it still needs manager approval afterwards).

> A punch **at** the location, followed by a travelling request, followed by a
> checkout **out of** location, is allowed — the checkout is the out-of-location
> punch and is gated the same way.

---

## 5. Manager approving the check-in — sequencing

The check-in approval endpoint is unchanged in path:
`employee_self_service.mobile.v1.approvals.otpl_approval.approve_employee_checkin`
(**POST**, body `{ "name": "<checkin>", "time": "<optional>" }`).

It now enforces **"approve the Travelling CL first"**. When the manager tries to
approve an out-of-location check-in whose covering Travelling CL is **still
Pending**, it returns **200** with a redirect payload instead of approving:

**Response `data` (blocked):**
```json
{
  "redirect_to": "travelling_cl",
  "travelling_cl": "TCL00001",
  "travelling_cl_status": "Pending",
  "checkin": "EMP-CKIN-..."
}
```
with `message`: *"Approve the Travelling CL TCL00001 first, then approve this check-in."*

**App behaviour (manager):**
1. Call `approve_employee_checkin`.
2. If `data.redirect_to == "travelling_cl"`, open Travelling CL `data.travelling_cl`
   and approve it via `set_travelling_cl_status` (3.6).
3. **Approving the Travelling CL auto-approves the check-in** (and processes its
   attendance) server-side — the app should just **refresh** the check-in; it does
   **not** need to call `approve_employee_checkin` again.

If the Travelling CL is **Rejected**, the check-in is already auto-rejected
server-side (no redirect); the manager will simply see it as rejected.

> So the simplest manager flow is: approve/reject the **Travelling CL** — the
> linked out-of-location check-ins follow automatically. The check-in approval
> screen is only a fallback entry point that redirects to the Travelling CL.

**Reject** is unchanged: `reject_employee_checkin` (POST `{ "name": "<checkin>" }`).

---

## 5b. Pending-approval badge count

The existing pending-approvals count endpoint
(`employee_self_service.mobile.v1.approvals.otpl_approval.get_pending_approval_counts`,
GET) now includes Travelling CL. Its `data` gains a `travelling_cl` key and the
`total` includes it — exactly like `leave`, `expense`, `travel`, etc.:

```json
{ "leave": 2, "expense": 1, "checkin": 0, "checkout": 0,
  "site_expense_pending": 0, "travel": 1, "travelling_cl": 3, "total": 7 }
```

The app should surface `travelling_cl` in the manager's pending badge and include it
wherever the aggregate `total` is shown (no separate call needed).

## 6. Holiday CL credit — informational only

If an enabled staff type has an **approved** out-of-location check-in on a
**qualifying holiday**, the backend nightly job credits **+1 Casual Leave** to the
employee (idempotent). **No app action** is required — the employee's CL balance
(shown wherever leave balance is displayed) simply increases.

---

## 7. Suggested app flows

**Employee — travelling + out-of-location punch**
1. On the check-in screen, call `is_out_of_location_enabled`.
2. If enabled and the punch is out of location, ensure a Travelling CL exists for
   the date (offer `apply_travelling_cl` (3.3) if the create-punch call returns the
   Section-4 error).
3. After the request exists, the punch is created (pending manager approval).

**Manager — approvals**
1. **Pending tab:** show the queue via `get_travelling_cl_approvals` (3.5).
2. Approve/reject via `set_travelling_cl_status` (3.6).
3. **Approved tab:** show already-approved requests via
   `get_travelling_cl_approved_list` (3.7) — the same two-tab layout as OTPL Leave /
   Expense / Travel.
4. In the check-in approval queue, if `approve_employee_checkin` returns
   `redirect_to: travelling_cl`, approve that request first, then re-approve the
   check-in.

---

## 8. Field / status reference

- **Travelling CL** `status`: `Pending` → `Approved` / `Rejected` (also `Cancelled`).
- Dates are `YYYY-MM-DD`. `number_of_days` = inclusive day count.
- Only **internal-approver** employees are actioned from the app via
  `set_travelling_cl_status`; **external-report-to** employees are approved on the
  external ERP and their status syncs back automatically (no app work).

---

*Backend reference (for questions): `doctype/travelling_cl`,
`mobile/v1/travelling_cl.py`, `mobile/v1/approvals/otpl_approval.py`
(`approve_employee_checkin`), `utils/otpl_attendance.py`
(`enforce_travelling_cl_gate`), `utils/travelling_cl_credit.py`.*
