(function (root, factory) {
    "use strict";
    root.StockRadarPriceChart = factory(root);
}(typeof window !== "undefined" ? window : globalThis, function (root) {
    "use strict";

    const TREND_COLOURS = {
        buy: "#4ade80",
        sell: "#fb7185",
        hold: "#f4c95d"
    };

    function finiteNumber(value) {
        const number = Number(value);
        return Number.isFinite(number) ? number : null;
    }

    function normalizePoints(rawPoints) {
        const source = Array.isArray(rawPoints) ? rawPoints : [];
        const points = [];

        source.forEach(function (point, index) {
            if (!point || typeof point !== "object") {
                return;
            }
            const price = finiteNumber(point.price);
            if (price === null) {
                return;
            }
            points.push({
                date: String(point.date || ""),
                tooltipLabel: String(point.tooltip_label || point.label || point.date || ""),
                price: price,
                timestamp: finiteNumber(point.timestamp_ms),
                sourceIndex: index
            });
        });

        const useTimestamps = points.length > 0 && points.every(function (point) {
            return point.timestamp !== null;
        });
        points.forEach(function (point, index) {
            point.x = useTimestamps ? point.timestamp : index;
        });
        return points;
    }

    function directionForPoints(points) {
        if (!points.length) {
            return "hold";
        }
        const first = points[0].price;
        const last = points[points.length - 1].price;
        const tolerance = Math.max(Math.abs(first), Math.abs(last), 1) * 1e-9;
        if (last - first > tolerance) {
            return "buy";
        }
        if (last - first < -tolerance) {
            return "sell";
        }
        return "hold";
    }

    function formatPrice(value, currency) {
        const price = finiteNumber(value);
        if (price === null) {
            return "—";
        }
        const metadata = currency && typeof currency === "object" ? currency : {};
        const sign = price < 0 ? "-" : "";
        const amount = Math.abs(price).toLocaleString("en-GB", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
        return sign + String(metadata.prefix || "") + amount + String(metadata.suffix || "");
    }

    function nearestIndex(metaPoints, x) {
        if (!metaPoints || !metaPoints.length || !Number.isFinite(x)) {
            return null;
        }
        let selectedIndex = 0;
        let selectedDistance = Math.abs(metaPoints[0].x - x);
        for (let index = 1; index < metaPoints.length; index += 1) {
            const distance = Math.abs(metaPoints[index].x - x);
            if (distance < selectedDistance) {
                selectedDistance = distance;
                selectedIndex = index;
            }
        }
        return selectedIndex;
    }

    function closestPointByScaleValue(points, value) {
        if (!points.length) {
            return null;
        }
        let selected = points[0];
        let selectedDistance = Math.abs(points[0].x - value);
        for (let index = 1; index < points.length; index += 1) {
            const distance = Math.abs(points[index].x - value);
            if (distance < selectedDistance) {
                selected = points[index];
                selectedDistance = distance;
            }
        }
        return selected;
    }

    const interactionGuide = {
        id: "stockRadarInteractionGuide",
        afterDatasetsDraw: function (chart) {
            const index = chart.$stockRadarInteractionIndex;
            if (!Number.isInteger(index)) {
                return;
            }
            const element = chart.getDatasetMeta(0).data[index];
            if (!element) {
                return;
            }

            const area = chart.chartArea;
            const x = Math.max(area.left, Math.min(area.right, element.x));
            const y = Math.max(area.top, Math.min(area.bottom, element.y));
            const colour = chart.$stockRadarTrendColour || TREND_COLOURS.hold;
            const context = chart.ctx;

            context.save();
            context.beginPath();
            context.setLineDash([4, 4]);
            context.moveTo(x, area.top);
            context.lineTo(x, area.bottom);
            context.lineWidth = 1;
            context.strokeStyle = "rgba(203, 213, 225, 0.42)";
            context.stroke();
            context.setLineDash([]);

            context.beginPath();
            context.arc(x, y, 4.5, 0, Math.PI * 2);
            context.fillStyle = colour;
            context.fill();
            context.lineWidth = 2;
            context.strokeStyle = "#071018";
            context.stroke();
            context.restore();
        }
    };

    function canvasX(chart, event) {
        const bounds = chart.canvas.getBoundingClientRect();
        if (!bounds.width) {
            return null;
        }
        return (event.clientX - bounds.left) * (chart.width / bounds.width);
    }

    function positionTooltip(chart, tooltipElement, element) {
        if (!tooltipElement || !element) {
            return;
        }
        const shell = tooltipElement.offsetParent;
        if (!shell) {
            return;
        }
        const canvasBounds = chart.canvas.getBoundingClientRect();
        const shellBounds = shell.getBoundingClientRect();
        const scaleX = canvasBounds.width / chart.width;
        const scaleY = canvasBounds.height / chart.height;
        const anchorX = canvasBounds.left - shellBounds.left + (element.x * scaleX);
        const anchorY = canvasBounds.top - shellBounds.top + (element.y * scaleY);
        const margin = 4;
        const width = tooltipElement.offsetWidth;
        const height = tooltipElement.offsetHeight;
        const left = Math.max(
            margin,
            Math.min(shell.clientWidth - width - margin, anchorX - (width / 2))
        );
        let top = anchorY - height - 12;
        if (top < margin) {
            top = Math.min(shell.clientHeight - height - margin, anchorY + 12);
        }
        tooltipElement.style.left = Math.round(left) + "px";
        tooltipElement.style.top = Math.round(Math.max(margin, top)) + "px";
    }

    function create(canvas, options) {
        if (!canvas || !root.Chart) {
            return null;
        }
        const config = options && typeof options === "object" ? options : {};
        const points = normalizePoints(config.points);
        if (!points.length) {
            return null;
        }

        const computedDirection = directionForPoints(points);
        const direction = config.direction === computedDirection ? config.direction : computedDirection;
        const trendColour = TREND_COLOURS[direction] || TREND_COLOURS.hold;
        const currency = config.currency || {};
        const tooltipElement = config.tooltipElement || null;
        const data = points.map(function (point) {
            return {x: point.x, y: point.price};
        });

        const chart = new root.Chart(canvas, {
            type: "line",
            data: {
                datasets: [{
                    label: String(config.datasetLabel || "Close price"),
                    data: data,
                    parsing: false,
                    borderColor: trendColour,
                    backgroundColor: trendColour,
                    borderWidth: 2,
                    tension: 0,
                    cubicInterpolationMode: "default",
                    spanGaps: false,
                    fill: false,
                    clip: false,
                    pointRadius: function (context) {
                        return context.dataIndex === points.length - 1 ? 4 : 0;
                    },
                    pointHoverRadius: 0,
                    pointHitRadius: 10,
                    pointBackgroundColor: trendColour,
                    pointBorderColor: "#071018",
                    pointBorderWidth: 2
                }]
            },
            plugins: [interactionGuide],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                normalized: false,
                animation: {duration: 220},
                interaction: {intersect: false, mode: "nearest", axis: "x"},
                layout: {padding: {top: 8, right: 10, bottom: 4, left: 4}},
                plugins: {
                    legend: {display: false},
                    tooltip: {enabled: false}
                },
                scales: {
                    x: {
                        type: "linear",
                        bounds: "data",
                        offset: false,
                        ticks: {
                            color: "#94a3b8",
                            maxTicksLimit: 6,
                            callback: function (value) {
                                const point = closestPointByScaleValue(points, Number(value));
                                return point ? point.tooltipLabel : "";
                            }
                        },
                        grid: {display: false},
                        border: {color: "rgba(148, 163, 184, 0.20)"}
                    },
                    y: {
                        ticks: {
                            color: "#94a3b8",
                            maxTicksLimit: 6,
                            callback: function (value) {
                                return formatPrice(value, currency);
                            }
                        },
                        grid: {color: "rgba(255, 255, 255, 0.07)"},
                        border: {display: false}
                    }
                }
            }
        });
        chart.$stockRadarTrendColour = trendColour;

        function hideInteraction() {
            chart.$stockRadarInteractionIndex = null;
            if (tooltipElement) {
                tooltipElement.classList.remove("visible");
                tooltipElement.textContent = "";
            }
            chart.draw();
        }

        function select(index, persistent) {
            if (!Number.isInteger(index) || !points[index]) {
                return;
            }
            chart.$stockRadarInteractionIndex = index;
            if (persistent) {
                chart.$stockRadarPersistentIndex = index;
            }
            chart.draw();

            if (tooltipElement) {
                tooltipElement.textContent = points[index].tooltipLabel + " · " +
                    formatPrice(points[index].price, currency);
                tooltipElement.classList.add("visible");
                positionTooltip(chart, tooltipElement, chart.getDatasetMeta(0).data[index]);
            }
        }

        function selectNearest(event, persistent) {
            const x = canvasX(chart, event);
            const index = nearestIndex(chart.getDatasetMeta(0).data, x);
            select(index, persistent);
        }

        let touching = false;
        canvas.addEventListener("pointerdown", function (event) {
            if (event.pointerType === "mouse" && event.button !== 0) {
                return;
            }
            touching = event.pointerType !== "mouse";
            if (touching && canvas.setPointerCapture) {
                try {
                    canvas.setPointerCapture(event.pointerId);
                } catch (error) {
                    // Selection still works when pointer capture is unavailable.
                }
            }
            selectNearest(event, touching);
        });
        canvas.addEventListener("pointermove", function (event) {
            if (event.pointerType === "mouse") {
                selectNearest(event, false);
            } else if (touching) {
                selectNearest(event, true);
            }
        });
        canvas.addEventListener("pointerup", function () {
            touching = false;
        });
        canvas.addEventListener("pointercancel", function () {
            touching = false;
        });
        canvas.addEventListener("pointerleave", function (event) {
            if (event.pointerType === "mouse") {
                hideInteraction();
            }
        });
        canvas.addEventListener("keydown", function (event) {
            if (!["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(event.key)) {
                return;
            }
            event.preventDefault();
            if (event.key === "Escape") {
                hideInteraction();
                return;
            }
            let index = Number.isInteger(chart.$stockRadarInteractionIndex)
                ? chart.$stockRadarInteractionIndex
                : points.length - 1;
            if (event.key === "ArrowLeft") {
                index = Math.max(0, index - 1);
            } else if (event.key === "ArrowRight") {
                index = Math.min(points.length - 1, index + 1);
            } else if (event.key === "Home") {
                index = 0;
            } else if (event.key === "End") {
                index = points.length - 1;
            }
            select(index, true);
        });

        return chart;
    }

    return {
        create: create,
        normalizePoints: normalizePoints,
        directionForPoints: directionForPoints,
        formatPrice: formatPrice,
        nearestIndex: nearestIndex
    };
}));
