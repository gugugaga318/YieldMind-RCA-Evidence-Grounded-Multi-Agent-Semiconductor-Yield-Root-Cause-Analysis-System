import { Download, FileText } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { RCAReport } from "../types";

interface ReportViewProps {
  report: RCAReport;
}

export function ReportView({ report }: ReportViewProps) {
  function downloadReport() {
    const blob = new Blob([report.markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${report.report_id}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="report-section" aria-labelledby="report-heading">
      <div className="report-toolbar">
        <div className="report-title-row">
          <FileText size={20} aria-hidden="true" />
          <div>
            <span className="section-kicker">Generated output</span>
            <h2 id="report-heading">{report.title}</h2>
          </div>
        </div>
        <button type="button" className="secondary-button" onClick={downloadReport}>
          <Download size={16} aria-hidden="true" />
          Download .md
        </button>
      </div>
      <article className="markdown-report">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{report.markdown}</ReactMarkdown>
      </article>
    </section>
  );
}
