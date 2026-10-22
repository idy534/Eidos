import { useCallback, useEffect, useRef, useState } from "react";
import type { McpServerRecord } from "../../contracts.js";
import type { McpToolDetail, McpToolsResponse } from "../../../../shared/mcp-details.js";

export function useMcpTools(server: McpServerRecord | null) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState({ identity: "", text: "" });
  const [tools, setTools] = useState<McpToolDetail[]>([]);
  const [page, setPage] = useState<McpToolsResponse>();
  const [description, setDescription] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const catalog = useRef<string | null>(null);
  const generation = useRef(0);
  const busy = useRef(false);
  const identity = server ? `${server.pluginId}:${server.serverId}:${server.pluginHash}:${server.updatedAt}:${server.consented}` : "";
  const searchText = search.identity === identity ? search.text : "";

  useEffect(() => {
    catalog.current = null;
    setQuery(""); setSearch({ identity, text: "" }); setPage(undefined); setTools([]);
    setDescription(null);
  }, [identity]);
  useEffect(() => {
    const timer = setTimeout(() => setSearch({ identity, text: query.trim() }), 250);
    return () => clearTimeout(timer);
  }, [query, identity]);

  const request = useCallback(async (cursor: string | null, append: boolean, token: number) => {
    if (!server || busy.current) return;
    busy.current = true; setLoading(true); setError(undefined);
    try {
      const result = await window.eidosRuntime.readMcpTools({
        pluginId: server.pluginId, serverId: server.serverId, query: searchText, cursor,
      });
      if (generation.current !== token) return;
      catalog.current = result.catalogId ?? null;
      setPage(result);
      if (result.state === "ready") setDescription(result.description ?? null);
      if (result.state === "error") {
        setError(`无法获取工具列表（${result.errorCode || "MCP_DISCOVERY_FAILED"}）。请重试。`);
      } else {
        setTools(previous => append ? [...previous, ...(result.tools ?? [])] : result.tools ?? []);
      }
    } catch (cause) {
      if (generation.current !== token) return;
      const expired = cause instanceof Error && cause.message.includes("INVALID_CURSOR");
      setError(expired ? "工具目录已过期或配置已变化，请重新加载。" : "无法获取工具列表，请重试。");
      if (expired) catalog.current = null;
    } finally {
      busy.current = false;
      if (generation.current === token) { setLoading(false); }
    }
  }, [identity, searchText]);

  useEffect(() => {
    const token = ++generation.current;
    busy.current = false;
    setTools([]); setPage(undefined); setError(undefined);
    if (server) void request(catalog.current ? `${catalog.current}:0` : null, false, token);
    else setLoading(false);
    return () => { generation.current++; };
  }, [request, revision]);

  const loadMore = useCallback(() => {
    if (!busy.current && !error && page?.nextCursor) void request(page.nextCursor, true, generation.current);
  }, [page?.nextCursor, error, request]);
  function reload() { catalog.current = null; setRevision(value => value + 1); }
  function retry() {
    if (catalog.current && page?.nextCursor && tools.length) void request(page.nextCursor, true, generation.current);
    else reload();
  }
  return { query, setQuery, tools, page, description, error, loading, loadMore, reload, retry };
}
