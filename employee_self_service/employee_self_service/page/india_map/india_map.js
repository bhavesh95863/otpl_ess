frappe.pages["india-map"].on_page_load = function (wrapper) {
	window.location.href = "/employee-map";
	return;

	frappe.ui.make_app_page({
		parent: wrapper,
		title: "India Map",
		single_column: true,
	});

	let view_mode = "Cluster";
	let all_markers = [];
	let all_employees = [];
	let total_active_employees = 0;
	let map = null;
	let cluster_layer = null;
	let plain_layer = null;
	let base_layers = {};
	let selected_employee = null;

	const $container = $(wrapper).find(".layout-main-section");
	$container.html(`
		<div class="india-map-wrapper">
			<div class="india-map-left">
				<div id="india-map-container"></div>
			</div>
			<div class="india-map-right">
				<div class="map-panel-header">
					<h4>Employee Locations</h4>
					<p class="text-muted">Live check-in tracking across OTPL &amp; TRANZ</p>
				</div>

				<div class="map-control-group">
					<label>Date</label>
					<input type="date" id="map-date" class="form-control input-sm" />
				</div>

				<div class="map-control-group">
					<label>View Mode</label>
					<select id="map-view-mode" class="form-control input-sm">
						<option value="Cluster">Cluster</option>
						<option value="Pins">Pins</option>
					</select>
				</div>

				<div class="map-control-group">
					<label>Search Employee</label>
					<div class="employee-search-wrapper">
						<input id="map-search" type="text" class="form-control input-sm"
							placeholder="Type name or ID..." autocomplete="off" />
						<div id="search-results" class="search-results-dropdown"></div>
					</div>
				</div>

				<div class="map-stats">
					<div class="stat-row">
						<span class="stat-label">Employees Checked In</span>
						<span class="stat-value">
							<strong id="stat-checked-in">0</strong>
							<span class="text-muted"> / </span>
							<span id="stat-total-employees">0</span>
						</span>
					</div>
					<div class="stat-row stat-sub">
						<span class="stat-label">
							<span class="legend-dot" style="background:#e65100"></span>
							Oberoi Thermit Pvt. Ltd.
						</span>
						<span class="stat-value" id="stat-oberoi">0</span>
					</div>
					<div class="stat-row stat-sub">
						<span class="stat-label">
							<span class="legend-dot" style="background:#1565c0"></span>
							Tranzrail
						</span>
						<span class="stat-value" id="stat-tranzrail">0</span>
					</div>
				</div>

				<div class="map-actions">
					<button class="btn btn-primary btn-sm btn-block" id="btn-refresh">
						<i class="fa fa-refresh"></i> Refresh Data
					</button>
				</div>
			</div>
		</div>
	`);

	// Set today's date as default
	$container.find("#map-date").val(frappe.datetime.get_today());

	// ── Event bindings ──────────────────────────────────────────────
	$container.find("#map-view-mode").on("change", function () {
		view_mode = $(this).val();
		render_markers();
	});

	$container.find("#map-date").on("change", function () {
		load_markers();
	});

	$container.find("#btn-refresh").on("click", function () {
		load_markers();
	});

	// ── Employee search dropdown ────────────────────────────────────
	let search_timer;
	const $search = $container.find("#map-search");
	const $results = $container.find("#search-results");

	$search.on("input", function () {
		clearTimeout(search_timer);
		const val = $(this).val().trim().toLowerCase();

		if (!val) {
			$results.hide();
			selected_employee = null;
			render_markers();
			return;
		}

		search_timer = setTimeout(() => {
			const filtered = all_employees.filter(emp =>
				(emp.employee_name || "").toLowerCase().includes(val) ||
				(emp.employee || "").toLowerCase().includes(val)
			).slice(0, 25);

			if (filtered.length) {
				$results.html(filtered.map(emp => `
					<div class="search-result-item" data-employee="${frappe.utils.escape_html(emp.employee)}">
						<strong>${frappe.utils.escape_html(emp.employee_name)}</strong>
						<span class="text-muted">${frappe.utils.escape_html(emp.employee)}</span>
					</div>
				`).join("")).show();
			} else {
				$results.html('<div class="search-result-empty">No employees found</div>').show();
			}
		}, 200);
	});

	$results.on("click", ".search-result-item", function () {
		const emp_id = $(this).data("employee");
		selected_employee = emp_id;
		const emp = all_employees.find(e => e.employee === emp_id);
		if (emp) {
			$search.val(emp.employee_name + " (" + emp.employee + ")");
		}
		$results.hide();
		render_markers();
		zoom_to_employee(emp_id);
	});

	$(document).on("click", function (e) {
		if (!$(e.target).closest(".employee-search-wrapper").length) {
			$results.hide();
		}
	});

	$search.on("keydown", function (e) {
		if (e.key === "Escape") {
			$(this).val("");
			$results.hide();
			selected_employee = null;
			render_markers();
		}
	});

	// ── Icons ───────────────────────────────────────────────────────
	const ICONS = {};

	function create_icons() {
		ICONS.oberoi = L.divIcon({
			className: "custom-marker",
			html: '<div style="background:#e65100; width:12px; height:12px; border-radius:50%; border:2px solid #fff; box-shadow:0 1px 4px rgba(0,0,0,0.3);"></div>',
			iconSize: [16, 16],
			iconAnchor: [8, 8],
			popupAnchor: [0, -10],
		});
		ICONS.tranzrail = L.divIcon({
			className: "custom-marker",
			html: '<div style="background:#1565c0; width:12px; height:12px; border-radius:50%; border:2px solid #fff; box-shadow:0 1px 4px rgba(0,0,0,0.3);"></div>',
			iconSize: [16, 16],
			iconAnchor: [8, 8],
			popupAnchor: [0, -10],
		});
		ICONS.highlight = L.divIcon({
			className: "custom-marker highlight-marker",
			html: '<div style="background:#ffd600; width:16px; height:16px; border-radius:50%; border:3px solid #f44336; box-shadow:0 1px 6px rgba(0,0,0,0.5);"></div>',
			iconSize: [22, 22],
			iconAnchor: [11, 11],
			popupAnchor: [0, -12],
		});
	}

	// ── Library loaders ─────────────────────────────────────────────
	function load_css(id, href) {
		if (!document.getElementById(id)) {
			const link = document.createElement("link");
			link.id = id;
			link.rel = "stylesheet";
			link.href = href;
			link.crossOrigin = "";
			document.head.appendChild(link);
		}
	}

	function load_script(src, integrity) {
		return new Promise((resolve) => {
			const script = document.createElement("script");
			script.src = src;
			if (integrity) script.integrity = integrity;
			script.crossOrigin = "";
			script.onload = () => resolve();
			document.head.appendChild(script);
		});
	}

	function init_leaflet() {
		load_css("leaflet-css", "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css");
		load_css("mc-css", "https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css");
		load_css("mc-default-css", "https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css");

		let chain = Promise.resolve();

		if (!window.L) {
			chain = chain.then(() =>
				load_script(
					"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
					"sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
				)
			);
		}

		chain = chain.then(() => {
			if (window.L && !L.MarkerClusterGroup) {
				return load_script(
					"https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"
				);
			}
		});

		return chain;
	}

	// ── Map setup ───────────────────────────────────────────────────
	function create_map() {
		if (map) return;
		map = L.map("india-map-container", {
			zoomControl: true,
			maxZoom: 22,
		}).setView([22.5, 82.0], 5);

		// Multiple base layers for overlay switching (Req #6)
		base_layers = {
			"Street": L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
				attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
				maxZoom: 19,
			}),
			"Satellite": L.tileLayer("https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}", {
				attribution: '&copy; Google',
				maxZoom: 22,
			}),
			"Hybrid": L.tileLayer("https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}", {
				attribution: '&copy; Google',
				maxZoom: 22,
			}),
			"Terrain": L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
				attribution: '&copy; <a href="https://opentopomap.org">OpenTopoMap</a>',
				maxZoom: 17,
			}),
		};

		base_layers["Street"].addTo(map);
		L.control.layers(base_layers, null, { position: "topright" }).addTo(map);
		create_icons();

		// Invalidate size after layout settles
		setTimeout(() => map.invalidateSize(), 200);
	}

	// ── Zoom to a specific employee ─────────────────────────────────
	function zoom_to_employee(emp_id) {
		const m = all_markers.find(mk => mk.employee === emp_id);
		if (!m) return;

		map.setView([m.latitude, m.longitude], 16);

		setTimeout(() => {
			if (cluster_layer) {
				cluster_layer.eachLayer((layer) => {
					if (layer._emp_id === emp_id && layer.openPopup) {
						cluster_layer.zoomToShowLayer(layer, () => {
							layer.openPopup();
						});
					}
				});
			}
			if (plain_layer) {
				plain_layer.eachLayer((layer) => {
					if (layer._emp_id === emp_id && layer.openPopup) {
						layer.openPopup();
					}
				});
			}
		}, 600);
	}

	// ── Render markers ──────────────────────────────────────────────
	function render_markers() {
		if (!map) return;

		if (cluster_layer) { map.removeLayer(cluster_layer); cluster_layer = null; }
		if (plain_layer) { map.removeLayer(plain_layer); plain_layer = null; }

		const use_cluster = view_mode === "Cluster";

		if (use_cluster) {
			cluster_layer = L.markerClusterGroup({
				showCoverageOnHover: false,
				maxClusterRadius: 45,
				spiderfyOnMaxZoom: true,
				zoomToBoundsOnClick: true,
				// Larger spider web spread so pins are easy to click (Req #5)
				spiderfyDistanceMultiplier: 2.5,
				// NO disableClusteringAtZoom — clusters always spiderfy instead of overlapping
				iconCreateFunction: function (cluster) {
					const count = cluster.getChildCount();
					let size = "small";
					let dim = 36;
					if (count > 50) { size = "large"; dim = 52; }
					else if (count > 10) { size = "medium"; dim = 44; }
					return L.divIcon({
						html: '<div class="cluster-pin cluster-' + size + '">' + count + '</div>',
						className: "marker-cluster-custom",
						iconSize: L.point(dim, dim),
					});
				},
			});
		} else {
			plain_layer = L.layerGroup();
		}

		const target_layer = use_cluster ? cluster_layer : plain_layer;
		const bounds = [];
		let oberoi_count = 0;
		let tranzrail_count = 0;

		all_markers.forEach((m) => {
			const is_selected = selected_employee && m.employee === selected_employee;
			const icon = is_selected
				? ICONS.highlight
				: (m.source === "Oberoi" ? ICONS.oberoi : ICONS.tranzrail);
			const color = m.source === "Oberoi" ? "#e65100" : "#1565c0";

			// Enhanced popup with address, business vertical, sales order, company (Req #4)
			let popup_html = `
				<div style="min-width:220px; font-size:13px;">
					<div style="font-weight:600; margin-bottom:4px; color:${color};">
						${frappe.utils.escape_html(m.company || m.source)}
					</div>
					<div><strong>${frappe.utils.escape_html(m.employee_name || "Unknown")}</strong></div>
					<div style="color:var(--text-muted); font-size:12px;">
						${frappe.utils.escape_html(m.employee || "")}
					</div>
					<div style="margin-top:4px; font-size:12px;">
						<span style="color:var(--text-muted);">Time:</span>
						${frappe.utils.escape_html(m.time || "")}
					</div>`;

			if (m.address) {
				popup_html += `
					<div style="margin-top:3px; font-size:12px;">
						<span style="color:var(--text-muted);">Address:</span>
						${frappe.utils.escape_html(m.address)}
					</div>`;
			}
			if (m.business_vertical) {
				popup_html += `
					<div style="margin-top:3px; font-size:12px;">
						<span style="color:var(--text-muted);">Business Vertical:</span>
						${frappe.utils.escape_html(m.business_vertical)}
					</div>`;
			}
			if (m.sales_order) {
				popup_html += `
					<div style="margin-top:3px; font-size:12px;">
						<span style="color:var(--text-muted);">Sales Order:</span>
						${frappe.utils.escape_html(m.sales_order)}
					</div>`;
			}

			popup_html += `
					<div style="font-size:11px; color:var(--text-light); margin-top:4px;">
						${m.latitude.toFixed(5)}, ${m.longitude.toFixed(5)}
					</div>
				</div>`;

			const marker = L.marker([m.latitude, m.longitude], { icon: icon })
				.bindPopup(popup_html);
			marker._emp_id = m.employee;
			target_layer.addLayer(marker);
			bounds.push([m.latitude, m.longitude]);

			if (m.source === "Oberoi") oberoi_count++;
			else tranzrail_count++;
		});

		map.addLayer(target_layer);

		// Update stats  (Req #2) — "X / Y employees checked in"
		const checked_in = oberoi_count + tranzrail_count;
		document.getElementById("stat-checked-in").textContent = checked_in;
		document.getElementById("stat-total-employees").textContent =
			total_active_employees || checked_in;
		document.getElementById("stat-oberoi").textContent = oberoi_count;
		document.getElementById("stat-tranzrail").textContent = tranzrail_count;

		if (bounds.length && !selected_employee) {
			map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
		}
	}

	// ── Load data from API ──────────────────────────────────────────
	function load_markers() {
		frappe.show_alert({ message: __("Loading employee locations…"), indicator: "blue" });

		const date = $container.find("#map-date").val() || frappe.datetime.get_today();

		frappe.xcall(
			"employee_self_service.employee_self_service.page.india_map.india_map.get_map_markers",
			{ date: date }
		).then((data) => {
			// Backward compatible: old API returns just an array
			if (Array.isArray(data)) {
				all_markers = data;
				all_employees = data.map(m => ({
					employee: m.employee,
					employee_name: m.employee_name,
				}));
				total_active_employees = 0;
			} else {
				all_markers = data.markers || [];
				all_employees = data.employee_list || all_markers.map(m => ({
					employee: m.employee,
					employee_name: m.employee_name,
				}));
				total_active_employees = data.total_active_employees || 0;
			}

			selected_employee = null;
			$search.val("");
			render_markers();

			if (!all_markers.length) {
				frappe.show_alert({ message: __("No locations found for this date"), indicator: "yellow" });
			} else {
				frappe.show_alert({
					message: __("{0} employee locations loaded", [all_markers.length]),
					indicator: "green",
				});
			}
		});
	}

	// ── Initialize ──────────────────────────────────────────────────
	init_leaflet().then(() => {
		create_map();
		load_markers();
	});
};
