(function () {
    if (typeof L === "undefined") {
        return;
    }

    function createMap(element) {
        const map = L.map(element).setView([48.7758, 9.1829], 11);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: "&copy; OpenStreetMap contributors",
        }).addTo(map);
        return map;
    }

    function fitMapToLayers(map, layers) {
        const validLayers = layers.filter(Boolean);
        if (!validLayers.length) {
            return;
        }
        const group = L.featureGroup(validLayers);
        map.fitBounds(group.getBounds().pad(0.18));
    }

    function formatLocalDateTime(isoString) {
        if (!isoString) {
            return "n/a";
        }
        const date = new Date(isoString);
        if (Number.isNaN(date.getTime())) {
            return isoString;
        }
        return date.toLocaleString([], {
            day: "2-digit",
            month: "short",
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    function polylineForRoute(route, options) {
        const path = route.geometry && route.geometry.coordinates
            ? route.geometry.coordinates.map(([longitude, latitude]) => [latitude, longitude])
            : [
                [route.start_depot.latitude, route.start_depot.longitude],
                [route.service_point.latitude, route.service_point.longitude],
                [route.end_depot.latitude, route.end_depot.longitude],
            ];
        return L.polyline(
            path,
            options,
        ).bindPopup(
            `<strong>${route.name}</strong><br>${route.service_stop_count || (route.service_points ? route.service_points.length : 1)} stops`
            + `<br>${route.service_point.label}<br>${route.distance_km} km`
            + (route.route_duration_minutes ? `<br>${Math.round(route.route_duration_minutes)} min` : "")
            + (route.assigned_vehicle ? `<br>Vehicle: ${route.assigned_vehicle}` : "")
        );
    }

    function colorForRoute(routeId) {
        const palette = ["#244f41", "#ef8f35", "#2d7f5e", "#8868b2", "#cc5b45", "#3f7eb8", "#9f6d2a", "#4d7d9a"];
        const index = Math.abs(Number(routeId || 0)) % palette.length;
        return palette[index];
    }

    function attachRouteHoverInteractions(routeLayers) {
        const rows = document.querySelectorAll(".map-linked-row[data-route-id]");
        const activeClass = "is-active";

        function setActive(routeId, isActive) {
            const layer = routeLayers[String(routeId)];
            if (!layer) {
                return;
            }
            const baseStyle = layer.baseStyle || {};
            layer.setStyle({
                color: baseStyle.color,
                weight: isActive ? (baseStyle.weight || 4) + 3 : baseStyle.weight,
                opacity: isActive ? 1 : baseStyle.opacity,
                dashArray: baseStyle.dashArray,
            });
            if (isActive) {
                layer.bringToFront();
            }
            rows.forEach((row) => {
                if (row.dataset.routeId === String(routeId)) {
                    row.classList.toggle(activeClass, isActive);
                }
            });
        }

        Object.entries(routeLayers).forEach(([routeId, layer]) => {
            layer.on("mouseover", () => setActive(routeId, true));
            layer.on("mouseout", () => setActive(routeId, false));
        });

        rows.forEach((row) => {
            const routeId = row.dataset.routeId;
            row.addEventListener("mouseenter", () => setActive(routeId, true));
            row.addEventListener("mouseleave", () => setActive(routeId, false));
        });
    }

    function renderRouteFormMap(element) {
        const map = createMap(element);
        const depots = JSON.parse(element.dataset.depots || "[]");
        const routes = JSON.parse(element.dataset.routes || "[]");
        const layers = [];
        const routeLayers = {};
        const depotsById = {};

        depots.forEach((depot) => {
            depotsById[String(depot.id)] = depot;
            layers.push(
                L.circleMarker([depot.latitude, depot.longitude], {
                    radius: 8,
                    color: "#244f41",
                    fillColor: "#ef8f35",
                    fillOpacity: 0.95,
                    weight: 2,
                }).bindPopup(`<strong>${depot.name}</strong><br>${depot.location}`)
            );
        });

        routes.forEach((route) => {
            const routeColor = colorForRoute(route.id);
            const layer = polylineForRoute(route, {
                color: routeColor,
                weight: 4,
                opacity: 0.78,
                dashArray: "6 6",
            });
            layer.baseStyle = {
                color: routeColor,
                weight: 4,
                opacity: 0.78,
                dashArray: "6 6",
            };
            routeLayers[String(route.id)] = layer;
            layers.push(layer);
        });

        layers.forEach((layer) => layer.addTo(map));

        const startDepotSelect = document.querySelector('select[name="start_depot_id"]');
        const endDepotSelect = document.querySelector('select[name="end_depot_id"]');
        const departureInput = document.querySelector('input[name="departure_at"]');
        const serviceAddressesInput = document.querySelector('textarea[name="service_addresses"]');
        const distanceField = document.getElementById("preview-distance");
        const durationField = document.getElementById("preview-duration");
        const arrivalField = document.getElementById("preview-arrival");
        const prefillDemoRouteButton = document.getElementById("prefill-demo-route");
        let previewLayer = null;
        let geocodeTimer = null;

        function drawPreview(previewRoute) {
            if (previewLayer) {
                map.removeLayer(previewLayer);
            }
            if (!previewRoute) {
                return;
            }
            previewLayer = polylineForRoute(
                previewRoute,
                {
                    color: "#ef8f35",
                    weight: 5,
                    opacity: 0.9,
                }
            ).addTo(map);
            fitMapToLayers(map, layers.concat([previewLayer]));
        }

        async function updatePreviewFromAddress() {
            const addresses = serviceAddressesInput.value.trim();
            const departureAt = departureInput.value;
            if (!addresses || !startDepotSelect.value || !endDepotSelect.value) {
                return;
            }
            try {
                const params = new URLSearchParams({
                    service_addresses: addresses,
                    start_depot_id: startDepotSelect.value,
                    end_depot_id: endDepotSelect.value,
                });
                if (departureAt) {
                    params.set("departure_at", departureAt);
                }
                const response = await fetch(`/api/route-preview?${params.toString()}`);
                if (!response.ok) {
                    return;
                }
                const payload = await response.json();
                distanceField.textContent = `${payload.distance_km} km`;
                durationField.textContent = `${Math.round(payload.duration_minutes)} min`;
                arrivalField.textContent = formatLocalDateTime(payload.arrival_at);
                drawPreview({
                    name: "New route preview",
                    distance_km: payload.distance_km,
                    route_duration_minutes: payload.duration_minutes,
                    geometry: payload.geometry,
                    service_stop_count: payload.service_stop_count,
                    service_points: payload.service_points,
                    start_depot: depotsById[startDepotSelect.value],
                    end_depot: depotsById[endDepotSelect.value],
                    service_point: {
                        label: payload.service_label,
                        latitude: payload.service_latitude,
                        longitude: payload.service_longitude,
                    },
                });
            } catch (_error) {
                return;
            }
        }

        [startDepotSelect, endDepotSelect, departureInput].forEach((elementRef) => {
            elementRef.addEventListener("change", () => {
                window.clearTimeout(geocodeTimer);
                geocodeTimer = window.setTimeout(updatePreviewFromAddress, 150);
            });
        });

        serviceAddressesInput.addEventListener("input", () => {
            window.clearTimeout(geocodeTimer);
            geocodeTimer = window.setTimeout(updatePreviewFromAddress, 450);
        });
        serviceAddressesInput.addEventListener("blur", updatePreviewFromAddress);

        if (prefillDemoRouteButton) {
            prefillDemoRouteButton.addEventListener("click", () => {
                const now = new Date();
                now.setDate(now.getDate() + 1);
                now.setHours(14, 5, 0, 0);
                const localValue = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
                    .toISOString()
                    .slice(0, 16);

                const routeNameInput = document.querySelector('input[name="name"]');
                if (routeNameInput) {
                    routeNameInput.value = "Live Demo Multi-Stop Route";
                }
                departureInput.value = localValue;

                const depotOptions = Array.from(startDepotSelect.options);
                const eastOption = depotOptions.find((option) => option.text.includes("East Depot"));
                const northOption = depotOptions.find((option) => option.text.includes("North Depot"));
                if (eastOption) {
                    startDepotSelect.value = eastOption.value;
                    endDepotSelect.value = eastOption.value;
                } else if (northOption) {
                    startDepotSelect.value = northOption.value;
                    endDepotSelect.value = northOption.value;
                }

                serviceAddressesInput.value = [
                    "Domplatz 20, 48143 Münster, Germany",
                    "Aegidiimarkt 7, 48143 Münster, Germany",
                    "Hafenweg 26B, 48155 Münster, Germany",
                ].join("\n");
                updatePreviewFromAddress();
            });
        }

        fitMapToLayers(map, layers);
        attachRouteHoverInteractions(routeLayers);
    }

    function renderPlanOverviewMap(element) {
        const map = createMap(element);
        const depots = JSON.parse(element.dataset.depots || "[]");
        const chargers = JSON.parse(element.dataset.chargers || "[]");
        const routes = JSON.parse(element.dataset.routes || "[]");
        const layers = [];
        const routeLayers = {};

        depots.forEach((depot) => {
            layers.push(
                L.marker([depot.latitude, depot.longitude]).bindPopup(
                    `<strong>${depot.name}</strong><br>${depot.location}<br>Solar: ${depot.solar_capacity_kwp} kWp`
                    + (depot.charger_summary ? `<br>Chargers: ${depot.charger_summary}` : "")
                )
            );
        });

        const chargersByDepot = {};
        chargers.forEach((charger) => {
            chargersByDepot[charger.depot_id] = chargersByDepot[charger.depot_id] || [];
            chargersByDepot[charger.depot_id].push(charger);
        });

        Object.values(chargersByDepot).forEach((depotChargers) => {
            depotChargers.forEach((charger, index) => {
                const lat = charger.latitude + 0.0015 * Math.sin(index);
                const lng = charger.longitude + 0.0015 * Math.cos(index);
                layers.push(
                    L.circleMarker([lat, lng], {
                        radius: 6,
                        color: "#18362d",
                        fillColor: "#d4ecdf",
                        fillOpacity: 0.95,
                        weight: 2,
                    }).bindPopup(
                        `<strong>${charger.name}</strong><br>${charger.depot_name}<br>${charger.power_kw} kW x ${charger.slot_count}`
                    )
                );
            });
        });

        routes.forEach((route) => {
            const routeColor = route.status === "unserved" ? "#9c3d2f" : colorForRoute(route.id);
            const layer = polylineForRoute(route, {
                color: routeColor,
                weight: route.status === "unserved" ? 4 : 5,
                opacity: route.status === "unserved" ? 0.78 : 0.92,
                dashArray: route.status === "unserved" ? "10 8" : undefined,
            });
            layer.baseStyle = {
                color: routeColor,
                weight: route.status === "unserved" ? 4 : 5,
                opacity: route.status === "unserved" ? 0.78 : 0.92,
                dashArray: route.status === "unserved" ? "10 8" : undefined,
            };
            routeLayers[String(route.id)] = layer;
            layers.push(layer);
        });

        layers.forEach((layer) => layer.addTo(map));
        fitMapToLayers(map, layers);
        attachRouteHoverInteractions(routeLayers);
    }

    document.querySelectorAll("[data-map-type]").forEach((element) => {
        if (element.dataset.mapType === "route-form") {
            renderRouteFormMap(element);
        }
        if (element.dataset.mapType === "plan-overview") {
            renderPlanOverviewMap(element);
        }
    });
})();
