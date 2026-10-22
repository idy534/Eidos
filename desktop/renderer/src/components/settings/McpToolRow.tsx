import React from "react";
import type { McpToolDetail } from "../../../../shared/mcp-details.js";

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function ExternalLinkIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <path d="M6 3.5h6.5v6.5M12.5 3.5l-7 7" />
    </svg>
  );
}

interface McpToolRowProps {
  tool: McpToolDetail;
  onOpenSchema: (tool: McpToolDetail) => void;
}

export function McpToolRow({ tool, onOpenSchema }: McpToolRowProps) {
  const properties = Object.entries(object(tool.inputSchema?.properties));
  const required = new Set(Array.isArray(tool.inputSchema?.required) ? tool.inputSchema.required : []);
  const hasParameters = tool.available !== false && properties.length > 0;

  const handleOpenSchema = (event: React.MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    onOpenSchema(tool);
  };

  const headerContent = (
    <div className="mcp-tool-row__content">
      <div className="mcp-tool-row__title-line">
        <strong className="mcp-tool-row__name">{tool.name}</strong>
        <button
          type="button"
          className="mcp-tool-row__schema-link"
          aria-label={`查看 ${tool.name} 原始 Schema`}
          onClick={handleOpenSchema}
        >
          <span>查看原始 Schema</span>
          <ExternalLinkIcon className="mcp-tool-row__schema-icon" />
        </button>
      </div>
      <p className="mcp-tool-row__description">{tool.description || "此工具未提供描述。"}</p>
    </div>
  );

  if (!hasParameters) {
    return (
      <div className="mcp-tool-row mcp-tool-row--flat">
        <div className="mcp-tool-row__summary">
          <span className="mcp-tool-row__chevron-placeholder" aria-hidden="true" />
          {headerContent}
        </div>
      </div>
    );
  }

  return (
    <details className="mcp-tool-row mcp-tool-row--expandable">
      <summary className="mcp-tool-row__summary">
        <span className="mcp-tool-row__chevron" aria-hidden="true">›</span>
        {headerContent}
      </summary>
      <div className="mcp-tool-row__body">
        <div className="mcp-tool-parameters" role="region" aria-label={`${tool.name} 参数`} tabIndex={0}>
          <table>
            <thead>
              <tr>
                <th>参数</th>
                <th>类型</th>
                <th>必填</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {properties.map(([name, value]) => {
                const schema = object(value);
                const type = typeof schema.type === "string" ? schema.type : Array.isArray(schema.type) ? schema.type.join(" | ") : "见 Schema";
                return (
                  <tr key={name}>
                    <td><code>{name}</code></td>
                    <td><code>{type}</code></td>
                    <td>{required.has(name) ? "是" : "否"}</td>
                    <td>{typeof schema.description === "string" ? schema.description : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </details>
  );
}
