(function () {
    function findPanelForm(button) {
        const panel = button.closest(".form-panel");
        if (!panel) {
            return null;
        }
        return panel.querySelector("form");
    }

    function selectDepotByName(selectElement, matcher) {
        if (!selectElement) {
            return false;
        }
        const option = Array.from(selectElement.options).find((item) => matcher(item.textContent || ""));
        if (!option) {
            return false;
        }
        selectElement.value = option.value;
        return true;
    }

    function fillDepotPrefill() {
        const button = document.getElementById("prefill-demo-depot");
        if (!button) {
            return;
        }
        button.addEventListener("click", () => {
            const form = findPanelForm(button);
            if (!form) {
                return;
            }
            form.querySelector('input[name="name"]').value = "Demo Muenster South Depot";
            form.querySelector('input[name="location"]').value = "Hammer Straße 470, 48153 Münster, Germany";
            form.querySelector('input[name="solar_capacity_kwp"]').value = "14";
            form.querySelector('input[name="panel_tilt_deg"]').value = "28";
            form.querySelector('input[name="panel_azimuth_deg"]').value = "190";
            form.querySelector('input[name="solar_efficiency_factor"]').value = "0.79";
            form.querySelector('input[name="grid_fee_per_kwh"]').value = "0.22";
            form.querySelector('input[name="supplier_markup_pct"]').value = "3.2";
            form.querySelector('input[name="tax_multiplier"]').value = "1.19";
        });
    }

    function fillChargerPrefill() {
        const button = document.getElementById("prefill-demo-charger");
        if (!button) {
            return;
        }
        button.addEventListener("click", () => {
            const form = findPanelForm(button);
            if (!form) {
                return;
            }
            form.querySelector('input[name="name"]').value = "South Opportunity Charger";
            form.querySelector('input[name="power_kw"]').value = "60";
            form.querySelector('input[name="slot_count"]').value = "2";
            selectDepotByName(
                form.querySelector('select[name="depot_id"]'),
                (label) => label.includes("South Depot"),
            );
        });
    }

    function fillVehiclePrefill() {
        const button = document.getElementById("prefill-demo-vehicle");
        if (!button) {
            return;
        }
        button.addEventListener("click", () => {
            const form = findPanelForm(button);
            if (!form) {
                return;
            }
            form.querySelector('input[name="name"]').value = "South Shuttle 02";
            form.querySelector('input[name="vehicle_type"]').value = "Electric shuttle";
            form.querySelector('input[name="battery_kwh"]').value = "130";
            form.querySelector('input[name="current_soc_pct"]').value = "24";
            form.querySelector('input[name="min_reserve_pct"]').value = "18";
            form.querySelector('input[name="efficiency_kwh_per_km"]').value = "0.86";
            form.querySelector('input[name="max_speed_kph"]').value = "95";
            form.querySelector('input[name="max_charge_power_kw"]').value = "60";
            selectDepotByName(
                form.querySelector('select[name="depot_id"]'),
                (label) => label.includes("South Depot"),
            );
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        fillDepotPrefill();
        fillChargerPrefill();
        fillVehiclePrefill();
    });
})();
