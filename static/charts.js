(function () {
    const COLORS = {
        axis: "#5f6f67",
        grid: "rgba(7, 45, 81, 0.12)",
        solarFill: "rgba(239, 143, 53, 0.22)",
        solarStroke: "#ef8f35",
        priceStroke: "#072d51",
        boughtFill: "#072d51",
        boughtSoft: "#5e88b5",
    };

    function svgNode(tagName, attributes) {
        const node = document.createElementNS("http://www.w3.org/2000/svg", tagName);
        Object.entries(attributes || {}).forEach(([key, value]) => {
            node.setAttribute(key, String(value));
        });
        return node;
    }

    function formatTickLabelParts(timestamp) {
        const date = new Date(timestamp);
        if (Number.isNaN(date.getTime())) {
            return {
                primary: timestamp,
                secondary: "",
            };
        }
        return {
            primary: date.toLocaleDateString([], {
                weekday: "short",
                day: "2-digit",
                month: "short",
            }),
            secondary: date.toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
            }),
        };
    }

    function buildScales(series, keys) {
        const width = 920;
        const height = 340;
        const padding = { top: 20, right: 58, bottom: 74, left: 58 };
        const innerWidth = width - padding.left - padding.right;
        const innerHeight = height - padding.top - padding.bottom;
        const maxima = {};

        keys.forEach((key) => {
            const maxValue = Math.max(...series.map((item) => Number(item[key] || 0)), 0);
            maxima[key] = maxValue <= 0 ? 1 : maxValue * 1.12;
        });

        function x(index) {
            if (series.length <= 1) {
                return padding.left + innerWidth / 2;
            }
            return padding.left + (innerWidth * index) / (series.length - 1);
        }

        function y(value, key) {
            return padding.top + innerHeight - (innerHeight * value) / maxima[key];
        }

        return {
            width,
            height,
            padding,
            innerWidth,
            innerHeight,
            maxima,
            x,
            y,
        };
    }

    function appendYAxis(svg, scale, side, key, formatter) {
        const ticks = 4;
        const x = side === "left" ? scale.padding.left : scale.width - scale.padding.right;
        const textAnchor = side === "left" ? "end" : "start";
        const dx = side === "left" ? -10 : 10;

        for (let index = 0; index <= ticks; index += 1) {
            const value = (scale.maxima[key] * index) / ticks;
            const y = scale.y(value, key);
            svg.appendChild(
                svgNode("line", {
                    x1: scale.padding.left,
                    x2: scale.width - scale.padding.right,
                    y1: y,
                    y2: y,
                    stroke: COLORS.grid,
                    "stroke-width": 1,
                }),
            );
            const label = svgNode("text", {
                x: x + dx,
                y: y + 4,
                fill: COLORS.axis,
                "font-size": 11,
                "text-anchor": textAnchor,
            });
            label.textContent = formatter(value);
            svg.appendChild(label);
        }
    }

    function appendXAxis(svg, scale, series, title, preferredTickCount) {
        const tickCount = Math.min(preferredTickCount || 6, series.length);
        const usedIndexes = new Set();
        const axisY = scale.height - scale.padding.bottom;

        svg.appendChild(
            svgNode("line", {
                x1: scale.padding.left,
                x2: scale.width - scale.padding.right,
                y1: axisY,
                y2: axisY,
                stroke: COLORS.axis,
                "stroke-width": 1.2,
            }),
        );

        for (let tick = 0; tick < tickCount; tick += 1) {
            const ratio = tickCount === 1 ? 0 : tick / (tickCount - 1);
            const index = Math.round((series.length - 1) * ratio);
            if (usedIndexes.has(index)) {
                continue;
            }
            usedIndexes.add(index);
            const x = scale.x(index);
            const tickLabel = formatTickLabelParts(series[index].timestamp);

            svg.appendChild(
                svgNode("line", {
                    x1: x,
                    x2: x,
                    y1: axisY,
                    y2: axisY + 6,
                    stroke: COLORS.axis,
                    "stroke-width": 1,
                }),
            );

            const label = svgNode("text", {
                x,
                y: axisY + 18,
                fill: COLORS.axis,
                "font-size": 11,
                "text-anchor": "middle",
            });

            const primary = svgNode("tspan", {
                x,
                dy: 0,
            });
            primary.textContent = tickLabel.primary;
            label.appendChild(primary);

            if (tickLabel.secondary) {
                const secondary = svgNode("tspan", {
                    x,
                    dy: 13,
                });
                secondary.textContent = tickLabel.secondary;
                label.appendChild(secondary);
            }
            svg.appendChild(label);
        }

        if (title) {
            const axisTitle = svgNode("text", {
                x: scale.width / 2,
                y: scale.height - 8,
                fill: COLORS.axis,
                "font-size": 12,
                "font-weight": 600,
                "text-anchor": "middle",
            });
            axisTitle.textContent = title;
            svg.appendChild(axisTitle);
        }
    }

    function renderForecastChart(element, series) {
        if (!series.length) {
            return;
        }
        const scale = buildScales(series, ["solar_kwh", "buy_price"]);
        const svg = svgNode("svg", {
            viewBox: `0 0 ${scale.width} ${scale.height}`,
            class: "chart-svg",
            role: "img",
            "aria-label": "Projected solar availability and buy price over time",
        });

        appendYAxis(svg, scale, "left", "solar_kwh", (value) => `${value.toFixed(0)} kWh`);
        appendYAxis(svg, scale, "right", "buy_price", (value) => `EUR ${value.toFixed(2)}`);
        appendXAxis(svg, scale, series, "Time (local)", 8);

        let areaPath = `M ${scale.x(0)} ${scale.y(0, "solar_kwh")} `;
        let solarPath = "";
        let pricePath = "";

        series.forEach((point, index) => {
            const x = scale.x(index);
            const solarY = scale.y(point.solar_kwh, "solar_kwh");
            const priceY = scale.y(point.buy_price, "buy_price");
            areaPath += `${index === 0 ? "L" : "L"} ${x} ${solarY} `;
            solarPath += `${index === 0 ? "M" : "L"} ${x} ${solarY} `;
            pricePath += `${index === 0 ? "M" : "L"} ${x} ${priceY} `;
        });
        areaPath += `L ${scale.x(series.length - 1)} ${scale.y(0, "solar_kwh")} Z`;

        svg.appendChild(
            svgNode("path", {
                d: areaPath,
                fill: COLORS.solarFill,
                stroke: "none",
            }),
        );
        svg.appendChild(
            svgNode("path", {
                d: solarPath,
                fill: "none",
                stroke: COLORS.solarStroke,
                "stroke-width": 3,
                "stroke-linejoin": "round",
                "stroke-linecap": "round",
            }),
        );
        svg.appendChild(
            svgNode("path", {
                d: pricePath,
                fill: "none",
                stroke: COLORS.priceStroke,
                "stroke-width": 2.5,
                "stroke-linejoin": "round",
                "stroke-linecap": "round",
            }),
        );

        element.appendChild(svg);
    }

    function renderChargeSplitChart(element, series) {
        if (!series.length) {
            return;
        }
        const stackedSeries = series.map((point) => ({
            ...point,
            total_kwh: Number(point.solar_kwh || 0) + Number(point.grid_kwh || 0),
        }));
        const scale = buildScales(stackedSeries, ["total_kwh"]);
        const svg = svgNode("svg", {
            viewBox: `0 0 ${scale.width} ${scale.height}`,
            class: "chart-svg",
            role: "img",
            "aria-label": "Charged energy split between solar and bought power over time",
        });

        appendYAxis(svg, scale, "left", "total_kwh", (value) => `${value.toFixed(0)} kWh`);
        appendXAxis(svg, scale, stackedSeries, "Charging time (local)", 10);

        const barWidth = Math.max(2, Math.min(18, (scale.innerWidth / stackedSeries.length) * 0.76));
        stackedSeries.forEach((point, index) => {
            const x = scale.x(index) - barWidth / 2;
            const totalBaseY = scale.y(0, "total_kwh");
            const solarHeight = totalBaseY - scale.y(point.solar_kwh, "total_kwh");
            const gridCombinedHeight = totalBaseY - scale.y(point.total_kwh, "total_kwh");
            const gridHeight = Math.max(0, gridCombinedHeight - solarHeight);

            svg.appendChild(
                svgNode("rect", {
                    x,
                    y: totalBaseY - solarHeight,
                    width: barWidth,
                    height: solarHeight,
                    rx: 2,
                    fill: COLORS.solarStroke,
                    opacity: 0.9,
                }),
            );
            svg.appendChild(
                svgNode("rect", {
                    x,
                    y: totalBaseY - solarHeight - gridHeight,
                    width: barWidth,
                    height: gridHeight,
                    rx: 2,
                    fill: COLORS.boughtFill,
                    opacity: 0.92,
                }),
            );
        });

        element.appendChild(svg);
    }

    function renderLegends() {
        document.querySelectorAll(".chart-shell").forEach((shell) => {
            if (shell.querySelector(".chart-legend")) {
                return;
            }
            const type = shell.querySelector(".chart-canvas")?.dataset.chartType;
            const legend = document.createElement("div");
            legend.className = "chart-legend";

            if (type === "forecast-overview") {
                legend.innerHTML = `
                    <span><i style="background:${COLORS.solarStroke}"></i>Solar availability</span>
                    <span><i style="background:${COLORS.priceStroke}"></i>Average buy price</span>
                `;
            } else if (type === "charge-split") {
                legend.innerHTML = `
                    <span><i style="background:${COLORS.solarStroke}"></i>Solar charged</span>
                    <span><i style="background:${COLORS.boughtFill}"></i>Grid charged</span>
                `;
            }

            shell.appendChild(legend);
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll(".chart-canvas[data-chart-type]").forEach((element) => {
            const series = JSON.parse(element.dataset.series || "[]");
            if (element.dataset.chartType === "forecast-overview") {
                renderForecastChart(element, series);
            } else if (element.dataset.chartType === "charge-split") {
                renderChargeSplitChart(element, series);
            }
        });
        renderLegends();
    });
})();
