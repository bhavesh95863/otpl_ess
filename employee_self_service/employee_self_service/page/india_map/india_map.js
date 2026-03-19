frappe.pages["india-map"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: "India Map - Employee Locations",
		single_column: true,
	});

	const page = wrapper.page;
	page.set_primary_action(__("Refresh"), () => load_markers(), "refresh");

	// Source filter
	let current_source = "All";
	page.add_field({
		fieldname: "source_filter",
		label: __("Source"),
		fieldtype: "Select",
		options: "All\nOberoi\nTranzrail",
		default: "All",
		change: function () {
			current_source = this.get_value();
			render_markers();
		},
	});

	// Search filter
	page.add_field({
		fieldname: "search",
		label: __("Search Employee"),
		fieldtype: "Data",
		change: function () {
			render_markers();
		},
	});

	const $container = $(wrapper).find(".layout-main-section");
	$container.html(`
		<div class="map-stats" style="display:flex; gap:16px; margin-bottom:12px; flex-wrap:wrap;">
			<div class="stat-card" style="padding:8px 16px; background:var(--bg-light-gray); border-radius:var(--border-radius); font-size:13px;">
				<span style="color:var(--text-muted)">Total Pins:</span>
				<strong id="stat-total">0</strong>
			</div>
			<div class="stat-card" style="padding:8px 16px; background:#fff3e0; border-radius:var(--border-radius); font-size:13px;">
				<span style="color:var(--text-muted)">Oberoi:</span>
				<strong id="stat-oberoi" style="color:#e65100;">0</strong>
			</div>
			<div class="stat-card" style="padding:8px 16px; background:#e3f2fd; border-radius:var(--border-radius); font-size:13px;">
				<span style="color:var(--text-muted)">Tranzrail:</span>
				<strong id="stat-tranzrail" style="color:#1565c0;">0</strong>
			</div>
		</div>
		<div id="india-map-container" style="height: calc(100vh - 220px); width: 100%; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color);"></div>
	`);

	let map = null;
	let marker_layer = null;
	let all_markers = [];

	// Custom icons
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
	}

	function init_leaflet() {
		if (!document.getElementById("leaflet-css")) {
			const link = document.createElement("link");
			link.id = "leaflet-css";
			link.rel = "stylesheet";
			link.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
			link.integrity = "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=";
			link.crossOrigin = "";
			document.head.appendChild(link);
		}

		return new Promise((resolve) => {
			if (window.L) {
				resolve();
				return;
			}
			const script = document.createElement("script");
			script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
			script.integrity = "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=";
			script.crossOrigin = "";
			script.onload = () => resolve();
			document.head.appendChild(script);
		});
	}

	function create_map() {
		if (map) return;
		map = L.map("india-map-container").setView([22.5, 82.0], 5);
		L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
			attribution:
				'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
			maxZoom: 18,
		}).addTo(map);
		marker_layer = L.layerGroup().addTo(map);
		create_icons();
	}

	function render_markers() {
		if (!marker_layer) return;
		marker_layer.clearLayers();

		const search_val = (page.fields_dict.search.get_value() || "").toLowerCase();
		const bounds = [];
		let oberoi_count = 0;
		let tranzrail_count = 0;

		all_markers.forEach((m) => {
			// Source filter
			if (current_source !== "All" && m.source !== current_source) return;

			// Search filter
			if (search_val) {
				const haystack = (
					(m.employee_name || "") +
					" " +
					(m.employee || "")
				).toLowerCase();
				if (!haystack.includes(search_val)) return;
			}

			const icon = m.source === "Oberoi" ? ICONS.oberoi : ICONS.tranzrail;
			const color = m.source === "Oberoi" ? "#e65100" : "#1565c0";

			const popup = `
				<div style="min-width:200px; font-size:13px;">
					<div style="font-weight:600; margin-bottom:4px; color:${color};">
						${frappe.utils.escape_html(m.source)}
					</div>
					<div><strong>${frappe.utils.escape_html(m.employee_name || "Unknown")}</strong></div>
					<div style="color:var(--text-muted); font-size:12px;">${frappe.utils.escape_html(m.employee || "")}</div>
					<div style="margin-top:4px; font-size:12px;">
						<span style="color:var(--text-muted)">Time:</span> ${frappe.utils.escape_html(m.time || "")}
					</div>
					<div style="font-size:11px; color:var(--text-light);">
						${m.latitude.toFixed(5)}, ${m.longitude.toFixed(5)}
					</div>
				</div>
			`;

			L.marker([m.latitude, m.longitude], { icon: icon })
				.bindPopup(popup)
				.addTo(marker_layer);

			bounds.push([m.latitude, m.longitude]);

			if (m.source === "Oberoi") oberoi_count++;
			else tranzrail_count++;
		});

		// Update stats
		document.getElementById("stat-total").textContent = bounds.length;
		document.getElementById("stat-oberoi").textContent = oberoi_count;
		document.getElementById("stat-tranzrail").textContent = tranzrail_count;

		if (bounds.length) {
			map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
		}
	}

	function load_markers() {
		frappe.show_alert({ message: __("Loading employee locations..."), indicator: "blue" });

		frappe.xcall(
			"employee_self_service.employee_self_service.page.india_map.india_map.get_map_markers"
		).then((markers) => {
			all_markers = markers || [];
			render_markers();

			if (!all_markers.length) {
				frappe.show_alert({ message: __("No locations found"), indicator: "yellow" });
			} else {
				frappe.show_alert({
					message: __("{0} employee locations loaded", [all_markers.length]),
					indicator: "green",
				});
			}
		});
	}

	// Initialize
	init_leaflet().then(() => {
		create_map();
		load_markers();
	});
};
