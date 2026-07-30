import { LineChart } from "echarts/charts";
import { GridComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { Activity, ShieldAlert } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { RCAState, SpcChartResult, SpcOocContext } from "../types";

echarts.use([LineChart, GridComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

export function SpcEvidencePanel({ state }: { state: RCAState }) {
  const finding = state.findings.find((item) => item.agent === "fdc");
  const method = finding?.details.spc_method as Record<string, unknown> | undefined;
  const results = useMemo(
    () => (finding?.details.spc_results as SpcChartResult[] | undefined) ?? [],
    [finding],
  );
  const contexts =
    (finding?.details.spc_ooc_contexts as SpcOocContext[] | undefined) ?? [];
  const [selectedParameter, setSelectedParameter] = useState<string>(
    results[0]?.parameter_name ?? "",
  );

  useEffect(() => {
    if (!results.some((item) => item.parameter_name === selectedParameter)) {
      setSelectedParameter(results[0]?.parameter_name ?? "");
    }
  }, [results, selectedParameter]);

  if (method?.engine !== "deterministic_advanced_spc" || results.length === 0) {
    return null;
  }
  const selected =
    results.find((item) => item.parameter_name === selectedParameter) ?? results[0];

  return (
    <section className="spc-section" aria-labelledby="spc-heading">
      <div className="section-heading-row">
        <div>
          <span className="section-kicker">Deterministic analytics</span>
          <h2 id="spc-heading">SPC Evidence</h2>
        </div>
        <span className="section-count">Nelson Rules 1-8</span>
      </div>

      <div className="spc-tabs" role="tablist" aria-label="SPC parameters">
        {results.map((item) => (
          <button
            type="button"
            role="tab"
            aria-selected={item.parameter_name === selected.parameter_name}
            className={item.parameter_name === selected.parameter_name ? "active" : ""}
            onClick={() => setSelectedParameter(item.parameter_name)}
            key={item.parameter_name}
          >
            <span>{formatParameter(item.parameter_name)}</span>
            <strong className={item.status === "OOC" ? "spc-ooc" : "spc-control"}>
              {item.status}
            </strong>
          </button>
        ))}
      </div>

      <div className="spc-layout">
        <div className="spc-chart-panel">
          <div className="spc-chart-meta">
            <div>
              <span>Chart</span>
              <strong>{selected.chart_type}</strong>
            </div>
            <div>
              <span>Baseline</span>
              <strong>{selected.baseline_id}</strong>
            </div>
            <div>
              <span>Window</span>
              <strong>
                {selected.baseline_window.start.slice(0, 10)} to{" "}
                {selected.baseline_window.end.slice(0, 10)}
              </strong>
            </div>
            <div>
              <span>Capability</span>
              <strong>{capabilityLabel(selected)}</strong>
            </div>
          </div>
          <SpcChart result={selected} />
        </div>

        <div className="spc-context-panel">
          <RuleSummary result={selected} />
          {contexts.map((context) => (
            <ExcursionContext context={context} key={context.event_key} />
          ))}
        </div>
      </div>
    </section>
  );
}

function SpcChart({ result }: { result: SpcChartResult }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = echarts.init(containerRef.current, undefined, { renderer: "canvas" });
    const violatedSamples = new Set(
      result.violations.flatMap((violation) => violation.sample_ids),
    );
    chart.setOption({
      animationDuration: 350,
      grid: { left: 58, right: 24, top: 34, bottom: 58 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#17201d",
        borderWidth: 0,
        textStyle: { color: "#ffffff", fontSize: 11 },
        formatter: (params: unknown) => {
          const point = Array.isArray(params) ? params[0] : null;
          if (!point || typeof point !== "object" || !("dataIndex" in point)) return "";
          const sample = result.series[Number(point.dataIndex)];
          return [
            `<strong>${sample.lot_id}</strong>`,
            sample.wafer_id ? `Wafer: ${sample.wafer_id}` : "Lot subgroup",
            `Value: ${sample.value} ${result.unit}`,
            `Time: ${new Date(sample.timestamp).toLocaleString()}`,
          ].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: result.series.map((item) => item.wafer_id ?? item.lot_id),
        axisLine: { lineStyle: { color: "#c8d0cc" } },
        axisTick: { show: false },
        axisLabel: { color: "#64706a", fontSize: 9, hideOverlap: true, rotate: 35 },
      },
      yAxis: {
        type: "value",
        scale: true,
        name: result.unit,
        nameTextStyle: { color: "#64706a", fontSize: 10 },
        axisLabel: { color: "#64706a", fontSize: 10 },
        splitLine: { lineStyle: { color: "#e8ecea", type: "dashed" } },
      },
      series: [
        {
          type: "line",
          data: result.series.map((item) => ({
            value: item.value,
            itemStyle: {
              color: violatedSamples.has(item.sample_id) ? "#c64f3a" : "#ffffff",
              borderColor: violatedSamples.has(item.sample_id) ? "#c64f3a" : "#087f73",
              borderWidth: 2,
            },
          })),
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { color: "#087f73", width: 2 },
          markLine: {
            symbol: "none",
            label: { fontSize: 9, color: "#64706a" },
            data: controlLines(result),
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
  }, [result]);

  return (
    <div
      ref={containerRef}
      className="spc-chart"
      role="img"
      aria-label={`${result.parameter_name} ${result.chart_type} control chart`}
    />
  );
}

function controlLines(result: SpcChartResult) {
  const lines: Array<Record<string, unknown>> = [
    { name: "CL", yAxis: result.center_line, lineStyle: { color: "#52615a" } },
    { name: "UCL", yAxis: result.upper_control_limit, lineStyle: { color: "#c64f3a" } },
    { name: "LCL", yAxis: result.lower_control_limit, lineStyle: { color: "#c64f3a" } },
  ];
  const spec = result.capability;
  if (spec?.spec_upper !== null && spec?.spec_upper !== undefined) {
    lines.push({
      name: "USL",
      yAxis: spec.spec_upper,
      lineStyle: { color: "#a96c13", type: "dotted" },
    });
  }
  if (spec?.spec_lower !== null && spec?.spec_lower !== undefined) {
    lines.push({
      name: "LSL",
      yAxis: spec.spec_lower,
      lineStyle: { color: "#a96c13", type: "dotted" },
    });
  }
  return lines;
}

function RuleSummary({ result }: { result: SpcChartResult }) {
  const rules = Array.from(new Set(result.violations.map((item) => item.rule_code)));
  return (
    <div className="spc-rule-block">
      <div className="spc-subheading">
        <Activity size={16} aria-hidden="true" />
        <strong>Rule Violations</strong>
      </div>
      <div className="spc-rule-list">
        {rules.length > 0 ? (
          rules.map((rule) => <code key={rule}>{rule}</code>)
        ) : (
          <span>No violations</span>
        )}
      </div>
      <p>{result.point_violation_count} analysis samples participate in violations.</p>
    </div>
  );
}

function ExcursionContext({ context }: { context: SpcOocContext }) {
  return (
    <div className="spc-excursion-block">
      <div className="spc-subheading">
        <ShieldAlert size={16} aria-hidden="true" />
        <strong>Excursion Scope</strong>
      </div>
      <dl>
        <div>
          <dt>OOC</dt>
          <dd>{context.event_key}</dd>
        </div>
        <div>
          <dt>Trigger Lot</dt>
          <dd>{context.trigger_lot_id}</dd>
        </div>
        <div>
          <dt>Trigger Wafer</dt>
          <dd>{context.trigger_wafer_id || "Lot-level"}</dd>
        </div>
        <div>
          <dt>Trigger Hold</dt>
          <dd>{context.trigger_hold?.hold_id ?? "Missing"}</dd>
        </div>
      </dl>
      <span className="spc-impact-label">Impact Lots / Holds</span>
      <div className="spc-impact-list">
        {context.impact_scopes.map((scope) => (
          <div key={scope.lot_id}>
            <code>{scope.lot_id}</code>
            <span>{scope.hold_id}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatParameter(value: string) {
  return value.replaceAll("_", " ");
}

function capabilityLabel(result: SpcChartResult) {
  if (!result.capability) return "Not calculated";
  const status = result.capability.valid_for_decision ? "valid" : "informational";
  return `Cpk ${result.capability.cpk ?? "N/A"} / ${status}`;
}
