import { LineChart } from "echarts/charts";
import { GridComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { TrendingDown } from "lucide-react";
import { useEffect, useRef } from "react";

import type { YieldTrendPoint } from "../types";

echarts.use([LineChart, GridComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

interface YieldTrendChartProps {
  data: YieldTrendPoint[];
}

export function YieldTrendChart({ data }: YieldTrendChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = echarts.init(containerRef.current, undefined, { renderer: "canvas" });
    chart.setOption({
      animationDuration: 450,
      grid: { left: 45, right: 22, top: 30, bottom: 42 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#17201d",
        borderWidth: 0,
        textStyle: { color: "#ffffff", fontSize: 12 },
        formatter: (params: unknown) => {
          const point = Array.isArray(params) ? params[0] : null;
          if (!point || typeof point !== "object" || !("dataIndex" in point)) return "";
          const item = data[Number(point.dataIndex)];
          return [
            `<strong>${item.date}</strong>`,
            `WAT pass rate: ${item.pass_rate.toFixed(1)}%`,
            `Passed: ${item.pass_count} / ${item.lot_count} lots`,
          ].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: data.map((item) => item.date.slice(5)),
        axisLine: { lineStyle: { color: "#c8d0cc" } },
        axisTick: { show: false },
        axisLabel: { color: "#64706a", fontSize: 11, margin: 14 },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        interval: 25,
        axisLabel: { color: "#64706a", fontSize: 11, formatter: "{value}%" },
        splitLine: { lineStyle: { color: "#e8ecea", type: "dashed" } },
      },
      series: [
        {
          name: "WAT pass rate",
          type: "line",
          data: data.map((item) => item.pass_rate),
          smooth: false,
          symbol: "circle",
          symbolSize: 8,
          lineStyle: { color: "#087f73", width: 3 },
          itemStyle: { color: "#ffffff", borderColor: "#087f73", borderWidth: 3 },
          areaStyle: { color: "rgba(8, 127, 115, 0.10)" },
          markLine: {
            symbol: "none",
            label: { show: false },
            lineStyle: { color: "#d45b45", type: "dashed", width: 1 },
            data: [{ yAxis: 85 }],
          },
        },
      ],
    });

    const resizeObserver = new ResizeObserver(() => chart.resize());
    resizeObserver.observe(containerRef.current);
    return () => {
      resizeObserver.disconnect();
      chart.dispose();
    };
  }, [data]);

  return (
    <section className="yield-panel" aria-labelledby="yield-heading">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">WAT lot pass rate</span>
          <h2 id="yield-heading">Yield Trend</h2>
        </div>
        <TrendingDown size={20} aria-hidden="true" />
      </div>
      <div
        ref={containerRef}
        className="yield-chart"
        role="img"
        aria-label="Daily WAT lot pass rate trend"
      />
    </section>
  );
}
