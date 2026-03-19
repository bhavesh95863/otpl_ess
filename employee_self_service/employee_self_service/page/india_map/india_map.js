frappe.pages["india-map"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: "India Map - Employee Locations",
		single_column: true,
	});

	const page = wrapper.page;
	page.set_primary_action(__("Refresh"), () => load_markers(), "refresh");

	let current_source = "All";
	let view_mode = "Cluster";

	const $container = $(wrapper).find(".layout-main-section");
	$container.html(`
		<div class="map-toolbar" style="display:flex; gap:12px; margin-bottom:12px; flex-wrap:wrap; align-items:flex-end;">
			<div class="map-filter-group">
				<label style="font-size:11px; color:var(--text-muted); display:block; margin-bottom:4px;">View Mode</label>
				<select id="map-view-mode" class="form-control input-xs" style="width:140px; height:30px; font-size:13px;">
					<option value="Cluster">Cluster</option>
					<option value="Pins">Pins</option>
				</select>
			</div>
			<div class="map-filter-group">
				<label style="font-size:11px; color:var(--text-muted); display:block; margin-bottom:4px;">Search Employee</label>
				<input id="map-search" type="text" class="form-control input-xs" placeholder="Type name or ID..." style="width:200px; height:30px; font-size:13px;" />
			</div>
			<div style="margin-left:auto; padding:6px 14px; background:var(--bg-light-gray); border-radius:var(--border-radius); font-size:13px;">
				<span style="color:var(--text-muted)">Total Pins:</span>
				<strong id="stat-total">0</strong>
			</div>
		</div>
		<div id="india-map-container" style="height: calc(100vh - 220px); width: 100%; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color);"></div>
	`);

	// Bind filter events
	$container.find("#map-view-mode").on("change", function () {
		view_mode = $(this).val();
		render_markers();
	});
	let search_timer;
	$container.find("#map-search").on("input", function () {
		clearTimeout(search_timer);
		search_timer = setTimeout(() => render_markers(), 300);
	});

	let map = null;
	let cluster_layer = null;
	let plain_layer = null;
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
		// Leaflet CSS
		load_css("leaflet-css", "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css");
		// MarkerCluster CSS
		load_css("mc-css", "https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css");
		load_css("mc-default-css", "https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css");

		let chain = Promise.resolve();

		// Load Leaflet JS
		if (!window.L) {
			chain = chain.then(() =>
				load_script(
					"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
					"sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
				)
			);
		}

		// Load MarkerCluster JS (depends on Leaflet)
		chain = chain.then(() => {
			if (window.L && !L.MarkerClusterGroup) {
				return load_script(
					"https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"
				);
			}
		});

		return chain;
	}

	function create_map() {
		if (map) return;
		map = L.map("india-map-container").setView([22.5, 82.0], 5);
		L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
			attribution:
				'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
			maxZoom: 18,
		}).addTo(map);
		create_icons();
	}

	function render_markers() {
		if (!map) return;

		// Remove old layers
		if (cluster_layer) {
			map.removeLayer(cluster_layer);
			cluster_layer = null;
		}
		if (plain_layer) {
			map.removeLayer(plain_layer);
			plain_layer = null;
		}

		const use_cluster = view_mode === "Cluster";

		if (use_cluster) {
			// Create cluster group
			cluster_layer = L.markerClusterGroup({
				showCoverageOnHover: false,
				maxClusterRadius: 45,
				spiderfyOnMaxZoom: true,
				disableClusteringAtZoom: 16,
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
			// Plain layer group — all pins shown individually
			plain_layer = L.layerGroup();
		}

		const target_layer = use_cluster ? cluster_layer : plain_layer;

		const search_val = ($("#map-search").val() || "").toLowerCase();
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

			const marker = L.marker([m.latitude, m.longitude], { icon: icon })
				.bindPopup(popup);
			target_layer.addLayer(marker);

			bounds.push([m.latitude, m.longitude]);

			if (m.source === "Oberoi") oberoi_count++;
			else tranzrail_count++;
		});

		map.addLayer(target_layer);

		// Update stats
		document.getElementById("stat-total").textContent = bounds.length;

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
